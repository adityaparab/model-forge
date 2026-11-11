"""Run writes: submit, gate decisions, resume, pause, abort and replay (spec 3.4).

None executes the graph: each writes lineage rows and enqueues in one transaction, and the
worker executes, exactly as the CLI's ``--detach``.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from modelforge_core.schemas import ModelingPlan
from modelforge_gateway.schemas import GatewayError
from modelforge_lineage import runs
from modelforge_lineage import settings as server_settings
from modelforge_lineage.approvals import ApprovalError, BlockingFindingsError, record_approval
from modelforge_lineage.db import Database
from modelforge_lineage.findings import blocking_findings
from modelforge_lineage.models import ApprovalRecord, GateDecision, RunRecord, RunStatus
from modelforge_lineage.queue import enqueue_if_absent, release
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.briefs import BriefError
from modelforge_orchestrator.replay import ReplayError, create_replay
from modelforge_orchestrator.submit import UnknownAdapterError, create_run
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from modelforge_api.deps import get_db, get_settings, get_store
from modelforge_api.errors import ApiError
from modelforge_api.identity import Identity, require
from modelforge_api.routers.runs import Run
from modelforge_api.schemas import ApprovalRequest, ReplayRequest, ResumeRequest, SubmitRequest
from modelforge_api.settings import ApiSettings

__all__ = ["ABORTABLE", "MAX_BRIEF_BYTES", "YAML_MEDIA_TYPES", "router"]

router = APIRouter(prefix="/runs", tags=["runs"])

MAX_BRIEF_BYTES = 1024 * 1024
YAML_MEDIA_TYPES: frozenset[str] = frozenset(
    {"application/yaml", "application/x-yaml", "text/yaml"}
)
_MAX_JSON_BYTES = 4 * MAX_BRIEF_BYTES
"""The JSON envelope escapes the brief text, so its body may exceed the brief's own limit."""
_TOO_LARGE = "brief larger than 1 MiB"
_ALREADY_DECIDED = "a decision at this gate is already queued"
ABORTABLE: tuple[RunStatus, ...] = (
    "queued",
    "awaiting_scope",
    "awaiting_model",
    "awaiting_signoff",
    "needs_attention",
    "paused",
)
"""The statuses an abort moves from: neither running nor final."""

Db = Annotated[Database, Depends(get_db)]
Store = Annotated[ArtifactStore, Depends(get_store)]
Settings = Annotated[ApiSettings, Depends(get_settings)]
Requester = Annotated[Identity, Depends(require("requester"))]
APPROVER = [Depends(require("approver"))]
"""Declared on the route so the role is checked before the run lookup (no 404 for a 403)."""

_SUBMIT_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": SubmitRequest.model_json_schema()},
            **{
                media: {"schema": {"type": "string", "description": "The brief YAML."}}
                for media in sorted(YAML_MEDIA_TYPES)
            },
        },
    }
}


async def _read_body(request: Request, limit: int) -> bytes:
    """The request body; ``400`` kind ``brief`` as soon as it passes ``limit`` bytes."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise ApiError(400, "brief", _TOO_LARGE)
    return bytes(body)


def _parse_json(body: bytes) -> SubmitRequest:
    try:
        return SubmitRequest.model_validate_json(body)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)  # never echo the body
        raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in errors]) from None


def _submit(
    db: Database,
    store: ArtifactStore,
    settings: ApiSettings,
    data: bytes,
    *,
    requester: str,
    profile: str | None,
    cost_cap_usd: float | None,
) -> RunRecord:
    core = settings.core
    with db.connection() as conn:
        if profile is None:
            profile = str(server_settings.get(conn, "default_profile", core.profile))
        if cost_cap_usd is None:
            cost_cap_usd = float(
                server_settings.get(conn, "default_cost_cap_usd", core.gateway.cost_cap_usd)
            )
    try:
        return create_run(
            db,
            store,
            data,
            requester=requester,
            profile=profile,
            cost_cap_usd=cost_cap_usd,
            gateway=core.gateway,
        )
    except (BriefError, UnknownAdapterError) as exc:
        raise ApiError(400, "brief", str(exc)) from None
    except GatewayError as exc:
        raise ApiError(400, "profile", exc.message) from None


@router.post("", status_code=201, response_model=RunRecord, openapi_extra=_SUBMIT_BODY)
async def submit_run(
    who: Requester,
    request: Request,
    db: Db,
    store: Store,
    settings: Settings,
    profile: Annotated[
        str | None, Query(description="The profile of a YAML body; JSON sends it in the body.")
    ] = None,
    cost_cap_usd: Annotated[
        float | None,
        Query(gt=0, description="The cost cap of a YAML body; JSON sends it in the body."),
    ] = None,
) -> RunRecord:
    """Submit a brief: create the run and enqueue its start.

    The body is JSON (`SubmitRequest`), or the brief YAML itself with a YAML ``Content-Type``
    and ``profile`` and ``cost_cap_usd`` as query parameters. Unset values default to the
    ``default_profile`` and ``default_cost_cap_usd`` settings.
    """
    media = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media in YAML_MEDIA_TYPES:
        data = await _read_body(request, MAX_BRIEF_BYTES)
    else:
        payload = _parse_json(await _read_body(request, _MAX_JSON_BYTES))
        data = payload.brief.encode()
        if len(data) > MAX_BRIEF_BYTES:
            raise ApiError(400, "brief", _TOO_LARGE)
        profile, cost_cap_usd = payload.profile, payload.cost_cap_usd
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(400, "brief", f"brief is not UTF-8: {exc}") from None
    return await run_in_threadpool(
        _submit,
        db,
        store,
        settings,
        data,
        requester=who.name,
        profile=profile,
        cost_cap_usd=cost_cap_usd,
    )


@router.post("/{run_id}/approvals", status_code=202, response_model=ApprovalRecord)
def approve(
    who: Annotated[Identity, Depends(require("approver"))],
    run: Run,
    body: ApprovalRequest,
    db: Db,
    store: Store,
) -> ApprovalRecord:
    """Record a gate decision as the request's identity and enqueue the resume."""
    decision = GateDecision(
        gate=body.gate,
        decision=body.decision,
        approver=who.name,
        comment=body.comment,
        plan=body.plan,
        adjust_target=body.adjust_target,
        conditions=body.conditions,
    )
    current_plan = store.get_model(run.plan_ref, ModelingPlan) if run.plan_ref else None
    try:
        with db.transaction() as conn:
            record = record_approval(
                conn, run.id, decision, current_plan=current_plan, loop_bound=3
            )
            if not enqueue_if_absent(conn, run.id, "resume"):
                # a second decision must not reset a claimed row; roll this one back
                raise ApiError(409, "approval", _ALREADY_DECIDED)
    except BlockingFindingsError as exc:
        with db.connection() as conn:
            rows = blocking_findings(conn, run.id)
        findings = [
            {
                "id": r["id"].removeprefix(f"{run.id}/"),
                "severity": r["severity"],
                "title": r["title"],
            }
            for r in rows
        ]
        raise ApiError(409, "approval", str(exc), findings=findings) from None
    except ApprovalError as exc:
        raise ApiError(409, "approval", str(exc)) from None
    return record


@router.post("/{run_id}/resume", status_code=202, response_model=RunRecord, dependencies=APPROVER)
def resume(run: Run, db: Db, body: Annotated[ResumeRequest | None, Body()] = None) -> RunRecord:
    """Continue a ``needs_attention`` or ``paused`` run from its last checkpoint."""
    with db.transaction() as conn:
        # Locked: a concurrent abort either lands first (and is seen here) or waits for us.
        current = runs.get_run(conn, run.id, for_update=True)
        if current.status not in ("needs_attention", "paused"):
            raise ApiError(
                409,
                "state",
                f"run {run.id} is {current.status}; only a needs_attention or paused run resumes",
            )
        if body is not None and body.cost_cap_usd is not None:
            runs.set_cost_cap(conn, run.id, body.cost_cap_usd)
        runs.set_error(conn, run.id, None)
        if not enqueue_if_absent(conn, run.id, "resume"):
            # raising inside the transaction rolls the cap and error changes back
            raise ApiError(409, "state", f"run {run.id} is already queued")
        return runs.get_run(conn, run.id)


@router.post("/{run_id}/pause", status_code=202, response_model=RunRecord, dependencies=APPROVER)
def pause(run: Run, db: Db) -> RunRecord:
    """Ask a running run to pause at its next node boundary."""
    with db.transaction() as conn:
        if not runs.request_pause(conn, run.id):
            raise ApiError(409, "state", f"run {run.id} is not running; only a running run pauses")
        return runs.get_run(conn, run.id)


@router.post("/{run_id}/abort", response_model=RunRecord, dependencies=APPROVER)
def abort(run: Run, db: Db) -> RunRecord:
    """Mark a run aborted; a running run must be paused first."""
    with db.transaction() as conn:
        # The run row first, then its queue row: the lock order `resume` takes.
        if not runs.transition(conn, run.id, from_statuses=ABORTABLE, to="aborted"):
            status = runs.get_run(conn, run.id).status
            if status == "running":
                raise ApiError(
                    409,
                    "state",
                    f"run {run.id} is running and there is no cancellation; pause it first"
                    f" (POST /runs/{run.id}/pause), then abort it once it is paused",
                )
            raise ApiError(409, "state", f"run {run.id} is {status} and cannot be aborted")
        release(conn, run.id)  # a pending start or resume
        return runs.get_run(conn, run.id)


@router.post("/{run_id}/replay", status_code=202, response_model=RunRecord, dependencies=APPROVER)
def replay(run: Run, db: Db, body: Annotated[ReplayRequest | None, Body()] = None) -> RunRecord:
    """Create and enqueue a replay of a sealed run; the replay's `RunRecord`."""
    rerun_llm = body.rerun_llm if body is not None else False
    with db.transaction() as conn:
        try:
            return create_replay(conn, run.id, rerun_llm=rerun_llm)
        except ReplayError as exc:
            raise ApiError(409, "state", str(exc)) from None
