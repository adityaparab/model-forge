"""The eval harness: submit a brief and drive its run through the scripted gate decisions."""

from pathlib import Path
from typing import Any

from modelforge_core.schemas import ModelingPlan, StrictModel
from modelforge_gateway.gateway import Gateway
from modelforge_lineage import runs
from modelforge_lineage.approvals import BlockingFindingsError, record_approval
from modelforge_lineage.db import Database
from modelforge_lineage.facade import Lineage
from modelforge_lineage.models import GateDecision, RunRecord
from modelforge_lineage.queue import enqueue
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.submit import create_run_from_brief
from modelforge_orchestrator.tools import StdioToolClient
from modelforge_orchestrator.worker import run_once

from modelforge_evals.config import ApprovalSpec

__all__ = ["DrivenRun", "EvalError", "build_deps", "drive_run"]

FINAL_STATUSES = ("completed", "rejected")


class EvalError(RuntimeError):
    """A driven run parked: in ``needs_attention``, or at a gate with no decision left."""


class DrivenRun(StrictModel):
    run_id: str
    status: str
    approvals: list[str]
    """``gate decision approver`` for each decision recorded, in order."""
    error: dict[str, Any] | None


def build_deps(settings: Settings, db: Database, store: ArtifactStore, profile: str) -> Deps:
    """Node dependencies outside the worker; the caller enters ``deps.tools`` before use."""
    lineage = Lineage(db)
    return Deps(
        tools=StdioToolClient(settings),
        lineage=lineage,
        store=store,
        settings=settings,
        gateway=Gateway.for_run(settings.gateway, profile, lineage, store),
    )


def _execute(settings: Settings, db: Database, store: ArtifactStore, run_id: str) -> RunRecord:
    run_once(settings, db, store, run_id=run_id)
    with db.connection() as conn:
        run = runs.get_run(conn, run_id)
    if run.status == "needs_attention":
        raise EvalError(f"run {run_id} needs attention: {run.error}")
    return run


def _record(db: Database, store: ArtifactStore, run: RunRecord, decision: GateDecision) -> None:
    plan = store.get_model(run.plan_ref, ModelingPlan) if run.plan_ref else None
    with db.transaction() as conn:
        record_approval(conn, run.id, decision, current_plan=plan)
        enqueue(conn, run.id, "resume")


def drive_run(
    settings: Settings,
    db: Database,
    store: ArtifactStore,
    brief_path: Path,
    *,
    profile: str,
    approvals: list[ApprovalSpec],
    requester: str = "evals-requester",
) -> DrivenRun:
    """Create the run, execute it, then record each approval and resume until it finishes.

    An ``approve`` at sign-off refused for blocking findings is recorded instead as
    ``approve_with_conditions`` with the refusal message as its one condition. `EvalError`
    when the run lands in ``needs_attention`` or waits at a gate after the last approval.
    """
    created = create_run_from_brief(
        db,
        store,
        brief_path,
        requester=requester,
        profile=profile,
        cost_cap_usd=settings.gateway.cost_cap_usd,
        gateway=settings.gateway,
    )
    run = _execute(settings, db, store, created.id)
    recorded: list[str] = []
    for spec in approvals:
        if run.status in FINAL_STATUSES:
            break
        decision = GateDecision(gate=spec.gate, decision=spec.decision, approver=spec.approver)
        try:
            _record(db, store, run, decision)
        except BlockingFindingsError as exc:
            if (spec.gate, spec.decision) != ("signoff", "approve"):
                raise
            decision = decision.model_copy(
                update={"decision": "approve_with_conditions", "conditions": [str(exc)]}
            )
            _record(db, store, run, decision)
        recorded.append(f"{decision.gate} {decision.decision} {decision.approver}")
        run = _execute(settings, db, store, run.id)
    if run.status not in FINAL_STATUSES:
        raise EvalError(f"run {run.id} is {run.status} after the last scripted approval")
    return DrivenRun(run_id=run.id, status=run.status, approvals=recorded, error=run.error)
