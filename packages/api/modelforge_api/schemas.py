"""Response models of the API; the console's TypeScript types are generated from them."""

from typing import Any, Literal

from modelforge_lineage.models import (
    ApprovalRecord,
    ArtifactRef,
    ArtifactRow,
    EventRecord,
    GateName,
    InventoryEntry,
    LlmCallRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    ToolCallRecord,
)
from modelforge_orchestrator.schemas import Counters, ReplayReport
from pydantic import BaseModel

__all__ = [
    "ArtifactItem",
    "ConfigResponse",
    "CostBucket",
    "CostSummary",
    "Directories",
    "EventsResponse",
    "FindingsCounts",
    "GateInfo",
    "GateView",
    "HealthResponse",
    "IdentityConfig",
    "LlmCallsResponse",
    "ReplayItem",
    "RunDetail",
    "RunListItem",
    "RunListResponse",
    "StepDetail",
    "TraceLinks",
    "TraceUrls",
    "TracingConfig",
    "Versions",
]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: str
    """``ok``, or the error the database query raised."""
    version: str


class IdentityConfig(BaseModel):
    mode: Literal["open", "roles"]
    """``open``: every identity has every role; ``roles``: the ``MODELFORGE_USERS`` map."""
    roles: dict[str, list[str]]


class TraceUrls(BaseModel):
    jaeger: str | None
    phoenix: str | None


class TracingConfig(BaseModel):
    endpoints: list[str]
    content: bool


class Directories(BaseModel):
    artifacts: str
    data: str
    templates: str
    knowledge: str
    prompts: str
    profiles: str
    briefs: str
    console: str


class Versions(BaseModel):
    core: str
    orchestrator: str
    api: str


class GateInfo(BaseModel):
    name: GateName
    status: RunStatus
    """The run status while the run waits at this gate."""
    decisions: list[str]


class ConfigResponse(BaseModel):
    """The redacted effective configuration: never a URL with a password or a key."""

    profile_default: str
    console: Literal["built", "missing", "dev"]
    identity: IdentityConfig
    trace_urls: TraceUrls
    tracing: TracingConfig
    directories: Directories
    versions: Versions
    nodes: list[str]
    gates: list[GateInfo]


class CostBucket(BaseModel):
    n_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


class CostSummary(BaseModel):
    """The run's spend from its ``llm_calls`` rows (spec 3.3.1)."""

    spent_usd: float
    cap_usd: float
    n_calls: int
    n_schema_retries: int
    """The number of calls whose output failed the schema (``schema_valid = false``)."""
    by_purpose: dict[str, CostBucket]
    by_model: dict[str, CostBucket]


class FindingsCounts(BaseModel):
    n: int
    max_open_severity: str | None
    """The highest severity among the findings still ``open`` or ``disputed``."""


class RunListItem(RunRecord):
    model_name: str | None
    """The inventory entry's name; ``None`` before intake."""
    findings: FindingsCounts


class RunListResponse(BaseModel):
    runs: list[RunListItem]
    total: int
    limit: int
    offset: int


class ReplayItem(BaseModel):
    run: RunRecord
    report: ReplayReport | None
    """The replay's `ReplayReport`; ``None`` until the replay has sealed."""


class TraceLinks(BaseModel):
    trace_id: str | None
    jaeger_url: str | None
    phoenix_url: str | None


class GateView(BaseModel):
    gate: str
    summary: dict[str, Any]


class RunDetail(BaseModel):
    run: RunRecord
    refs: dict[str, ArtifactRef]
    counters: Counters | None
    """From the checkpoint; ``None`` before the first step."""
    max_remediation_rounds: int | None
    gate: GateView | None
    """The latest gate prompt, only while the run awaits a gate."""
    approvals: list[ApprovalRecord]
    findings_summary: dict[str, Any] | None
    responses_summary: dict[str, Any] | None
    cost: CostSummary
    inventory: InventoryEntry | None
    trace: TraceLinks
    replays: list[ReplayItem]


class StepDetail(StepRecord):
    duration_ms: int | None
    """``None`` while the step runs."""
    tool_calls: list[ToolCallRecord]
    llm_calls: list[LlmCallRecord]


class LlmCallsResponse(BaseModel):
    calls: list[LlmCallRecord]
    cost: CostSummary


class ArtifactItem(ArtifactRow):
    ref_names: list[str]
    """The checkpoint ref names that point at the artifact."""
    summary: dict[str, Any] | None
    """The summary of the first ref naming the artifact; ``None`` when no ref does."""


class EventsResponse(BaseModel):
    events: list[EventRecord]
    next_after: int
    """The ``after`` that fetches the events following these."""
