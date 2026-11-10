"""Run reads: the list, the detail, steps, LLM calls, artifacts, approvals, the manifest,
verification, events and replays (spec 3.3)."""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from modelforge_lineage import runs
from modelforge_lineage.approvals import list_approvals
from modelforge_lineage.db import Database
from modelforge_lineage.inventory import get_for_run
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.models import (
    ApprovalRecord,
    ArtifactRef,
    RunRecord,
    RunStatus,
    VerifyReport,
)
from modelforge_lineage.steps import list_artifacts, list_steps, list_tool_calls
from modelforge_lineage.store import (
    MEDIA_HTML,
    MEDIA_JSON,
    MEDIA_MARKDOWN,
    MEDIA_PARQUET,
    MEDIA_YAML,
    ArtifactStore,
)
from modelforge_lineage.verify import verify_run
from modelforge_orchestrator.inspect import gate_prompt, run_refs, run_state
from modelforge_orchestrator.schemas import Manifest
from opentelemetry import trace

from modelforge_api import queries
from modelforge_api.deps import get_db, get_settings, get_store
from modelforge_api.errors import ApiError
from modelforge_api.schemas import (
    ArtifactItem,
    EventsResponse,
    GateView,
    LlmCallsResponse,
    ReplayItem,
    RunDetail,
    RunListItem,
    RunListResponse,
    StepDetail,
    TraceLinks,
)
from modelforge_api.settings import ApiSettings

__all__ = ["router"]

router = APIRouter(prefix="/runs", tags=["runs"])

_AWAITING: frozenset[str] = frozenset({"awaiting_scope", "awaiting_model", "awaiting_signoff"})
_TEXT_TYPES = frozenset({MEDIA_YAML, MEDIA_MARKDOWN, MEDIA_HTML})
_EXTENSIONS = {MEDIA_PARQUET: ".parquet", "text/plain": ".txt"}

Db = Annotated[Database, Depends(get_db)]
Store = Annotated[ArtifactStore, Depends(get_store)]
RunId = Annotated[str, Path(description="The run id.")]


def get_run_record(run_id: RunId, db: Db) -> RunRecord:
    """The run, or ``404`` kind ``not_found``; tags the request's span with the run id."""
    trace.get_current_span().set_attribute("modelforge.run_id", run_id)
    try:
        with db.connection() as conn:
            return runs.get_run(conn, run_id)
    except runs.RunNotFound:
        raise ApiError(404, "not_found", f"run not found: {run_id}", run_id=run_id) from None


Run = Annotated[RunRecord, Depends(get_run_record)]


def _sealed(run: RunRecord) -> str:
    if run.manifest_hash is None:
        raise ApiError(409, "not_ready", f"run {run.id} is not sealed yet", run_id=run.id)
    return run.manifest_hash


@router.get("", response_model=RunListResponse)
def list_runs(
    db: Db,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: RunStatus | None = None,
    profile: str | None = None,
    requester: str | None = None,
    replays: Literal["include", "exclude"] = "include",
) -> RunListResponse:
    filters: dict[str, Any] = {
        "status": status,
        "profile": profile,
        "requester": requester,
        "include_replays": replays == "include",
    }
    with db.connection() as conn:
        records = runs.list_runs(conn, limit=limit, offset=offset, **filters)
        total = runs.count_runs(conn, **filters)
        ids = [r.id for r in records]
        names = queries.inventory_names(conn, ids)
        findings = queries.findings_counts(conn, ids)
    items = [
        RunListItem(**r.model_dump(), model_name=names.get(r.id), findings=findings[r.id])
        for r in records
    ]
    return RunListResponse(runs=items, total=total, limit=limit, offset=offset)


@router.get("/{run_id}", response_model=RunDetail)
def run_detail(
    run: Run,
    db: Db,
    store: Store,
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> RunDetail:
    state = run_state(db, run.id)
    refs: dict[str, ArtifactRef] = {**state.refs} if state is not None else {}
    gate = None
    if run.status in _AWAITING:
        prompt = gate_prompt(db, run.id)
        if prompt is not None:
            gate = GateView(gate=prompt["gate"], summary=prompt["summary"])
    with db.connection() as conn:
        approvals = list_approvals(conn, run.id)
        cost = queries.cost_summary(conn, run.id, spent_usd=run.cost_usd, cap_usd=run.cost_cap_usd)
        inventory = get_for_run(conn, run.id)
    jaeger = (
        f"{settings.jaeger_url}/trace/{run.trace_id}"
        if settings.jaeger_url is not None and run.trace_id is not None
        else None
    )
    findings, responses = refs.get("findings"), refs.get("responses")
    return RunDetail(
        run=run,
        refs=refs,
        counters=state.counters if state is not None else None,
        max_remediation_rounds=state.max_remediation_rounds if state is not None else None,
        gate=gate,
        approvals=approvals,
        findings_summary=findings.summary if findings is not None else None,
        responses_summary=responses.summary if responses is not None else None,
        cost=cost,
        inventory=inventory,
        trace=TraceLinks(
            trace_id=run.trace_id, jaeger_url=jaeger, phoenix_url=settings.phoenix_url
        ),
        replays=queries.replay_items(db, store, run.id),
    )


@router.get("/{run_id}/steps", response_model=list[StepDetail])
def steps(run: Run, db: Db) -> list[StepDetail]:
    with db.connection() as conn:
        records = list_steps(conn, run.id)
        tool_calls = list_tool_calls(conn, run.id)
        llm_calls = list_llm_calls(conn, run.id)
    details: list[StepDetail] = []
    for step in records:
        duration = (
            int((step.finished_at - step.started_at).total_seconds() * 1000)
            if step.finished_at is not None
            else None
        )
        details.append(
            StepDetail(
                **step.model_dump(),
                duration_ms=duration,
                tool_calls=[c for c in tool_calls if c.step_id == step.id],
                llm_calls=[c for c in llm_calls if c.step_id == step.id],
            )
        )
    return details


@router.get("/{run_id}/llm-calls", response_model=LlmCallsResponse)
def llm_calls(run: Run, db: Db) -> LlmCallsResponse:
    with db.connection() as conn:
        calls = list_llm_calls(conn, run.id)
        cost = queries.cost_summary(conn, run.id, spent_usd=run.cost_usd, cap_usd=run.cost_cap_usd)
    return LlmCallsResponse(calls=calls, cost=cost)


@router.get("/{run_id}/artifacts", response_model=list[ArtifactItem])
def artifacts(run: Run, db: Db) -> list[ArtifactItem]:
    with db.connection() as conn:
        rows = list_artifacts(conn, run.id)
    named: dict[str, list[tuple[str, ArtifactRef]]] = {}
    for name, ref in run_refs(db, run.id).items():
        named.setdefault(ref.id, []).append((name, ref))
    items: list[ArtifactItem] = []
    for row in rows:
        refs = named.get(row.id, [])
        items.append(
            ArtifactItem(
                **row.model_dump(),
                ref_names=[name for name, _ in refs],
                summary=refs[0][1].summary if refs else None,
            )
        )
    return items


@router.get(
    "/{run_id}/artifacts/{artifact_id}",
    response_class=Response,
    responses={
        200: {
            "description": "JSON for JSON artifacts, text for YAML, Markdown and HTML, a download"
            " for everything else.",
        }
    },
)
def artifact_content(run: Run, artifact_id: str, db: Db, store: Store) -> Response:
    with db.connection() as conn:
        row = queries.get_artifact_row(conn, run.id, artifact_id)
    if row is None or not store.exists(artifact_id):
        raise ApiError(
            404,
            "not_found",
            f"artifact {artifact_id} not found for run {run.id}",
            run_id=run.id,
            artifact_id=artifact_id,
        )
    if row.media_type == MEDIA_JSON:
        return JSONResponse(content=json.loads(store.get(artifact_id)))
    if row.media_type in _TEXT_TYPES:
        return Response(content=store.get(artifact_id), media_type=row.media_type)
    filename = f"{artifact_id}{_EXTENSIONS.get(row.media_type, '')}"
    return FileResponse(
        store.path(artifact_id),
        media_type=row.media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{run_id}/approvals", response_model=list[ApprovalRecord])
def approvals(run: Run, db: Db) -> list[ApprovalRecord]:
    with db.connection() as conn:
        return list_approvals(conn, run.id)


@router.get("/{run_id}/manifest", response_model=Manifest)
def manifest(run: Run, store: Store) -> Manifest:
    return store.get_model(_sealed(run), Manifest)


@router.get("/{run_id}/verify", response_model=VerifyReport)
def verify(run: Run, db: Db, store: Store) -> VerifyReport:
    _sealed(run)
    with db.connection() as conn:
        return verify_run(conn, store, run.id)


@router.get("/{run_id}/events", response_model=EventsResponse)
def events(
    run: Run,
    db: Db,
    after: Annotated[int, Query(ge=0, description="An event id; events above it.")] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> EventsResponse:
    with db.connection() as conn:
        records = queries.list_events_after(conn, run.id, after, limit)
    return EventsResponse(events=records, next_after=records[-1].id if records else after)


@router.get("/{run_id}/replays", response_model=list[ReplayItem])
def replays(run: Run, db: Db, store: Store) -> list[ReplayItem]:
    return queries.replay_items(db, store, run.id)
