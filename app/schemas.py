"""讨论过程中所有结构化数据的定义。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Stance = Literal["propose", "agree", "disagree", "conditional"]


class Claim(BaseModel):
    text: str
    evidence: list[str] = Field(default_factory=list)
    unverified: bool = False


class Speech(BaseModel):
    """通用发言：architect / implementer / tester 使用。"""

    role: str
    stance: Stance = "conditional"
    proposal: str = ""
    claims: list[Claim] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    request_context: list[str] = Field(default_factory=list)


class CounterExample(BaseModel):
    scenario: str
    failure: str
    evidence: list[str] = Field(default_factory=list)
    verdict: Literal["valid", "invalid", "unknown"] = "unknown"
    reason: str = ""


class SkepticReport(BaseModel):
    role: str = "skeptic"
    counterexamples: list[CounterExample] = Field(default_factory=list)


class HostReport(BaseModel):
    converged: bool = False
    unresolved: list[str] = Field(default_factory=list)
    summary: str = ""


class Decision(BaseModel):
    issue: str
    decision: str
    reason: str
    overruled: str = ""


class CounterExampleVerdict(BaseModel):
    id: str = ""          # 对应发言里的反例编号（CE-1/CE-2），用于稳定对齐
    scenario: str = ""
    verdict: Literal["valid", "invalid", "unknown"] = "unknown"
    reason: str = ""


class Judgment(BaseModel):
    decisions: list[Decision] = Field(default_factory=list)
    final_approach: str = ""
    biggest_weakness: str = ""
    reservations: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    adopted_from: list[str] = Field(default_factory=list)
    counterexample_verdicts: list[CounterExampleVerdict] = Field(default_factory=list)

class FileSelection(BaseModel):
    """scout 阶段的产物：模型从文件列表里挑出的相关文件。"""

    files: list[str] = Field(default_factory=list)
    reason: str = ""