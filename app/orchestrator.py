"""讨论编排：选文件 -> 独立立场 -> 交叉质询 -> 收敛 -> 裁决。"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .config import ModelSpec, build_roles
from .context import (
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


async def _scout(spec: ModelSpec, requirement: str, files: list[str]) -> list[str]:
    """让便宜模型从文件列表里挑相关文件；失败则返回空，由 grep 兜底。"""
    if not files:
        return []
    listing = "\n".join(files)
    try:
        selection = await complete_json(
            spec,
            SYSTEM["scout"],
            f"# 需求\n{requirement}\n\n# 候选文件列表\n{listing}",
            FileSelection,
        )
    except (LLMError, ValueError) as exc:
        console.print(f"  [yellow]scout 失败({exc})，改用关键词兜底[/yellow]")
        return []
    return selection.files


async def _ask(spec: ModelSpec, role: str, user_prompt: str) -> Speech | SkepticReport:
    console.print(f"  [dim] {role}  {spec.alias}:{spec.model}[/dim]")
    return await complete_json(spec, SYSTEM[role], user_prompt, SCHEMA_FOR[role])


async def _ask_safe(
    spec: ModelSpec, role: str, user_prompt: str
) -> Speech | SkepticReport | None:
    try:
        return await _ask(spec, role, user_prompt)
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


async def run_discussion(
    *,
    repo: Path,
    requirement: str,
    models: dict[str, ModelSpec],
    max_rounds: int = 3,
    out_dir: Path | None = None,
    use_scout: bool = True,
) -> tuple[str, Path]:
    """执行一次完整讨论，返回 (markdown, 会话目录)。"""
    roles = build_roles()
    console.rule("[bold cyan]1 准备上下文")

    priority_files: list[str] = []
    if use_scout:
        files = list_text_files(repo)
        console.print(f"  候选文件 {len(files)} 个，正在挑选")
        picked = await _scout(models[roles['scout']], requirement, files)
        priority_files = existing_paths(repo, picked, limit=8)
        if priority_files:
            console.print(f"  [yellow]选中: {', '.join(priority_files)}[/yellow]")

    context_pack = build_context_pack(repo, requirement, priority_files=priority_files)
    console.print(f"  材料包 {len(context_pack):,} 字符")

    history: list[Speech | SkepticReport] = []
    extra_context = ""

    # ---------- 第 1 轮：独立立场（互相不可见） ----------
    console.rule("[bold cyan]2 第 1 轮  独立立场")
    tasks = [
        _ask_safe(
            models[roles[role]],
            role,
            build_user_prompt(
                requirement=requirement, context_pack=context_pack, focus=FOCUS[role]
            ),
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
        extra_context = "\n\n".join(read_snippet(repo, rel) for rel in requested)
        console.print(f"  [yellow]补充上下文: {', '.join(requested)}[/yellow]")

    # ---------- 交叉质询 ----------
    unresolved: list[str] = []
    for round_no in range(2, max_rounds + 1):
        console.rule(f"[bold cyan]3 第 {round_no} 轮  交叉质询")
        history_text = render_history(history)
        tasks = [
            _ask_safe(
                models[roles[role]],
                role,
                build_user_prompt(
                    requirement=requirement,
                    context_pack=context_pack,
                    history=history_text,
                    extra_context=extra_context,
                    focus=CRITIQUE_FOCUS + "\n\n本轮职责重点：" + FOCUS[role],
                ),
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
            models[roles['host']], requirement, context_pack, render_history(history)
        )
        unresolved = report.unresolved
        console.print(
            f"  [magenta]主持人: converged={report.converged} "
            f"未解决={len(unresolved)}[/magenta]"
        )
        if report.converged or not unresolved:
            console.print("  [green]已收敛，提前结束讨论[/green]")
            break

    # ---------- 裁决 ----------
    console.rule("[bold cyan]4 裁决")
    judge_user = build_user_prompt(
        requirement=requirement,
        context_pack=context_pack,
        history=render_history(history),
        extra_context=extra_context,
        focus=(
            "以下是仍未解决的分歧清单，请逐条给出决策：\n"
            + ("\n".join(f"- {item}" for item in unresolved) if unresolved else "（无，讨论已收敛）")
            + "\n\n同时判断 skeptic 反例成立与否。"
        ),
    )
    try:
        judgment = await complete_json(
            models[roles['judge']], SYSTEM["judge"], judge_user, Judgment
        )
    except (LLMError, ValueError) as exc:
        console.print(f"  [red]x 裁决失败: {exc}[/red]")
        raise

    from .render import render_html, render_markdown  # 延迟导入避免循环依赖

    render_args = {
        "requirement": requirement,
        "repo": repo,
        "history": history,
        "judgment": judgment,
        "unresolved": unresolved,
        "roles": roles,
    }
    markdown = render_markdown(**render_args)

    # HTML 是附加产物：渲染失败不能让整轮讨论白跑
    try:
        html_doc = render_html(**render_args)
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [yellow]HTML 渲染失败，跳过 plan.html: {exc}[/yellow]")
        html_doc = ""

    session_dir = _persist(
        requirement, repo, context_pack, history, judgment, markdown, out_dir, html=html_doc
    )
    return markdown, session_dir


async def _host(
    spec: ModelSpec, requirement: str, context_pack: str, history_text: str
) -> HostReport:
    try:
        return await complete_json(
            spec,
            SYSTEM["host"],
            build_user_prompt(
                requirement=requirement,
                context_pack=context_pack,
                history=history_text,
                focus="判断讨论是否可以收敛，并列出仍未解决的分歧。",
            ),
            HostReport,
        )
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


def _write_text(path: Path, text: str) -> None:
    """单个产物写盘失败只警告，不中断整轮讨论（半成品目录好过全丢）。"""
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        console.print(f"  [yellow]写入 {path.name} 失败: {exc}[/yellow]")


def _persist(
    requirement: str,
    repo: Path,
    context_pack: str,
    history: list,
    judgment: Judgment,
    markdown: str,
    out_dir: Path | None,
    *,
    html: str = "",
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = out_dir or (Path(__file__).resolve().parent.parent / "sessions")
    session_dir = base / f"{stamp}_{_slug(requirement)}"
    session_dir.mkdir(parents=True, exist_ok=True)

    _write_text(session_dir / "context.md", context_pack)
    _write_text(session_dir / "plan.md", markdown)
    _write_text(session_dir / "judgment.json", judgment.model_dump_json(indent=2))
    if html:
        _write_text(session_dir / "plan.html", html)

    try:
        with (session_dir / "transcript.jsonl").open("w", encoding="utf-8") as fh:
            for item in history:
                fh.write(item.model_dump_json() + "\n")
    except OSError as exc:
        console.print(f"  [yellow]写入 transcript.jsonl 失败: {exc}[/yellow]")

    _write_text(
        session_dir / "meta.json",
        json.dumps(
            {"requirement": requirement, "repo": str(repo)},
            ensure_ascii=False,
            indent=2,
        ),
    )
    return session_dir
