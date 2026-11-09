"""Record models for rows and values the lineage store reads and writes."""

from datetime import date, datetime
from typing import Any, Literal

from modelforge_core.schemas import ModelingPlan, StrictModel
from pydantic import Field

RunStatus = Literal[
    "queued",
    "running",
    "awaiting_scope",
    "awaiting_model",
    "awaiting_signoff",
    "needs_attention",
    "completed",
    "rejected",
    "aborted",
]
GateName = Literal["scope", "model", "signoff"]
QueueKind = Literal["start", "resume", "replay"]
StepStatus = Literal["running", "succeeded", "failed"]


class Producer(StrictModel):
    server: str
    tool: str
    version: str


class ArtifactRef(StrictModel):
    id: str
    kind: str
    media_type: str
    size_bytes: int
    producer: Producer | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class EventRecord(StrictModel):
    id: int
    run_id: str
    step_id: str | None
    ts: str
    type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


class RunRecord(StrictModel):
    id: str
    created_at: datetime
    updated_at: datetime
    requester: str
    status: RunStatus
    profile: str
    brief_hash: str
    brief_ref: str
    dataset_ref: str
    dataset_id: str | None
    plan_ref: str | None
    seed: int
    cost_usd: float
    cost_cap_usd: float
    trace_id: str | None
    manifest_hash: str | None
    replay_of: str | None
    error: dict[str, Any] | None


class StepRecord(StrictModel):
    id: str
    run_id: str
    node: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None
    status: StepStatus
    input_state_hash: str
    output_state_hash: str | None


class ToolCallRecord(StrictModel):
    id: str
    step_id: str
    server: str
    tool: str
    version: str
    input_hash: str
    output_hash: str
    duration_ms: int
    artifact_ids: list[str]


class ArtifactRow(StrictModel):
    id: str
    run_id: str
    kind: str
    media_type: str
    size_bytes: int
    uri: str
    producer_step_id: str | None
    schema_version: str


class QueuedRun(StrictModel):
    run_id: str
    kind: QueueKind


class GateDecision(StrictModel):
    gate: GateName
    decision: str
    approver: str
    comment: str = ""
    plan: ModelingPlan | None = None
    adjust_target: Literal["features", "build"] | None = None
    conditions: list[str] = Field(default_factory=list)


class PlanDiffEntry(StrictModel):
    path: str
    old: Any
    new: Any


class ApprovalRecord(StrictModel):
    id: str
    run_id: str
    gate: GateName
    decision: str
    approver: str
    comment: str
    payload: GateDecision
    plan_diff: list[PlanDiffEntry] | None
    conditions: list[str] | None
    adjust_target: str | None
    ts: datetime


class InventoryEntry(StrictModel):
    model_id: str
    run_id: str
    name: str
    version: int
    owner: str
    intended_use: str
    tier: int
    status: str
    champion_ref: str | None
    validation_summary: dict[str, Any] | None
    approved_at: datetime | None
    approver: str | None
    conditions: list[str] | None
    next_review: date | None


class ChainReport(StrictModel):
    run_id: str
    events: int
    ok: bool
    first_bad_id: int | None


class VerifyReport(StrictModel):
    run_id: str
    chain: ChainReport
    artifacts_checked: int
    artifacts_missing: list[str]
    artifacts_corrupt: list[str]
    manifest_ok: bool
    events_head_anchored: bool
    ok: bool
