"""代码上下文：目录树、文件列表、关键词检索、片段读取、引用校验。"""
from __future__ import annotations

import os
import re
from pathlib import Path

SKIP_DIRS = {
    # VCS / IDE / 编辑器缓存
    ".git", ".svn", ".hg", ".idea", ".vs", ".vscode",
    # AI 工具的工作目录（可能含海量会话文件）
    ".claude", ".agent-teams", ".cursor", ".aider", ".continue",
    # 依赖与构建产物
    "node_modules", "dist", "build", "out", "target", "coverage",
    ".next", ".nuxt", ".output", ".turbo", ".parcel-cache", ".cache",
    "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    # 备份 / 临时 / 第三方
    "backups", "backup", "oldassets", "old", "_tmp", "tmp", "temp",
    "vendor", "third_party", ".gradle",
    # Unreal Engine
    "Binaries", "Intermediate", "Saved", "DerivedDataCache", "Build",
    ".Rider", "Plugins",
}


def extra_skip_dirs() -> set[str]:
    """项目专属的额外跳过目录，逗号分隔，例如 AIDISCUSS_SKIP_DIRS=public,data。"""
    raw = os.environ.get("AIDISCUSS_SKIP_DIRS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}

TEXT_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".inl",
    ".go", ".rs", ".java", ".kt", ".swift", ".rb", ".php", ".lua",
    ".sql", ".proto", ".graphql", ".gql",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".rst", ".txt", ".xml", ".html", ".css", ".scss",
    ".sh", ".ps1", ".bat", ".gradle", ".cmake", ".uproject", ".uplugin",
}

def _env_int(key: str, default: int) -> int:
    """读取正整数环境变量；非法值回退默认。0 视为未设置。"""
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


MAX_FILE_BYTES = _env_int("AIDISCUSS_MAX_FILE_BYTES", 400_000)
# 每个文件读取的行数上界（大文件只读前 N 行）。调小省 token，但会丢内容。
MAX_SNIPPET_LINES = _env_int("AIDISCUSS_SNIPPET_LINES", 160)
# 材料包字符预算：会被塞进每一次模型调用，是最大的一块成本。
CONTEXT_BUDGET_CHARS = _env_int("AIDISCUSS_CONTEXT_BUDGET", 60_000)

EN_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when",
    "add", "new", "use", "using", "make", "some", "all",
}
# 中文里高频但无检索价值的字，用于过滤 n-gram
CH_STOP_CHARS = set("的了是在和与或也就都而及以对为从到把被让给会能要可请你他它们这那个一上中下内外前后我功能实现支持增加修改优化需要可以以及并且提供使用如果问题进行")


def iter_files(root: Path, max_files: int = 20000):
    """遍历仓库里的文本文件。

    用显式栈而不是 rglob：rglob 会先进入所有子目录再过滤，
    对含 node_modules/.claude 的大仓库是纯浪费。这里直接剪枝。
    """
    skip = SKIP_DIRS | extra_skip_dirs()
    count = 0
    stack = [root]
    while stack and count < max_files:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if entry.name not in skip:
                    stack.append(Path(entry.path))
                continue
            if Path(entry.name).suffix.lower() not in TEXT_EXTS:
                continue
            try:
                if entry.stat(follow_symlinks=False).st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            count += 1
            yield Path(entry.path)
            if count >= max_files:
                return


def list_text_files(root: Path, limit: int | None = None) -> list[str]:
    """列出仓库内的文本文件相对路径，供模型挑选。"""
    if limit is None:
        limit = int(os.environ.get("AIDISCUSS_SCOUT_FILES", "1500") or "1500")
    names: list[str] = []
    for path in iter_files(root):
        names.append(path.relative_to(root).as_posix())
        if len(names) >= limit:
            break
    return names


def repo_tree(root: Path, max_entries: int = 300, max_depth: int = 5) -> str:
    """生成精简目录树。"""
    lines: list[str] = []
    base_depth = len(root.parts)

    def walk(directory: Path, prefix: str = "") -> None:
        if len(lines) >= max_entries:
            return
        if len(directory.parts) - base_depth >= max_depth:
            return
        try:
            entries = sorted(
                directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except OSError:
            return
        shown = [e for e in entries if e.is_dir() and e.name not in SKIP_DIRS]
        shown += [e for e in entries if e.is_file()]
        for item in shown:
            if len(lines) >= max_entries:
                lines.append("... (目录树已截断)")
                return
            suffix = "/" if item.is_dir() else ""
            lines.append(f"{prefix}{item.name}{suffix}")
            if item.is_dir():
                walk(item, prefix + "  ")

    walk(root)
    return "\n".join(lines)


def _english_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)]


def _chinese_ngrams(text: str, n: int = 2) -> list[str]:
    """无词典的粗糙切词：整段短词 + 2-gram，过滤含停用字的组合。"""
    grams: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fa5]+", text):
        if 2 <= len(run) <= 5:
            grams.append(run)
        if len(run) > n:
            for i in range(len(run) - n + 1):
                grams.append(run[i : i + n])
    return [g for g in grams if not (set(g) & CH_STOP_CHARS)]


def guess_keywords(requirement: str, limit: int = 16) -> list[str]:
    """从需求里抽关键词（英文标识符优先，中文用 n-gram 兜底）。"""
    tokens = _english_tokens(requirement) + _chinese_ngrams(requirement)
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        key = token.lower()
        if key in EN_STOPWORDS or key in seen:
            continue
        seen.add(key)
        result.append(token)
    # 英文标识符信噪比更高，排前面
    result.sort(key=lambda t: 0 if re.match(r"^[A-Za-z_]", t) else 1)
    return result[:limit]


def grep(root: Path, keywords: list[str], max_hits: int = 60) -> list[str]:
    """在仓库里搜关键词，返回 'path:line: code' 列表。"""
    if not keywords:
        return []
    lowered = [k.lower() for k in keywords]
    hits: list[str] = []
    for path in iter_files(root):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > 300:
                continue
            low = line.lower()
            if any(k in low for k in lowered):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
                if len(hits) >= max_hits:
                    return hits
    return hits


def read_snippet(root: Path, rel_path: str, *, max_lines: int = MAX_SNIPPET_LINES) -> str:
    """读取文件片段并带行号。"""
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return f"# 拒绝访问仓库外的路径: {rel_path}"
    if not target.is_file():
        return f"# 文件不存在: {rel_path}"
    try:
        lines = target.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    except OSError as exc:
        return f"# 读取失败 {rel_path}: {exc}"
    shown = lines[:max_lines]
    numbered = "\n".join(f"{i:>5}| {t}" for i, t in enumerate(shown, start=1))
    if len(lines) > max_lines:
        numbered += f"\n... (共 {len(lines)} 行，已截断)"
    return f"--- {rel_path} ---\n{numbered}"


def existing_paths(root: Path, rel_paths: list[str], limit: int = 8) -> list[str]:
    """过滤出真实存在、且在仓库内的文件路径。"""
    good: list[str] = []
    for rel in rel_paths:
        rel = (rel or "").strip().strip("`").lstrip("./").replace("\\", "/")
        if not rel or rel in good:
            continue
        try:
            (root / rel).resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if (root / rel).is_file():
            good.append(rel)
        if len(good) >= limit:
            break
    return good


def build_context_pack(
    root: Path,
    requirement: str,
    *,
    priority_files: list[str] | None = None,
    budget_chars: int | None = None,
) -> str:
    """组装讨论用的材料包：目录树 + 检索命中 + 重点文件片段。

    budget_chars 为 None 时取 AIDISCUSS_CONTEXT_BUDGET（默认 60000）。
    """
    if budget_chars is None:
        budget_chars = CONTEXT_BUDGET_CHARS
    # 目录树与关键词命中同样占预算，按预算等比缩放；
    # 默认 60000 时折算结果与历史默认值（300 / 60）一致，行为不变。
    tree = repo_tree(root, max_entries=max(60, budget_chars // 200))
    keywords = guess_keywords(requirement)
    hits = grep(root, keywords, max_hits=max(20, budget_chars // 1000))

    ordered: list[str] = []
    for rel in priority_files or []:
        if rel not in ordered:
            ordered.append(rel)
    for hit in hits:
        rel = hit.split(":", 1)[0]
        if rel not in ordered:
            ordered.append(rel)

    parts: list[str] = [
        "# 仓库目录树（已裁剪）",
        tree,
        "",
        f"# 关键词命中（{', '.join(keywords) or '无'}）",
        "\n".join(hits) or "（无命中）",
        "",
        "# 重点文件内容",
    ]

    used = sum(len(p) for p in parts)
    for rel in ordered[:16]:
        snippet = read_snippet(root, rel)
        if used + len(snippet) > budget_chars:
            parts.append(f"... (达到上下文预算，省略 {rel} 及之后的文件)")
            break
        parts.append(snippet)
        used += len(snippet)

    return "\n".join(parts)


def validate_evidence(root: Path, evidence: list[str]) -> list[str]:
    """只保留真实存在的引用，用于抓模型编造的文件/行号。"""
    root_resolved = root.resolve()
    valid: list[str] = []
    for item in evidence:
        raw = (item or "").strip().strip("`").lstrip("./").replace("\\", "/")
        if not raw:
            continue
        path_part, _, location = raw.partition(":")
        path_part = path_part.strip()
        target = (root / path_part).resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError:
            continue
        if not target.is_file():
            continue
        if location.strip():
            digits = re.sub(r"\D", "", location)
            if digits:
                try:
                    total = len(
                        target.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
                    )
                except OSError:
                    continue
                if int(digits) > total:
                    continue
        valid.append(raw)
    return valid
