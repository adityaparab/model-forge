"""Response models of the API; the console's TypeScript types are generated from them."""

from datetime import date, datetime
from typing import Any, Literal

from modelforge_core.schemas import ModelingPlan
from modelforge_gateway.profiles import Price
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
    SettingsAuditRow,
    StepRecord,
    ToolCallRecord,
)
from modelforge_mcp.schemas import GroundingReport
from modelforge_orchestrator.schemas import Counters, FindingResponse, ReplayReport
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ApprovalRequest",
    "ArtifactItem",
    "BriefItem",
    "ConfigResponse",
    "CostBucket",
    "CostDay",
    "CostGroup",
    "CostRunRow",
    "CostSummary",
    "CostTokens",
    "CostsResponse",
    "Directories",
    "DocumentResponse",
    "EventsResponse",
    "FindingRow",
    "FindingsCounts",
    "FindingsResponse",
    "FindingsSummary",
    "GateInfo",
    "GateView",
    "HealthResponse",
    "IdentityConfig",
    "LlmCallsResponse",
    "PassageView",
    "ProfileView",
    "ReplayItem",
    "ReplayRequest",
    "ResolvedCitation",
    "ResumeRequest",
    "RouteView",
    "RunDetail",
    "RunListItem",
    "RunListResponse",
    "SectionView",
    "SettingsEditable",
    "SettingsPatch",
    "SettingsResponse",
    "StepDetail",
    "SubmitRequest",
    "TraceLinks",
    "TraceUrls",
    "TracingConfig",
    "Versions",
]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: str
    """``ok``, or the type of the error the database query raised (never its message)."""
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


class PassageView(BaseModel):
    citation_id: str
    doc_id: str
    title: str
    """The title of the passage's document."""
    section: str
    text: str
    url: str
    """The source URL of the passage's document."""


class ResolvedCitation(BaseModel):
    """A citation marker of a section body and what it points at (spec 3.3.2)."""

    marker: str
    kind: Literal["A", "K"]
    target: str
    resolved: bool
    artifact_id: str | None = None
    artifact_kind: str | None = None
    path: str | None = None
    value: Any = None
    """The JSON value at ``path``; a value longer than 2000 characters of JSON is its first
    2000 characters, with ``note`` ``truncated``."""
    passage: PassageView | None = None
    note: str | None = None
    """Why the citation did not resolve, or ``truncated``."""


class SectionView(BaseModel):
    section_id: str
    title: str
    """From the template; the section id when the template lacks the section."""
    required: bool
    body_md: str
    grounding: GroundingReport | None
    citations: list[ResolvedCitation]
    """One per distinct marker, in order of first occurrence."""


class DocumentResponse(BaseModel):
    template_id: str
    document_id: str
    """The id of the run's rendered Markdown document."""
    sections: list[SectionView]


class FindingRow(BaseModel):
    id: str
    """The short id (``F-1``)."""
    row_id: str
    """The ``findings`` row id (``<run_id>/F-1``)."""
    run_id: str
    round: int
    severity: str
    category: str
    title: str
    description: str
    evidence: list[str]
    required_action: str
    status: str
    response: str | None
    response_evidence: list[str] | None
    source: str
    check_key: str | None
    verdict: str | None
    closed_round: int | None
    citations: list[ResolvedCitation]
    """The markers of ``evidence`` and ``response_evidence`` resolved as in the document, one
    per distinct marker in order of first occurrence (section ids are not markers)."""


class FindingsSummary(BaseModel):
    n_findings: int
    by_severity: dict[str, int]
    by_status: dict[str, int]
    by_source: dict[str, int]
    max_severity: str | None
    max_open_severity: str | None
    challenge_rounds: int
    remediation_rounds: int
    sections_regenerated: list[str]
    """The union of the responses' ``sections_regenerated``, in order of first occurrence."""


class FindingsResponse(BaseModel):
    findings: list[FindingRow]
    responses: list[FindingResponse]
    """From the run's ``responses`` ref; empty when it has none."""
    summary: FindingsSummary


class _Request(BaseModel):
    """A request body: an unknown field is a 422, never silently dropped (an ``approver``
    in the body would otherwise read as if it were honoured)."""

    model_config = ConfigDict(extra="forbid")


class SubmitRequest(_Request):
    """The JSON form of ``POST /runs``; the YAML form sends the brief as the raw body."""

    brief: str
    """The brief YAML text."""
    profile: str | None = None
    """Defaults to the ``default_profile`` setting."""
    cost_cap_usd: float | None = Field(default=None, gt=0)
    """Defaults to the ``default_cost_cap_usd`` setting."""


class ApprovalRequest(_Request):
    """A gate decision; the approver is the request's identity."""

    gate: GateName
    decision: str
    comment: str = ""
    plan: ModelingPlan | None = None
    adjust_target: Literal["features", "build"] | None = None
    conditions: list[str] = Field(default_factory=list)


class ResumeRequest(_Request):
    cost_cap_usd: float | None = Field(default=None, gt=0)
    """Set the run's cost cap (USD) before resuming."""


class ReplayRequest(_Request):
    rerun_llm: bool = False
    """Call the LLM again instead of copying the original's outputs."""


class SettingsEditable(BaseModel):
    """The server-side settings an admin may change (spec 3.8)."""

    default_profile: str
    default_cost_cap_usd: float
    auto_replay: bool


class SettingsPatch(_Request):
    """Any subset of the editable settings; a wrong type is a 422, never coerced."""

    model_config = ConfigDict(extra="forbid", strict=True)

    default_profile: str | None = None
    """Must name a valid profile file in the profiles directory."""
    default_cost_cap_usd: float | None = None
    """USD; must be positive."""
    auto_replay: bool | None = None


class RouteView(BaseModel):
    provider: str
    model: str
    effort: str
    structured: str | None


class ProfileView(BaseModel):
    """A profile file of the profiles directory; ``error`` when it does not load."""

    name: str
    routes: dict[str, RouteView]
    """By purpose; empty when the profile does not load."""
    prices: dict[str, Price]
    """By model, USD per million tokens."""
    error: str | None


class SettingsResponse(BaseModel):
    editable: SettingsEditable
    audit: list[SettingsAuditRow]
    """The last 20 changes, newest first."""
    config: ConfigResponse
    """The ``GET /config`` document."""
    profiles: list[ProfileView]
    """Every profile file, by name."""


class CostTokens(BaseModel):
    input: int
    output: int
    cache_read: int
    cache_write: int


class CostRunRow(BaseModel):
    run_id: str
    status: RunStatus
    profile: str
    requester: str
    created_at: datetime
    cost_usd: float
    """From the run's calls in the window; ``0`` when it made none."""
    cost_cap_usd: float
    n_calls: int


class CostGroup(BaseModel):
    """The calls of the window sharing a profile, model or purpose, with their token sums;
    ``n_runs`` is 0 for a model or purpose group."""

    key: str
    cost_usd: float
    n_runs: int
    n_calls: int
    input_tokens: int
    output_tokens: int


class CostDay(BaseModel):
    day: date
    """The UTC day of the calls."""
    cost_usd: float
    n_calls: int


class CostsResponse(BaseModel):
    """Spend across runs (spec 3.9): the totals and groups are over the ``llm_calls`` whose
    ``ts`` is in the window; ``by_run`` lists every run created in the window."""

    since: datetime
    until: datetime
    total_usd: float
    n_runs: int
    """The runs with a call in the window."""
    n_calls: int
    tokens: CostTokens
    by_run: list[CostRunRow]
    by_profile: list[CostGroup]
    """``key`` is the profile; model and token fields are 0."""
    by_model: list[CostGroup]
    """``key`` is the model; ``n_runs`` is 0."""
    by_purpose: list[CostGroup]
    """``key`` is the purpose; ``n_runs`` and the token fields are 0."""
    by_day: list[CostDay]


class BriefItem(BaseModel):
    """An example brief of the briefs directory."""

    name: str
    path: str
    text: str
