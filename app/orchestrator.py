"""讨论编排：选文件 -> 独立立场 -> 交叉质询 -> 收敛 -> 裁决。"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .config import ModelSpec, build_roles
from .context import (
    CONTEXT_BUDGET_CHARS,
    build_context_pack,
    existing_paths,
    list_text_files,
    read_snippet,
    validate_evidence,
)
from .llm import LLMError, complete_json
from .prompts import SYSTEM, build_user_prompt, render_history
from .schemas import FileSelection, HostReport, Judgment, SkepticReport, Speech

console = Console()

ROLE_ORDER = ["architect", "implementer", "skeptic", "tester"]

# 模型 -> 角色分配在 app/config.py 的 build_roles() 里，可用 .env 覆盖

SCHEMA_FOR: dict[str, type] = {
    "architect": Speech,
    "implementer": Speech,
    "skeptic": SkepticReport,
    "tester": Speech,
}

FOCUS = {
    "architect": "给出你的架构方案、接口与数据变更。",
    "implementer": "给出逐文件的改动清单与兼容性评估。",
    "skeptic": "只列出会让这个改动失败的边界条件，不要给方案。",
    "tester": "列出必须覆盖的测试用例与通过标准。",
}

CRITIQUE_FOCUS = (
    "阅读上面的已有发言，针对其他人的结论做以下事情：\n"
    "1. 如果你有新的证据，更新或补充你的结论\n"
    "2. 如果你不同意某人，明确指出不同意哪一条、为什么\n"
    "3. 如果你同意某人，不要重复他，只补充他没覆盖的部分\n"
    "4. 回答别人提出的、与你职责相关的问题\n"
    "禁止只表达赞同而不给出任何新信息。"
)

STATE_FILE = "state.json"


def _env_budget(key: str, default: int) -> int:
    """读取预算类环境变量：0 表示不限制，非法值回退默认。"""
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


# 交叉质询/主持人看到的历史上限（字符）；0 = 不限制。
# judge 永远看完整历史，因为它的产物就是最终方案。
HISTORY_BUDGET_CHARS = _env_budget("AIDISCUSS_HISTORY_BUDGET", 40_000)
# 角色申请补充文件时的字符上限。
EXTRA_CONTEXT_BUDGET_CHARS = _env_budget("AIDISCUSS_EXTRA_CONTEXT_BUDGET", 20_000)


class Usage:
    """累计发送给模型的字符数，让成本可见。"""

    def __init__(self) -> None:
        self.calls = 0
        self.system_chars = 0
        self.user_chars = 0

    def add(self, system: str, user: str) -> None:
        self.calls += 1
        self.system_chars += len(system or "")
        self.user_chars += len(user or "")

    @property
    def total_chars(self) -> int:
        return self.system_chars + self.user_chars

    def summary(self) -> str:
        return (
            f"{self.calls} 次调用，输入合计 {self.total_chars:,} 字符"
            f"（system {self.system_chars:,} + user {self.user_chars:,}）"
        )


def _speech_report(item: object) -> str:
    if isinstance(item, Speech):
        return (
            f"{item.role}: stance={item.stance} "
            f"claims={len(item.claims)} risks={len(item.risks)} q={len(item.questions)}"
        )
    if isinstance(item, SkepticReport):
        return f"skeptic: counterexamples={len(item.counterexamples)}"
    return repr(item)


def _validate_speech(repo: Path, speech: Speech) -> Speech:
    for claim in speech.claims:
        valid = validate_evidence(repo, claim.evidence)
        claim.evidence = valid
        claim.unverified = not valid
    return speech


def _validate_skeptic(repo: Path, report: SkepticReport) -> SkepticReport:
    for counterexample in report.counterexamples:
        counterexample.evidence = validate_evidence(repo, counterexample.evidence)
    return report


async def _scout(
    spec: ModelSpec, requirement: str, files: list[str], usage: Usage
) -> list[str]:
    """让便宜模型从文件列表里挑相关文件；失败则返回空，由 grep 兜底。"""
    if not files:
        return []
    listing = "\n".join(files)
    user = f"# 需求\n{requirement}\n\n# 候选文件列表\n{listing}"
    try:
        usage.add(SYSTEM["scout"], user)
        selection = await complete_json(spec, SYSTEM["scout"], user, FileSelection)
    except (LLMError, ValueError) as exc:
        console.print(f"  [yellow]scout 失败({exc})，改用关键词兜底[/yellow]")
        return []
    return selection.files


async def _ask(
    spec: ModelSpec, role: str, user_prompt: str, usage: Usage
) -> Speech | SkepticReport:
    console.print(f"  [dim] {role}  {spec.alias}:{spec.model}[/dim]")
    usage.add(SYSTEM[role], user_prompt)
    return await complete_json(spec, SYSTEM[role], user_prompt, SCHEMA_FOR[role])


async def _ask_safe(
    spec: ModelSpec, role: str, user_prompt: str, usage: Usage
) -> Speech | SkepticReport | None:
    try:
        return await _ask(spec, role, user_prompt, usage)
    except (LLMError, ValueError) as exc:
        console.print(f"  [red]x {role} 失败: {exc}[/red]")
        return None


def _collect_requests(history: list) -> list[str]:
    requested: list[str] = []
    for item in history:
        if isinstance(item, Speech):
            for rel in item.request_context:
                if rel not in requested:
                    requested.append(rel)
    return requested


def _read_extra_context(
    repo: Path, rels: list[str], budget: int | None = None
) -> str:
    """读取角色申请补充的文件，受字符预算约束。"""
    if budget is None:
        budget = EXTRA_CONTEXT_BUDGET_CHARS
    parts: list[str] = []
    used = 0
    for rel in rels:
        snippet = read_snippet(repo, rel)
        if budget and used + len(snippet) > budget:
            parts.append(
                f"...（达到补充上下文预算 {budget} 字符，省略 {rel} 及之后的文件）"
            )
            break
        parts.append(snippet)
        used += len(snippet)
    return "\n\n".join(parts)


def _dump_history(history: list) -> list[dict]:
    rows: list[dict] = []
    for item in history:
        if isinstance(item, SkepticReport):
            rows.append({"kind": "skeptic", "data": item.model_dump()})
        else:
            rows.append({"kind": "speech", "data": item.model_dump()})
    return rows


def _load_history(rows: list[dict]) -> list:
    out: list = []
    for row in rows or []:
        if isinstance(row, (Speech, SkepticReport)):
            out.append(row)
            continue
        data = row.get("data") or {}
        if row.get("kind") == "skeptic":
            out.append(SkepticReport(**data))
        else:
            out.append(Speech(**data))
    return out


def _load_legacy_state(session_dir: Path) -> dict:
    """旧会话目录没有 state.json 时，从已有产物回填状态。

    只够续到裁决阶段：未解决分歧清单在旧目录里没有存，标记为 unknown，
    由调用方补跑一次主持人来归纳。
    """
    meta_path = session_dir / "meta.json"
    meta = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    history: list = []
    for raw in (session_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        row = json.loads(raw)
        if "counterexamples" in row:
            history.append(SkepticReport(**row))
        else:
            history.append(Speech(**row))
    rounds_done = len(history) // len(ROLE_ORDER)
    return {
        "requirement": meta.get("requirement", ""),
        "repo": meta.get("repo", "."),
        "history": _dump_history(history),
        "rounds_done": rounds_done,
        "unresolved": [],
        "unresolved_known": False,
        "extra_context": "",
        "priority_files": [],
        "max_rounds": rounds_done,
    }


def _write_text(path: Path, text: str) -> None:
    """单个产物写盘失败只警告，不中断整轮讨论（半成品目录好过全丢）。"""
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        console.print(f"  [yellow]写入 {path.name} 失败: {exc}[/yellow]")


def _write_transcript(session_dir: Path, history: list) -> None:
    try:
        with (session_dir / "transcript.jsonl").open("w", encoding="utf-8") as fh:
            for item in history:
                fh.write(item.model_dump_json() + "\n")
    except OSError as exc:
        console.print(f"  [yellow]写入 transcript.jsonl 失败: {exc}[/yellow]")


def _checkpoint(
    session_dir: Path,
    *,
    requirement: str,
    repo: Path,
    max_rounds: int,
    rounds_done: int,
    history: list,
    extra_context: str,
    unresolved: list[str],
    priority_files: list[str],
) -> None:
    """每轮结束落盘一次，让中断的讨论可以续跑，而不是整轮重来。"""
    state = {
        "requirement": requirement,
        "repo": str(repo),
        "max_rounds": max_rounds,
        "rounds_done": rounds_done,
        "unresolved": unresolved,
        "unresolved_known": True,
        "extra_context": extra_context,
        "priority_files": priority_files,
        "history": _dump_history(history),
    }
    _write_text(
        session_dir / STATE_FILE,
        json.dumps(state, ensure_ascii=False, indent=2),
    )
    _write_transcript(session_dir, history)


def _new_session_dir(requirement: str, out_dir: Path | None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = out_dir or (Path(__file__).resolve().parent.parent / "sessions")
    session_dir = base / f"{stamp}_{_slug(requirement)}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


async def run_discussion(
    *,
    repo: Path,
    requirement: str,
    models: dict[str, ModelSpec],
    max_rounds: int = 3,
    out_dir: Path | None = None,
    use_scout: bool = True,
    resume_dir: Path | None = None,
    context_budget: int | None = None,
    history_budget: int | None = None,
    include_minutes: bool = False,
    plan_scope: str = "latest",
    usage: Usage | None = None,
) -> tuple[str, Path]:
    """执行一次完整讨论，返回 (markdown, 会话目录)。

    resume_dir 非空时从该目录的 state.json 续跑；此时 requirement / repo
    以 state.json 记录的为准（CLI 传入值被忽略）。
    """
    roles = build_roles()
    usage = usage or Usage()
    if not context_budget or context_budget <= 0:
        context_budget = CONTEXT_BUDGET_CHARS
    if history_budget is None:
        history_budget = HISTORY_BUDGET_CHARS
    history_budget = history_budget or None  # 0 -> 不限制

    history: list[Speech | SkepticReport] = []
    extra_context = ""
    unresolved: list[str] = []
    unresolved_known = True
    rounds_done = 0
    priority_files: list[str] = []

    # ---------- 准备上下文 / 续跑 ----------
    if resume_dir is not None:
        session_dir = Path(resume_dir)
        state_path = session_dir / STATE_FILE
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        elif (session_dir / "transcript.jsonl").is_file():
            state = _load_legacy_state(session_dir)
            console.print(
                "  [yellow]未找到 state.json，已从 transcript.jsonl 回填"
                "（只能续到裁决阶段）[/yellow]"
            )
        else:
            raise FileNotFoundError(
                f"续跑失败：{session_dir} 里既没有 {STATE_FILE} 也没有 transcript.jsonl"
            )
        requirement = state["requirement"]
        repo = Path(state["repo"])
        context_pack = (session_dir / "context.md").read_text(encoding="utf-8")
        extra_context = state.get("extra_context", "")
        unresolved = state.get("unresolved", [])
        unresolved_known = bool(state.get("unresolved_known", True))
        rounds_done = int(state.get("rounds_done", 0))
        priority_files = state.get("priority_files", [])
        history = _load_history(state.get("history", []))
        max_rounds = max(max_rounds, int(state.get("max_rounds", max_rounds)))
        console.rule(f"[bold cyan]续跑 {session_dir.name}")
        console.print(
            f"  已完成 {rounds_done} 轮，历史 {len(history)} 条，目标 {max_rounds} 轮"
        )
    else:
        console.rule("[bold cyan]1 准备上下文")
        if use_scout:
            files = list_text_files(repo)
            console.print(f"  候选文件 {len(files)} 个，正在挑选")
            picked = await _scout(models[roles['scout']], requirement, files, usage)
            priority_files = existing_paths(repo, picked, limit=8)
            if priority_files:
                console.print(f"  [yellow]选中: {', '.join(priority_files)}[/yellow]")

        context_pack = build_context_pack(
            repo, requirement, priority_files=priority_files, budget_chars=context_budget
        )
        console.print(f"  材料包 {len(context_pack):,} 字符")

        session_dir = _new_session_dir(requirement, out_dir)
        _write_text(session_dir / "context.md", context_pack)
        console.print(f"  会话目录 {session_dir}")

    _checkpoint(
        session_dir,
        requirement=requirement,
        repo=repo,
        max_rounds=max_rounds,
        rounds_done=rounds_done,
        history=history,
        extra_context=extra_context,
        unresolved=unresolved,
        priority_files=priority_files,
    )

    # ---------- 第 1 轮：独立立场（互相不可见） ----------
    if rounds_done < 1:
        console.rule("[bold cyan]2 第 1 轮  独立立场")
        tasks = [
            _ask_safe(
                models[roles[role]],
                role,
                build_user_prompt(
                    requirement=requirement, context_pack=context_pack, focus=FOCUS[role]
                ),
                usage,
            )
            for role in ROLE_ORDER
        ]
        for role, result in zip(ROLE_ORDER, await asyncio.gather(*tasks)):
            if result is None:
                continue
            if isinstance(result, Speech):
                result.role = role
                _validate_speech(repo, result)
            else:
                _validate_skeptic(repo, result)
            history.append(result)
            console.print(f"  [green]ok[/green] {_speech_report(result)}")

        # 满足角色申请的文件（只做一次，避免无限膨胀）
        requested = existing_paths(repo, _collect_requests(history), limit=5)
        if requested:
            extra_context = _read_extra_context(repo, requested)
            console.print(f"  [yellow]补充上下文: {', '.join(requested)}[/yellow]")

        rounds_done = 1
        _checkpoint(
            session_dir,
            requirement=requirement,
            repo=repo,
            max_rounds=max_rounds,
            rounds_done=rounds_done,
            history=history,
            extra_context=extra_context,
            unresolved=unresolved,
            priority_files=priority_files,
        )

    # ---------- 交叉质询 ----------
    for round_no in range(rounds_done + 1, max_rounds + 1):
        console.rule(f"[bold cyan]3 第 {round_no} 轮  交叉质询")
        history_text = render_history(history, budget_chars=history_budget)
        tasks = [
            _ask_safe(
                models[roles[role]],
                role,
                build_user_prompt(
                    requirement=requirement,
                    context_pack=context_pack,
                    history=history_text,
                    extra_context=extra_context or None,
                    focus=CRITIQUE_FOCUS + "\n\n本轮职责重点：" + FOCUS[role],
                ),
                usage,
            )
            for role in ROLE_ORDER
        ]
        for role, result in zip(ROLE_ORDER, await asyncio.gather(*tasks)):
            if result is None:
                continue
            if isinstance(result, Speech):
                result.role = role
                _validate_speech(repo, result)
            else:
                _validate_skeptic(repo, result)
            history.append(result)
            console.print(f"  [green]ok[/green] {_speech_report(result)}")

        report = await _host(
            models[roles['host']],
            requirement,
            context_pack,
            render_history(history, budget_chars=history_budget),
            extra_context or None,
            usage,
        )
        unresolved = report.unresolved
        console.print(
            f"  [magenta]主持人: converged={report.converged} "
            f"未解决={len(unresolved)}[/magenta]"
        )

        rounds_done = round_no
        _checkpoint(
            session_dir,
            requirement=requirement,
            repo=repo,
            max_rounds=max_rounds,
            rounds_done=rounds_done,
            history=history,
            extra_context=extra_context,
            unresolved=unresolved,
            priority_files=priority_files,
        )

        if report.converged or not unresolved:
            console.print("  [green]已收敛，提前结束讨论[/green]")
            break

    # ---------- 裁决 ----------
    console.rule("[bold cyan]4 裁决")
    if not unresolved_known and rounds_done > 0:
        console.print("  [yellow]旧会话缺未解决分歧清单，先跑一次主持人归纳[/yellow]")
        report = await _host(
            models[roles['host']],
            requirement,
            context_pack,
            render_history(history, budget_chars=history_budget),
            extra_context or None,
            usage,
        )
        unresolved = report.unresolved
        console.print(
            f"  [magenta]主持人: converged={report.converged} "
            f"未解决={len(unresolved)}[/magenta]"
        )
    # judge 用完整历史：截断它等于牺牲最终产物质量。
    judge_user = build_user_prompt(
        requirement=requirement,
        context_pack=context_pack,
        history=render_history(history),
        extra_context=extra_context or None,
        focus=(
            "以下是仍未解决的分歧清单，请逐条给出决策：\n"
            + ("\n".join(f"- {item}" for item in unresolved) if unresolved else "（无，讨论已收敛）")
            + "\n\n同时判断 skeptic 反例成立与否。"
        ),
    )
    try:
        usage.add(SYSTEM["judge"], judge_user)
        judgment = await complete_json(
            models[roles['judge']], SYSTEM["judge"], judge_user, Judgment
        )
    except (LLMError, ValueError) as exc:
        console.print(f"  [red]x 裁决失败: {exc}[/red]")
        console.print(
            f"  [yellow]进度已保存，可用 --resume \"{session_dir}\" 重试裁决[/yellow]"
        )
        raise

    from .render import render_html, render_markdown  # 延迟导入避免循环依赖

    render_args = {
        "requirement": requirement,
        "repo": repo,
        "history": history,
        "judgment": judgment,
        "unresolved": unresolved,
        "roles": roles,
        "include_minutes": include_minutes,
        "plan_scope": plan_scope,
    }
    markdown = render_markdown(**render_args)

    # HTML 是附加产物：渲染失败不能让整轮讨论白跑
    try:
        html_doc = render_html(**render_args)
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [yellow]HTML 渲染失败，跳过 plan.html: {exc}[/yellow]")
        html_doc = ""

    _persist(
        session_dir,
        requirement=requirement,
        repo=repo,
        context_pack=context_pack,
        history=history,
        judgment=judgment,
        markdown=markdown,
        html=html_doc,
    )
    console.print(f"  [dim]成本：{usage.summary()}[/dim]")
    return markdown, session_dir


async def _host(
    spec: ModelSpec,
    requirement: str,
    context_pack: str,
    history_text: str,
    extra_context: str | None,
    usage: Usage,
) -> HostReport:
    user = build_user_prompt(
        requirement=requirement,
        context_pack=context_pack,
        history=history_text,
        extra_context=extra_context,
        focus="判断讨论是否可以收敛，并列出仍未解决的分歧。",
    )
    try:
        usage.add(SYSTEM["host"], user)
        return await complete_json(spec, SYSTEM["host"], user, HostReport)
    except (LLMError, ValueError) as exc:
        console.print(f"  [yellow]主持人调用失败({exc})，按未收敛处理[/yellow]")
        return HostReport(
            converged=False,
            unresolved=["主持人不可用，请人工确认是否收敛"],
            summary="",
        )


def _slug(text: str, limit: int = 24) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", text).strip("-")
    return cleaned[:limit] or "session"


def _persist(
    session_dir: Path,
    *,
    requirement: str,
    repo: Path,
    context_pack: str,
    history: list,
    judgment: Judgment,
    markdown: str,
    html: str = "",
) -> Path:
    session_dir.mkdir(parents=True, exist_ok=True)
    _write_text(session_dir / "context.md", context_pack)
    _write_text(session_dir / "plan.md", markdown)
    _write_text(session_dir / "judgment.json", judgment.model_dump_json(indent=2))
    if html:
        _write_text(session_dir / "plan.html", html)
    _write_transcript(session_dir, history)
    _write_text(
        session_dir / "meta.json",
        json.dumps(
            {"requirement": requirement, "repo": str(repo)},
            ensure_ascii=False,
            indent=2,
        ),
    )
    return session_dir
