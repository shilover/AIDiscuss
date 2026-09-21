"""配置：.env 加载 + 模型注册表（支持 api / cli 两种传输方式）。"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """极简 .env 加载器：不覆盖已存在的环境变量。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


@dataclass(frozen=True)
class ModelSpec:
    """一个可调用的模型端点（本地 CLI 或云端 API）。"""

    alias: str
    transport: str = "api"           # api | cli
    provider: str = ""               # anthropic | openai_compat | gemini
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096

    # ---- cli transport ----
    cli_command: tuple[str, ...] = ()
    cli_prompt_mode: str = "stdin"   # stdin | arg | file
    cli_prompt_flag: str = ""        # 例如 -p；空则位置传参
    cli_system_flag: str = ""        # 例如 --append-system-prompt
    cli_output_mode: str = "text"    # text | json_result
    cli_result_field: str = "result"  # json_result 模式下取哪个字段
    cli_model_flag: str = ""          # 例如 --model
    cli_model: str = ""               # 传给 cli_model_flag 的值
    cli_max_prompt_chars: int = 0    # 0 表示不限，仅 arg 模式有效
    cli_file_task: str = (
        "Read the file {path} and follow its instructions exactly. "
        "Output only what it asks for, with no extra commentary."
    )
    cli_timeout: int = 900
    cli_extra: tuple[str, ...] = field(default_factory=tuple)

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""

    @property
    def is_cli(self) -> bool:
        return self.transport == "cli"

    def cli_available(self) -> bool:
        """检查 CLI 可执行文件是否在 PATH 上。"""
        if not self.is_cli or not self.cli_command:
            return False
        return shutil.which(self.cli_command[0]) is not None

    def describe(self) -> str:
        if self.is_cli:
            return " ".join(self.cli_command)
        return f"{self.provider}:{self.model}"


def _claude_cli() -> ModelSpec:
    return ModelSpec(
        alias="claude",
        transport="cli",
        cli_command=("claude", "-p", "--output-format", "json"),
        cli_prompt_mode="stdin",
        cli_system_flag="--append-system-prompt",
        cli_output_mode="json_result",
        cli_timeout=900,
    )


def resolve_cli(name: str) -> str:
    """在 PATH 与几个已知安装位置里找可执行文件。"""
    found = shutil.which(name)
    if found:
        return found
    home = Path.home()
    candidates = [
        home / ".gemini" / "bin" / f"{name}.exe",
        home / ".gemini" / "bin" / name,
        home / ".local" / "bin" / f"{name}.exe",
        home / ".local" / "bin" / name,
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return name


def npm_node_entry(cmd_name: str, package: str, rel: str) -> list[str] | None:
    """npm 全局安装的 .cmd 包装在 Windows 上不可靠，改走 node 入口脚本。"""
    exe = shutil.which(cmd_name)
    if not exe:
        return None
    script = Path(exe).parent / "node_modules" / package / rel
    if not script.is_file():
        return None
    node = shutil.which("node") or "node"
    return [node, str(script)]


def _deepseek_cli() -> ModelSpec:
    # dsh 不读 stdin，且命令行长度受限；改为把 prompt 写文件让它自己读
    base = npm_node_entry("dsh", "@deepseek-ai/dsh", "lib/bin.js") or ["dsh"]
    return ModelSpec(
        alias="deepseek",
        transport="cli",
        cli_command=tuple(base) + ("--profile", "headless"),
        cli_prompt_mode="file",
        cli_system_flag="",
        cli_output_mode="text",
        cli_timeout=900,
    )


def _claude_api() -> ModelSpec:
    return ModelSpec(
        alias="claude",
        transport="api",
        provider="anthropic",
        model=_env("CLAUDE_MODEL", "claude-sonnet-4-5"),
        base_url="https://api.anthropic.com/v1/messages",
        api_key_env="ANTHROPIC_API_KEY",
        max_tokens=4096,
    )


def _deepseek_api() -> ModelSpec:
    return ModelSpec(
        alias="deepseek",
        transport="api",
        provider="openai_compat",
        model=_env("DEEPSEEK_MODEL", "deepseek-chat"),
        base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1/chat/completions"),
        api_key_env="DEEPSEEK_API_KEY",
        temperature=0.2,
        max_tokens=4096,
    )


def _gemini_api() -> ModelSpec:
    return ModelSpec(
        alias="gemini",
        transport="api",
        provider="gemini",
        model=_env("GEMINI_MODEL", "gemini-2.5-flash"),
        base_url="https://generativelanguage.googleapis.com/v1beta/models",
        api_key_env="GEMINI_API_KEY",
        temperature=0.4,
        max_tokens=8192,
    )


def _agy_spec(alias: str, default_model: str, model_env: str) -> ModelSpec:
    """agy = Antigravity CLI，一个 harness 里同时提供 Gemini / Claude / GPT。

    - 不读 stdin，所以 prompt 走临时文件
    - --mode plan 为只读模式，配 --dangerously-skip-permissions 才能在
      headless 下读取该文件（plan 模式本身不允许写文件）
    - --output-format json 的正文在 response 字段
    """
    return ModelSpec(
        alias=alias,
        transport="cli",
        cli_command=(
            resolve_cli("agy"),
            "--mode", "plan",
            "--dangerously-skip-permissions",
            "--output-format", "json",
        ),
        cli_prompt_mode="file",
        cli_prompt_flag="-p",
        cli_system_flag="",
        cli_output_mode="json_result",
        cli_result_field="response",
        cli_model_flag="--model",
        cli_model=_env(model_env, default_model),
        cli_timeout=900,
    )


def _gemini_cli() -> ModelSpec:
    return _agy_spec("gemini", "gemini-3.8-flash-high", "GEMINI_MODEL")


def _sonnet_cli() -> ModelSpec:
    return _agy_spec("sonnet", "claude-sonnet-4-6", "SONNET_MODEL")


def _opus_cli() -> ModelSpec:
    return _agy_spec("opus", "claude-opus-4-6-thinking", "OPUS_MODEL")


def _gpt_cli() -> ModelSpec:
    return _agy_spec("gpt", "gpt-oss-120b-medium", "GPT_MODEL")


_AVAILABLE = {
    ("claude", "cli"): _claude_cli,
    ("claude", "api"): _claude_api,
    ("deepseek", "cli"): _deepseek_cli,
    ("deepseek", "api"): _deepseek_api,
    ("gemini", "cli"): _gemini_cli,
    ("sonnet", "cli"): _sonnet_cli,
    ("opus", "cli"): _opus_cli,
    ("gpt", "cli"): _gpt_cli,
    ("gemini", "api"): _gemini_api,
}

# 默认传输方式：本机已装 claude 与 dsh，gemini 未装 CLI
_DEFAULT_TRANSPORT = {
    "claude": "cli",
    "deepseek": "cli",
    "gemini": "cli",
    "sonnet": "cli",
    "opus": "cli",
    "gpt": "cli",
}


def build_models() -> dict[str, ModelSpec]:
    """必须在 load_dotenv 之后调用。"""
    models: dict[str, ModelSpec] = {}
    for alias, default in _DEFAULT_TRANSPORT.items():
        transport = _env(f"{alias.upper()}_TRANSPORT", default).lower()
        factory = _AVAILABLE.get((alias, transport))
        if factory is None:
            factory = _AVAILABLE[(alias, default)]
        models[alias] = factory()
    return models

# 角色 -> 模型别名，可用 ROLE_<角色>=<别名> 覆盖
_DEFAULT_ROLES = {
    "scout": "deepseek",
    "architect": "claude",
    "implementer": "deepseek",
    "skeptic": "gemini",
    "tester": "deepseek",
    "host": "deepseek",
    "judge": "claude",
}


def build_roles() -> dict[str, str]:
    """必须在 load_dotenv 之后调用。"""
    return {name: _env(f"ROLE_{name.upper()}", alias) for name, alias in _DEFAULT_ROLES.items()}
