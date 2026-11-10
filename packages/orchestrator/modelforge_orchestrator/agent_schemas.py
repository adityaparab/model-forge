"""Structured outputs the orchestrator's LLM agents ask the gateway for."""

from typing import Literal

from modelforge_core.schemas import ModelingPlan, StrictModel
from pydantic import Field

from modelforge_orchestrator.schemas import FindingCategory


class ScopeProposal(StrictModel):
    plan: ModelingPlan
    rationale: str
    assumptions: list[str]
    open_questions: list[str]


class ScopeNotes(StrictModel):
    rationale: str
    assumptions: list[str]
    open_questions: list[str]
    passages: list[str]


class QueryPlan(StrictModel):
    queries: list[str] = Field(max_length=3)


class SectionDraft(StrictModel):
    body_md: str
    citations: list[str] = []


class ChallengeQueryPlan(StrictModel):
    queries: list[str] = Field(max_length=5)


class FindingDraft(StrictModel):
    """A finding as the challenger writes it; the node assigns its id, round and status."""

    severity: Literal["critical", "high", "medium", "low"]
    category: FindingCategory
    title: str
    description: str
    evidence: list[str]
    required_action: str


class ReviewDraft(StrictModel):
    findings: list[FindingDraft]


class Verdict(StrictModel):
    finding_id: str
    status: Literal["closed", "open", "disputed"]
    note: str


class ReReviewDraft(StrictModel):
    verdicts: list[Verdict]


class FindingResponseDraft(StrictModel):
    decision: Literal["accept", "dispute"]
    action: str
    evidence: list[str] = []
    sections_to_regenerate: list[str] = []
