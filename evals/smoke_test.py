r"""端到端冒烟测试：mock 掉模型调用，验证编排/校验/渲染/落盘全链路。

用法：
    .venv\Scripts\python.exe evals\smoke_test.py
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import orchestrator as orch  # noqa: E402
from app.config import build_models  # noqa: E402
from app.schemas import (  # noqa: E402
    Claim,
    CounterExample,
    Decision,
    FileSelection,
    HostReport,
    CounterExampleVerdict,
    Judgment,
    SkepticReport,
    Speech,
)

CALLS: list[tuple[str, str]] = []
EVIDENCE_GOOD = "app/cli.py:1"
EVIDENCE_FAKE = "src/does_not_exist.py:42"  # 故意编造，验证会被剔除


def _role_of(system: str) -> str:
    match = re.search(r'"role":\s*"(\w+)"', system)
    return match.group(1) if match else "unknown"


async def fake_complete_json(spec, system, user, schema, **kwargs):  # noqa: ANN001
    role = _role_of(system)
    CALLS.append((spec.alias, schema.__name__))

    if schema is FileSelection:
        return FileSelection(files=["app/cli.py", "app/orchestrator.py"], reason="mock")

    if schema is SkepticReport:
        return SkepticReport(
            counterexamples=[
                CounterExample(
                    scenario="并发重复请求",
                    failure="状态被写两次",
                    evidence=[EVIDENCE_GOOD, EVIDENCE_FAKE],
                )
            ]
        )

    if schema is HostReport:
        return HostReport(
            converged=False,
            unresolved=["改动是否会影响现有调用方？"],
            summary="mock 未收敛",
        )

    if schema is Judgment:
        return Judgment(
            decisions=[
                Decision(
                    issue="改动是否影响现有调用方",
                    decision="采用向后兼容的新增参数",
                    reason="避免破坏现有调用",
                    overruled="skeptic",
                )
            ],
            final_approach="新增可选参数并保留旧路径",
            biggest_weakness="新增参数会让签名变长",
            reservations=["需要补充并发测试"],
            open_questions=[],
            adopted_from=["implementer"],
            counterexample_verdicts=[
                # 关键：scenario 故意写得与 skeptic 不同，只靠 id 对齐；
                # 若对齐逻辑退化成文本匹配，这条判定就会丢成 unknown
                CounterExampleVerdict(
                    id="CE-1",
                    scenario="judge 改写过的措辞，与 skeptic 原文不一致",
                    verdict="valid",
                    reason="mock 判定：确实会双写",
                ),
                CounterExampleVerdict(
                    id="CE-2",
                    scenario="一条不存在的编号，应被忽略",
                    verdict="invalid",
                    reason="mock：不应出现在结果里",
                ),
            ],
        )

    if schema is Speech:
        assert role in {"architect", "implementer", "tester"}, role
        return Speech(
            role=role,
            stance="propose",
            proposal=f"{role} 的方案\n    缩进行一\n    缩进行二",
            claims=[
                Claim(text=f"{role} 的结论", evidence=[EVIDENCE_GOOD, EVIDENCE_FAKE])
            ],
            risks=["mock 风险"],
            questions=["mock 问题"],
            confidence=0.6,
            request_context=["app/render.py"],
        )

    raise AssertionError(f"未预期的 schema: {schema}")


async def main() -> None:
    orch.complete_json = fake_complete_json

    models = build_models()
    markdown, session_dir = await orch.run_discussion(
        repo=ROOT,
        requirement="给讨论结果增加导出 HTML 的能力",
        models=models,
        max_rounds=3,
        out_dir=ROOT / "evals" / "out",
        use_scout=True,
    )

    plan = (session_dir / "plan.md").read_text(encoding="utf-8")
    transcript = (session_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    context = (session_dir / "context.md").read_text(encoding="utf-8")

    checks: list[tuple[str, bool]] = [
        ("plan.md 有结论段", "## 0. 结论" in plan),
        ("plan.md 有决策表", "## 1. 决策记录" in plan),
        ("plan.md 有改动清单", "## 3. 改动清单" in plan),
        ("plan.md 有反例判定", "## 6. 反例与判定" in plan),
        ("plan.md 含最终方案", "新增可选参数并保留旧路径" in plan),
        ("plan.md 含最大弱点", "签名变长" in plan),
        ("transcript = 4 角色 x 3 轮", len(transcript) == 12),
        ("context.md 非空", len(context) > 500),
        ("context 含 scout 选中的文件", "app/render.py" in context),
        ("编造的引用被剔除", "does_not_exist" not in plan),
        ("真实引用被保留", "app/cli.py:1" in plan),
        ("judgment.json 已落盘", (session_dir / "judgment.json").is_file()),
        ("反例判定已合入方案", "mock 判定：确实会双写" in plan),
        ("反例去重后只剩一行", plan.count("| 并发重复请求 |") == 1),
        ("风险已去重", plan.count("- mock 风险") == 1),
        ("反例按编号对齐（judge 改写措辞仍命中）", "mock 判定：确实会双写" in plan),
        ("反例判定为 valid 而非 unknown", "| valid |" in plan),
        ("越界编号 CE-2 未混入结果", "不应出现在结果里" not in plan),
        ("plan.html 已生成", (session_dir / "plan.html").is_file()),
        (
            "plan.html 是完整文档",
            (session_dir / "plan.html").read_text(encoding="utf-8").startswith("<!doctype html>"),
        ),
        (
            "plan.html 声明 utf-8",
            'charset="utf-8"' in (session_dir / "plan.html").read_text(encoding="utf-8"),
        ),
        (
            "plan.html 声明 freetext 保留换行",
            ".freetext { white-space: pre-wrap; }"
            in (session_dir / "plan.html").read_text(encoding="utf-8"),
        ),
        (
            "plan.html 保留多行缩进",
            "    缩进行一" in (session_dir / "plan.html").read_text(encoding="utf-8"),
        ),
        (
            "判定列挂上 verdict 样式类（CE-6）",
            'class="verdict-valid"' in (session_dir / "plan.html").read_text(encoding="utf-8"),
        ),
        (
            "plan.html 含结论与决策",
            "最终方案" in (session_dir / "plan.html").read_text(encoding="utf-8")
            and "决策记录" in (session_dir / "plan.html").read_text(encoding="utf-8"),
        ),
    ]

    # ---------------- 单元回归 ----------------
    from app.orchestrator import _write_text
    from app.render import _counterexample_rows
    from app.schemas import CounterExample, CounterExampleVerdict

    # CE-5：同一反例重复出现，第二轮才带上判定 -> 必须保留带判定的那条
    dup_history = [
        SkepticReport(counterexamples=[CounterExample(scenario="并发双写", failure="旧描述")]),
        SkepticReport(counterexamples=[CounterExample(scenario="并发双写", failure="新描述")]),
    ]
    dup_judgment = Judgment(
        counterexample_verdicts=[
            CounterExampleVerdict(id="CE-2", scenario="并发双写", verdict="valid", reason="成立")
        ]
    )
    dup_rows = _counterexample_rows(dup_history, dup_judgment)
    checks += [
        ("CE-5 重复反例去重为一行", len(dup_rows) == 1),
        ("CE-5 新判定未被丢弃", bool(dup_rows) and dup_rows[0]["verdict"] == "valid"),
        ("CE-5 保留带判定的那条", bool(dup_rows) and dup_rows[0]["failure"] == "新描述"),
    ]

    # CE-8：写盘失败不能抛异常中断整轮讨论
    try:
        _write_text(Path(__file__).resolve() / "impossible.txt", "x")
        isolated = True
    except Exception:  # noqa: BLE001
        isolated = False
    checks.append(("CE-8 写盘失败被隔离", isolated))

    # ---------------- 新增：历史预算 ----------------
    from app.prompts import render_history

    long_hist = [
        Speech(role="architect", proposal="A" * 500),
        Speech(role="implementer", proposal="B" * 500),
        Speech(role="tester", proposal="C" * 500),
    ]
    full = render_history(long_hist)
    capped = render_history(long_hist, budget_chars=700)
    checks += [
        ("历史预算：不传时保持完整", "A" * 100 in full and "C" * 100 in full),
        ("历史预算：超限时给出省略提示", "已省略最早的" in capped),
        ("历史预算：保留最近的发言", "C" * 100 in capped),
        ("历史预算：丢弃最早的发言", "A" * 100 not in capped),
        ("历史预算：0 视为不限制", "A" * 100 in render_history(long_hist, budget_chars=0)),
    ]

    # ---------------- 新增：断点续跑 ----------------
    resume_root = ROOT / "evals" / "out_resume"
    _md1, dir1 = await orch.run_discussion(
        repo=ROOT,
        requirement="续跑自检",
        models=models,
        max_rounds=1,
        out_dir=resume_root,
        use_scout=False,
    )
    t1 = (dir1 / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    st1 = json.loads((dir1 / "state.json").read_text(encoding="utf-8"))
    _md2, dir2 = await orch.run_discussion(
        repo=ROOT,
        requirement="",
        models=models,
        max_rounds=3,
        out_dir=resume_root,
        use_scout=False,
        resume_dir=dir1,
    )
    t2 = (dir2 / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    checks += [
        ("续跑：首轮后即落盘 state.json", (dir1 / "state.json").is_file()),
        ("续跑：首轮 transcript=4 条", len(t1) == 4),
        ("续跑：state 记录 rounds_done=1", st1.get("rounds_done") == 1),
        ("续跑：续跑后 transcript=12 条", len(t2) == 12),
        ("续跑：续跑到同一个会话目录", dir2 == dir1),
        ("续跑：requirement 以 state 为准", "续跑自检" in dir2.name),
        ("续跑：最终 plan.md 已生成", (dir2 / "plan.md").is_file()),
    ]

    # ---------------- 新增：旧会话（无 state.json）回填续跑 ----------------
    legacy_root = ROOT / "evals" / "out_legacy"
    shutil.rmtree(legacy_root, ignore_errors=True)
    _mdl, dirl = await orch.run_discussion(
        repo=ROOT,
        requirement="旧会话回填自检",
        models=models,
        max_rounds=2,
        out_dir=legacy_root,
        use_scout=False,
    )
    legacy_transcript = len((dirl / "transcript.jsonl").read_text(encoding="utf-8").splitlines())
    (dirl / "state.json").unlink()  # 模拟本次改动之前产生的旧目录
    _mdl2, dirt = await orch.run_discussion(
        repo=ROOT,
        requirement="",
        models=models,
        max_rounds=2,
        out_dir=legacy_root,
        use_scout=False,
        resume_dir=dirl,
    )
    legacy_after = len((dirt / "transcript.jsonl").read_text(encoding="utf-8").splitlines())
    checks += [
        ("旧会话：无 state.json 也能续跑", (dirt / "plan.md").is_file()),
        ("旧会话：回填后不重复跑已有轮次", legacy_after == legacy_transcript == 8),
        ("旧会话：续跑到同一个目录", dirt == dirl),
    ]
    shutil.rmtree(legacy_root, ignore_errors=True)

    # ---------------- 新增：方案瘦身（默认不重复纪要 / 只取最终立场） ----------------
    from app.render import render_markdown

    hist_rows = []
    for line in (session_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        hist_rows.append(SkepticReport(**row) if "counterexamples" in row else Speech(**row))
    judgment_obj = Judgment(**json.loads((session_dir / "judgment.json").read_text(encoding="utf-8")))
    md_full_minutes = render_markdown(
        requirement="x", repo=ROOT, history=hist_rows, judgment=judgment_obj,
        unresolved=[], include_minutes=True,
    )
    md_all_rounds = render_markdown(
        requirement="x", repo=ROOT, history=hist_rows, judgment=judgment_obj,
        unresolved=[], plan_scope="all",
    )
    checks += [
        ("方案瘦身：默认不重复输出讨论纪要", "skeptic 反例清单" not in plan),
        ("方案瘦身：指向 transcript.jsonl", "transcript.jsonl" in plan),
        ("方案瘦身：标注只取最终立场", "只保留最终立场" in plan),
        ("方案瘦身：--with-minutes 仍可带上纪要", "skeptic 反例清单" in md_full_minutes),
        ("方案瘦身：plan_scope=all 标注为全轮次", "包含所有轮次" in md_all_rounds),
        ("方案瘦身：默认方案比全量短", len(plan) < len(md_full_minutes)),
    ]

    width = max(len(name) for name, _ in checks)
    failed = 0
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name.ljust(width)}")
        failed += 0 if ok else 1

    print(f"\n模型调用次数: {len(CALLS)} -> {[a for a, _ in CALLS]}")
    print(f"会话目录: {session_dir}")
    print("\n全部通过" if failed == 0 else f"\n{failed} 项失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())