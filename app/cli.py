"""命令行入口。"""
from __future__ import annotations

import asyncio
import ctypes
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import build_models, build_roles, load_dotenv


def _setup_console() -> None:
    """Windows 控制台默认是 GBK，会把中文输出打乱，这里强制 UTF-8。"""
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


_setup_console()

app = typer.Typer(add_completion=False, help="多模型代码方案讨论器")
console = Console()

ROOT = Path(__file__).resolve().parent.parent


def _boot() -> dict:
    load_dotenv(ROOT / ".env")
    return build_models()


@app.command()
def discuss(
    requirement: str = typer.Option("", "--requirement", "-r", help="需求描述"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="从文件读取需求"),
    repo: Path = typer.Option(".", "--repo", help="待讨论的代码仓库路径"),
    rounds: int = typer.Option(3, "--rounds", min=1, max=6, help="最大讨论轮次"),
    out: Optional[Path] = typer.Option(None, "--out", help="输出目录（默认 sessions/）"),
    no_scout: bool = typer.Option(
        False, "--no-scout", help="跳过 LLM 选文件，只用关键词检索"
    ),
    resume: Optional[Path] = typer.Option(
        None, "--resume", help="从已有会话目录续跑（读它的 state.json，忽略 --requirement/--repo）"
    ),
    context_budget: Optional[int] = typer.Option(
        None, "--context-budget", help="材料包字符预算；默认取 AIDISCUSS_CONTEXT_BUDGET 或 60000"
    ),
    history_budget: Optional[int] = typer.Option(
        None, "--history-budget", help="交叉质询/主持人可见的历史字符预算；0=不限制"
    ),
    with_minutes: bool = typer.Option(
        False, "--with-minutes", help="把完整讨论纪要也写进 plan（默认不写，只留 transcript.jsonl）"
    ),
    plan_scope: str = typer.Option(
        "latest",
        "--plan-scope",
        help="方案第2~4节取哪些发言：latest=每个角色只取最终立场；all=所有轮次",
    ),
) -> None:
    """针对一个功能改动，组织多个模型讨论并产出落地方案。"""
    models = _boot()

    text = requirement
    if file:
        text = Path(file).read_text(encoding="utf-8-sig")
    if plan_scope not in ("all", "latest"):
        console.print(f"[red]--plan-scope 只能是 all 或 latest，收到: {plan_scope}[/red]")
        raise typer.Exit(code=2)

    if not text.strip() and resume is None:
        console.print("[red]必须提供 --requirement 或 --file（或 --resume）[/red]")
        raise typer.Exit(code=2)

    repo_path = Path(repo).resolve()
    if resume is None and not repo_path.is_dir():
        console.print(f"[red]仓库路径不存在: {repo_path}[/red]")
        raise typer.Exit(code=2)

    if resume is not None:
        resume = resume.resolve()
        has_state = (resume / "state.json").is_file()
        has_transcript = (resume / "transcript.jsonl").is_file()
        if not has_state and not has_transcript:
            console.print(
                f"[red]续跑目录里既没有 state.json 也没有 transcript.jsonl: {resume}[/red]"
            )
            raise typer.Exit(code=2)

    roles = build_roles()
    used = [models[alias] for alias in roles.values() if alias in models]
    unknown = [alias for alias in roles.values() if alias not in models]
    if unknown:
        console.print(f"[red]角色配置引用了未知模型: {', '.join(sorted(set(unknown)))}[/red]")
        raise typer.Exit(code=3)

    missing = [
        spec.api_key_env for spec in used if not spec.is_cli and not spec.api_key
    ]
    if missing:
        console.print(f"[red]缺少 API Key: {', '.join(missing)}[/red]")
        console.print("请复制 .env.example 为 .env 并填入密钥，")
        console.print("或在 .env 里把对应模型改成 CLI 传输（*_TRANSPORT=cli）。")
        raise typer.Exit(code=3)

    broken = [spec.alias for spec in used if spec.is_cli and not spec.cli_available()]
    if broken:
        console.print(f"[red]CLI 不可用: {', '.join(broken)}[/red]")
        console.print("运行 `python -m app.cli doctor` 查看详情。")
        raise typer.Exit(code=3)

    from .orchestrator import run_discussion

    markdown, session_dir = asyncio.run(
        run_discussion(
            repo=repo_path,
            requirement=text,
            models=models,
            max_rounds=rounds,
            out_dir=out,
            use_scout=not no_scout,
            resume_dir=resume,
            context_budget=context_budget,
            history_budget=history_budget,
            include_minutes=with_minutes,
            plan_scope=plan_scope,
        )
    )

    console.rule("[bold green]完成")
    console.print(f"方案: [cyan]{session_dir / 'plan.md'}[/cyan]")
    html_path = session_dir / "plan.html"
    if html_path.is_file():
        console.print(f"HTML: [cyan]{html_path}[/cyan]")
    else:
        console.print("[yellow]plan.html 未生成，详见上方警告[/yellow]")


@app.command()
def doctor() -> None:
    """检查模型端点（本地 CLI 或云端 API）是否可用。"""
    models = _boot()

    table = Table("别名", "传输", "端点", "状态")
    for spec in models.values():
        if spec.is_cli:
            ok = spec.cli_available()
            status = "[green]CLI 就绪[/green]" if ok else "[red]找不到命令[/red]"
        else:
            ok = bool(spec.api_key)
            status = "[green]Key 已设置[/green]" if ok else f"[red]缺少 {spec.api_key_env}[/red]"
        table.add_row(spec.alias, spec.transport, spec.describe()[:70], status)
    console.print(table)

    from .llm import complete, extract_json

    roles = build_roles()
    used = {alias for alias in roles.values()}
    console.print(f"[dim]实际会用到: {', '.join(sorted(used))}[/dim]")

    for alias, spec in models.items():
        if alias not in used:
            continue
        if spec.is_cli and not spec.cli_available():
            continue
        if not spec.is_cli and not spec.api_key:
            continue
        try:
            raw = asyncio.run(complete(spec, "你是测试助手，只输出 JSON。", '输出 {"ok": true}'))
            extract_json(raw)
            console.print(f"[green]OK  {spec.alias} 连通（{spec.transport}）[/green]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]FAIL {spec.alias}: {str(exc)[:200]}[/red]")


@app.command("models")
def models_cmd() -> None:
    """显示当前的模型与角色分配。"""
    models = _boot()
    roles = build_roles()
    table = Table("角色", "模型别名", "传输", "端点")
    for role, alias in roles.items():
        spec = models.get(alias)
        table.add_row(
            role,
            alias,
            spec.transport if spec else "-",
            (spec.describe()[:46] if spec else "未定义"),
        )
    console.print(table)


if __name__ == "__main__":
    app()