"""Structured outputs the orchestrator's LLM agents ask the gateway for."""

from modelforge_core.schemas import ModelingPlan, StrictModel
from pydantic import Field


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
