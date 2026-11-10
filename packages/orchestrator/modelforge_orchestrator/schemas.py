"""LangGraph run state, manifest, replay report and findings models for the orchestrator."""

import operator
from typing import Annotated, Any, Literal

from modelforge_core.schemas import ModelingPlan, StrictModel
from modelforge_lineage.models import ArtifactRef, GateDecision, GateName, RunStatus
from modelforge_mcp.schemas import DatasetRef
from pydantic import Field

RefName = Literal[
    "dataset",
    "dictionary",
    "profile",
    "leakage",
    "psi",
    "split",
    "binning",
    "selection",
    "champion",
    "challenger_model",
    "calibration",
    "champion_scores",
    "challenger_scores",
    "discrimination",
    "calibration_tests",
    "stability",
    "sensitivity",
    "benchmark",
    "validation",
    "document_sections",
    "scope_notes",
    "document_md",
    "document_html",
    "completeness",
    "findings",
    "responses",
    "manifest",
    "replay_report",
    "knowledge_index",
]

DETERMINISTIC_REFS: tuple[RefName, ...] = (
    "dataset",
    "dictionary",
    "profile",
    "leakage",
    "psi",
    "split",
    "binning",
    "selection",
    "champion",
    "challenger_model",
    "calibration",
    "champion_scores",
    "challenger_scores",
    "discrimination",
    "calibration_tests",
    "stability",
    "sensitivity",
    "benchmark",
    "validation",
    "document_md",
    "document_html",
    "completeness",
    "knowledge_index",
)
"""The refs a replay compares with its original (spec 5.7)."""


def merge_dicts[K, V](a: dict[K, V], b: dict[K, V]) -> dict[K, V]:
    """Shallow merge for state reducers; keys in ``b`` win."""
    return {**a, **b}


class Counters(StrictModel):
    build_loops: int = 0
    remediation_rounds: int = 0
    challenge_rounds: int = 0


class ErrorRecord(StrictModel):
    node: str
    attempt: int
    kind: Literal["tool", "llm", "validation", "internal"]
    error: dict[str, Any]
    ts: str


class GatePrompt(StrictModel):
    gate: GateName
    run_id: str
    summary: dict[str, Any]


class RunState(StrictModel):
    run_id: str
    requester: str
    profile: str = "default"
    seed: int
    brief_hash: str
    brief_ref: str
    dataset_ref: DatasetRef
    replay_of: str | None = None
    rerun_llm: bool = False
    plan: ModelingPlan | None = None
    refs: Annotated[dict[RefName, ArtifactRef], merge_dicts] = Field(default_factory=dict)
    gates: Annotated[dict[GateName, GateDecision], merge_dicts] = Field(default_factory=dict)
    counters: Counters = Field(default_factory=Counters)
    max_remediation_rounds: int = 2
    """The brief's ``remediation_rounds`` bound, copied by `intake`."""
    cost_usd: float = 0.0
    errors: Annotated[list[ErrorRecord], operator.add] = Field(default_factory=list)
    status: RunStatus = "running"


def apply_update(state: RunState, update: dict[str, Any]) -> RunState:
    """``state`` after a node's ``update``, with the reducers LangGraph applies to `RunState`.

    ``refs`` and ``gates`` merge (keys in the update win), ``errors`` concatenate, and every
    other key is replaced.
    """
    merged = dict(update)
    if "refs" in update:
        merged["refs"] = merge_dicts(state.refs, update["refs"])
    if "gates" in update:
        merged["gates"] = merge_dicts(state.gates, update["gates"])
    if "errors" in update:
        merged["errors"] = state.errors + list(update["errors"])
    return state.model_copy(update=merged)


class ApprovalSummary(StrictModel):
    """No timestamp: approval times live in the approvals table and `gate.decided` events."""

    gate: GateName
    decision: str
    approver: str


class Manifest(StrictModel):
    run_id: str
    replay_of: str | None
    brief_hash: str
    dataset_ref: DatasetRef
    dataset_id: str | None
    """``None`` for a run rejected at the scope gate, sealed before `profile` loads the dataset."""
    seed: int
    profile: str
    initial_plan_ref: str
    plan_ref: str
    refs: dict[RefName, ArtifactRef]
    tool_versions: dict[str, str]
    approvals: list[ApprovalSummary]
    outcome: Literal["completed", "rejected"]
    core_version: str
    orchestrator_version: str
    events_head_hash: str


class ReplayMismatch(StrictModel):
    ref: RefName
    original_id: str
    replay_id: str


class ReplayReport(StrictModel):
    original_run_id: str
    replay_run_id: str
    matched: list[RefName]
    mismatched: list[ReplayMismatch]
    missing: list[RefName]
    ok: bool
    regenerated: list[RefName] = Field(default_factory=list)


FindingCategory = Literal[
    "conceptual_soundness",
    "data",
    "methodology",
    "performance",
    "documentation",
    "governance",
    "limitations",
    "monitoring",
]


class Finding(StrictModel):
    id: str
    round: int
    severity: Literal["critical", "high", "medium", "low"]
    category: FindingCategory
    title: str
    description: str
    evidence: list[str]
    required_action: str
    status: Literal["open", "accepted", "disputed", "closed"] = "open"
    response: str | None = None
    response_evidence: list[str] = Field(default_factory=list)
    source: Literal["precheck", "review"] = "review"
    check_key: str | None = None
    """The deterministic pre-check that raised a ``precheck`` finding."""
    verdict: str | None = None
    """The challenger's latest re-review note on this finding."""
    closed_round: int | None = None


class FindingsList(StrictModel):
    round: int
    findings: list[Finding]


class FindingResponse(StrictModel):
    finding_id: str
    decision: Literal["accept", "dispute"]
    action: str
    evidence: list[str] = Field(default_factory=list)
    round: int = 1
    """Defaults to 1 so a ``responses`` artifact sealed before the remediation loop, which
    lacks it, still validates on replay; the remediation node always sets it."""
    sections_regenerated: list[str] = Field(default_factory=list)


class ResponsesList(StrictModel):
    round: int
    responses: list[FindingResponse]
