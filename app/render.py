"""把讨论结果渲染成方案：Markdown（评审用）与 HTML（分享用）。"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .prompts import index_counterexamples, render_history
from .schemas import Judgment, SkepticReport, Speech

ROLE_LABEL = {
    "architect": "架构师",
    "implementer": "实现者",
    "skeptic": "反方",
    "tester": "测试",
    "judge": "裁决",
}

EMPTY = "（无）"


# ------------------------------------------------------------------ 纯数据层
# 下面这些函数只做数据处理，不产生任何标记语言，Markdown 与 HTML 渲染器共用。


def _title(requirement: str, limit: int = 60) -> str:
    """从需求里取一行做标题，去掉 Markdown 记号。"""
    for line in requirement.splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned[:limit] + ("" if len(cleaned) > limit else "")
    return "未命名需求"


def _roles_line(roles: dict[str, str] | None) -> str:
    if not roles:
        return "（未记录）"
    parts = [f"{ROLE_LABEL.get(r, r)}({roles[r]})" for r in ROLE_LABEL if roles.get(r)]
    return "  ".join(parts)


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = (item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _speeches(history: list, role: str) -> list[Speech]:
    return [h for h in history if isinstance(h, Speech) and h.role == role]


def _latest_per_role(history: list) -> list:
    """每个角色只保留**最后一次**发言（最终立场），其余条目原样保留。

    交叉质询里同一个角色每轮都会重述并细化立场，前三轮全量堆进方案会明显臃肿。
    只取最后一次不需要知道轮次边界，模型调用失败导致某轮缺条目也不会错位。
    """
    latest: dict[str, int] = {}
    for index, item in enumerate(history):
        if isinstance(item, Speech):
            latest[item.role] = index
    return [
        item
        for index, item in enumerate(history)
        if not isinstance(item, Speech) or latest.get(item.role) == index
    ]


def _claim_rows(speeches: list[Speech]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for speech in speeches:
        for claim in speech.claims:
            key = claim.text.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "text": key,
                    "evidence": ", ".join(claim.evidence),
                    "unverified": claim.unverified,
                }
            )
    return rows


def _proposal_list(speeches: list[Speech]) -> list[str]:
    return _dedupe(s.proposal for s in speeches)


def _normalize(text: str) -> str:
    """归一化场景文本用于跨轮次去重（只影响分组，不改变展示内容）。"""
    return " ".join((text or "").split()).lower()


def _counterexample_rows(history: list, judgment: Judgment) -> list[dict[str, str]]:
    """反例去重 + 与 judge 判定对齐。

    对齐**优先按 CE 编号**：实测 judge 会用自己的话复述场景，纯文本匹配会全部失配，
    导致 valid/invalid 退化成 unknown。场景文本匹配只作为旧数据（无 id）的兜底。

    去重按**归一化后的场景文本**分组，且当后续轮次带着判定出现时，
    用它替换掉先前只有 unknown 的同组记录  否则「更新的反例被丢弃」。
    """
    by_id = {
        v.id.strip(): v
        for v in judgment.counterexample_verdicts
        if v.id and v.id.strip()
    }
    # 只要有任意一条判定带了编号，就说明是「按编号对齐」的新数据；
    # 此时**禁用文本兜底**，否则同一个 scenario 文本会把判定误套给其它编号的记录。
    # 文本兜底只服务于没有编号的历史数据。
    by_scenario = (
        {}
        if by_id
        else {
            v.scenario.strip(): v
            for v in judgment.counterexample_verdicts
            if v.scenario and v.scenario.strip()
        }
    )

    groups: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for entry in index_counterexamples(history):
        key = _normalize(entry["scenario"])
        if not key:
            continue
        matched = by_id.get(entry["id"]) or by_scenario.get(entry["scenario"].strip())
        record: dict[str, object] = {
            "id": entry["id"],
            "scenario": entry["scenario"],
            "failure": entry["failure"],
            "evidence": ", ".join(entry["evidence"]),
            "verdict": matched.verdict if matched else entry["verdict"],
            "reason": (matched.reason if matched and matched.reason else "") or "",
            "resolved": matched is not None,
        }
        existing = groups.get(key)
        if existing is None:
            groups[key] = record
            order.append(key)
        elif record["resolved"] and not existing["resolved"]:
            groups[key] = record

    rows: list[dict[str, str]] = []
    for key in order:
        record = dict(groups[key])
        record.pop("resolved", None)
        rows.append(record)  # type: ignore[arg-type]
    return rows


def _collect(history: list) -> tuple[list[str], list[str]]:
    risks: list[str] = []
    questions: list[str] = []
    for speech in history:
        if isinstance(speech, Speech):
            risks += speech.risks
            questions += speech.questions
    return risks, questions


# ------------------------------------------------------------------ Markdown


def _bullets(items: Iterable[str], empty: str = EMPTY) -> str:
    values = _dedupe(items)
    return "\n".join(f"- {v}" for v in values) if values else empty


def _claims_block(speeches: list[Speech]) -> str:
    lines = []
    for row in _claim_rows(speeches):
        tag = " 未验证" if row["unverified"] else ""
        ev = f"  `{row['evidence']}`" if row["evidence"] else ""
        lines.append(f"- {row['text']}{ev}{tag}")
    return "\n".join(lines) if lines else EMPTY


def _proposals(speeches: list[Speech]) -> str:
    values = _proposal_list(speeches)
    return "\n\n".join(values) if values else EMPTY


def render_markdown(
    *,
    requirement: str,
    repo: Path,
    history: list,
    judgment: Judgment,
    unresolved: list[str],
    roles: dict[str, str] | None = None,
    include_minutes: bool = False,
    plan_scope: str = "latest",
) -> str:
    scoped = history if plan_scope == "all" else _latest_per_role(list(history))
    architect = _speeches(scoped, "architect")
    implementer = _speeches(scoped, "implementer")
    tester = _speeches(scoped, "tester")
    risks, questions = _collect(scoped)

    rows = _counterexample_rows(history, judgment)
    ce_lines = []
    for row in rows:
        evidence = f"`{row['evidence']}`" if row["evidence"] else ""
        ce_lines.append(
            f"| {row['scenario']} | {row['failure']} | {evidence} | "
            f"{row['verdict']} | {row['reason']} |"
        )
    counterexamples = "\n".join(ce_lines) or "|  |  |  |  |  |"

    decisions = "\n".join(
        f"| {d.issue} | {d.decision} | {d.reason} | {d.overruled or ''} |"
        for d in judgment.decisions
    ) or "|  |  |  |  |"

    scope_note = (
        "> 第 2~4 节的发言按「每个角色只保留最终立场」呈现；逐轮完整记录见 `transcript.jsonl`。"
        if plan_scope != "all"
        else "> 第 2~4 节包含所有轮次的发言；逐轮机器可读记录见 `transcript.jsonl`。"
    )

    if include_minutes:
        minutes_block = f"## 附录 B：讨论纪要\n\n{render_history(list(history))}"
    else:
        minutes_block = (
            "## 附录 B：讨论纪要\n\n"
            "（默认不重复输出：完整发言见同目录 `transcript.jsonl`；"
            "需要写进方案时加 `--with-minutes`）"
        )

    return f"""# 代码方案：{_title(requirement)}

> 仓库：`{repo}`
> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 参与角色：{_roles_line(roles)}

## 0. 结论

**最终方案：** {judgment.final_approach or '（未给出）'}

**最大弱点：** {judgment.biggest_weakness or '（未指出）'}

**主要采纳：** {'、'.join(judgment.adopted_from) if judgment.adopted_from else '（未标注）'}

## 1. 决策记录

| 分歧点 | 决策 | 理由 | 被否决方 |
| --- | --- | --- | --- |
{decisions}

## 2. 架构方案

{_proposals(architect)}

## 3. 改动清单

{_claims_block(implementer)}

## 4. 测试与验证

{_claims_block(tester)}

## 5. 风险

{_bullets(risks)}

## 6. 反例与判定

| 场景 | 失败结果 | 依据 | 判定 | 理由 |
| --- | --- | --- | --- | --- |
{counterexamples}

## 7. 保留意见与未决问题

**保留意见**

{_bullets(judgment.reservations)}

**未决问题（需人工确认）**

{_bullets(list(unresolved) + list(judgment.open_questions))}

{scope_note}

## 附录 A：讨论中提出的问题

{_bullets(questions)}

{minutes_block}

---
*由 AIDiscuss 多模型讨论生成，仅供参考，落地前请人工评审。*
"""


# ---------------------------------------------------------------------- HTML

_HTML_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1rem; line-height: 1.65;
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", Roboto, sans-serif;
  background: #f6f7f9; color: #1f2328;
}
main { max-width: 980px; margin: 0 auto; background: #fff; padding: 2rem 2.4rem 3rem;
  border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.09); }
h1 { font-size: 1.7rem; margin: 0 0 .6rem; line-height: 1.35; }
h2 { font-size: 1.15rem; margin: 2.2rem 0 .8rem; padding-bottom: .4rem;
  border-bottom: 1px solid #e3e6ea; }
h3 { font-size: .95rem; margin: 1.3rem 0 .5rem; color: #57606a; }
.meta { color: #6a737d; font-size: .85rem; margin-bottom: 1.6rem; }
.meta code { background: #f0f2f4; padding: .1rem .35rem; border-radius: 4px; }
.callout { background: #f0f6ff; border-left: 3px solid #3b82f6; padding: .9rem 1.1rem;
  border-radius: 0 6px 6px 0; margin: .8rem 0; }
.callout.warn { background: #fff8ec; border-left-color: #f59e0b; }
.callout strong { display: block; margin-bottom: .25rem; }
table { width: 100%; border-collapse: collapse; margin: .9rem 0; font-size: .9rem; }
th, td { border: 1px solid #e3e6ea; padding: .5rem .7rem; text-align: left;
  vertical-align: top; word-break: break-word; }
th { background: #f6f7f9; font-weight: 600; white-space: nowrap; }
ul { padding-left: 1.2rem; margin: .6rem 0; }
li { margin: .28rem 0; }
code { font-family: "Cascadia Mono", Consolas, monospace; font-size: .87em;
  background: #f0f2f4; padding: .1rem .35rem; border-radius: 4px; }
.empty { color: #8b949e; font-style: italic; }
/* 方案/风险等自由文本里可能有多行代码或缩进，浏览器默认会折叠空白 */
.freetext { white-space: pre-wrap; }
.verdict-valid { color: #b91c1c; font-weight: 600; }
.verdict-invalid { color: #15803d; font-weight: 600; }
details { margin-top: 1rem; }
summary { cursor: pointer; color: #57606a; font-size: .9rem; }
pre { white-space: pre-wrap; word-break: break-word; background: #f6f7f9;
  border: 1px solid #e3e6ea; border-radius: 6px; padding: 1rem; font-size: .82rem;
  max-height: 640px; overflow: auto; }
footer { margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid #e3e6ea;
  color: #8b949e; font-size: .8rem; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #e6edf3; }
  main { background: #161b22; box-shadow: none; }
  th, td { border-color: #30363d; }
  th { background: #1c2128; }
  h2 { border-color: #30363d; }
  code { background: #1c2128; }
  pre { background: #0d1117; border-color: #30363d; }
  .meta code { background: #1c2128; }
  .callout { background: #0f1d33; }
  .callout.warn { background: #2b2013; }
  footer { border-color: #30363d; }
}
"""


def _esc(value: object) -> str:
    """所有进入 HTML 的动态文本都必须过这里。"""
    return html.escape(str(value), quote=True)


class _Raw(str):
    """已自行转义、可直接注入的 HTML 片段（用 _cell 区分）。"""


def _cell(value: object) -> str:
    return str(value) if isinstance(value, _Raw) else _esc(value)


def _html_bullets(items: Iterable[str], empty: str = EMPTY) -> str:
    values = _dedupe(items)
    if not values:
        return f'<p class="empty">{_esc(empty)}</p>'
    return "<ul>" + "".join(f'<li class="freetext">{_esc(v)}</li>' for v in values) + "</ul>"


def _html_claims(speeches: list[Speech]) -> str:
    rows = _claim_rows(speeches)
    if not rows:
        return f'<p class="empty">{_esc(EMPTY)}</p>'
    parts = []
    for row in rows:
        ev = f" <code>{_esc(row['evidence'])}</code>" if row["evidence"] else ""
        tag = ' <span class="verdict-valid">未验证</span>' if row["unverified"] else ""
        parts.append(f'<li class="freetext">{_esc(row["text"])}{ev}{tag}</li>')
    return "<ul>" + "".join(parts) + "</ul>"


def _html_proposals(speeches: list[Speech]) -> str:
    values = _proposal_list(speeches)
    if not values:
        return f'<p class="empty">{_esc(EMPTY)}</p>'
    return "".join(f'<p class="freetext">{_esc(v)}</p>' for v in values)


def _html_table(headers: list[str], rows: list[list[str]], empty: str = EMPTY) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    if not rows:
        body = f'<tr><td class="empty" colspan="{len(headers)}">{_esc(empty)}</td></tr>'
    else:
        body = "".join(
            "<tr>" + "".join(f"<td>{_cell(cell)}</td>" for cell in row) + "</tr>"
            for row in rows
        )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html(
    *,
    requirement: str,
    repo: Path,
    history: list,
    judgment: Judgment,
    unresolved: list[str],
    roles: dict[str, str] | None = None,
    include_minutes: bool = False,
    plan_scope: str = "latest",
) -> str:
    """生成单文件 HTML：样式内联、可离线打开、与 Markdown 版同数据源。"""
    scoped = history if plan_scope == "all" else _latest_per_role(list(history))
    architect = _speeches(scoped, "architect")
    implementer = _speeches(scoped, "implementer")
    tester = _speeches(scoped, "tester")
    risks, questions = _collect(scoped)

    title = _title(requirement)

    decision_rows = [
        [d.issue, d.decision, d.reason, d.overruled or ""] for d in judgment.decisions
    ]

    verdict_class = {"valid": "verdict-valid", "invalid": "verdict-invalid"}
    ce_rows: list[list[object]] = []
    for row in _counterexample_rows(history, judgment):
        css = verdict_class.get(row["verdict"])
        verdict_cell: object = (
            _Raw(f'<span class="{css}">{_esc(row["verdict"])}</span>')
            if css
            else row["verdict"]
        )
        ce_rows.append(
            [
                row["scenario"],
                row["failure"],
                row["evidence"] or "",
                verdict_cell,
                row["reason"] or "",
            ]
        )

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = [
        f"<h1>{_esc(title)}</h1>",
        '<div class="meta">'
        f"仓库 <code>{_esc(repo)}</code>  "
        f"生成于 {_esc(generated)}  "
        f"参与角色 {_esc(_roles_line(roles))}"
        "</div>",
        "<h2>0. 结论</h2>",
        f'<div class="callout"><strong>最终方案</strong>{_esc(judgment.final_approach or "（未给出）")}</div>',
        f'<div class="callout warn"><strong>最大弱点</strong>{_esc(judgment.biggest_weakness or "（未指出）")}</div>',
        "<h3>主要采纳</h3>",
        _html_bullets(judgment.adopted_from, "（未标注）"),
        "<h2>1. 决策记录</h2>",
        _html_table(["分歧点", "决策", "理由", "被否决方"], decision_rows),
        "<h2>2. 架构方案</h2>",
        _html_proposals(architect),
        "<h2>3. 改动清单</h2>",
        _html_claims(implementer),
        "<h2>4. 测试与验证</h2>",
        _html_claims(tester),
        "<h2>5. 风险</h2>",
        _html_bullets(risks),
        "<h2>6. 反例与判定</h2>",
        _html_table(["场景", "失败结果", "依据", "判定", "理由"], ce_rows),
        "<h2>7. 保留意见与未决问题</h2>",
        "<h3>保留意见</h3>",
        _html_bullets(judgment.reservations),
        "<h3>未决问题（需人工确认）</h3>",
        _html_bullets(list(unresolved) + list(judgment.open_questions)),
        "<h2>附录 A：讨论中提出的问题</h2>",
        _html_bullets(questions),
        "<h2>附录 B：讨论纪要</h2>",
        (
            "<details><summary>展开完整讨论纪要</summary><pre>"
            + _esc(render_history(list(history)))
            + "</pre></details>"
            if include_minutes
            else "<p>（默认不重复输出：完整发言见同目录 <code>transcript.jsonl</code>）</p>"
        ),
        "<footer>由 AIDiscuss 多模型讨论生成，仅供参考，落地前请人工评审。</footer>",
    ]

    # 用普通字符串拼接而不是 f-string：CSS 里的花括号会被 f-string 当占位符
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n"
        "<style>"
        + _HTML_STYLE
        + "</style>\n</head>\n<body>\n<main>\n"
        + "\n".join(body)
        + "\n</main>\n</body>\n</html>\n"
    )