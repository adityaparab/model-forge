"""Human gate decisions: validation, four-eyes, the adjust loop bound and plan diffs."""

from typing import Any

from modelforge_core.schemas import ModelingPlan
from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from modelforge_lineage.events import append_event
from modelforge_lineage.findings import blocking_findings
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import ApprovalRecord, GateDecision, GateName, PlanDiffEntry
from modelforge_lineage.runs import RunNotFound

VALID_DECISIONS: dict[GateName, frozenset[str]] = {
    "scope": frozenset({"approve", "edit", "reject"}),
    "model": frozenset({"accept", "adjust", "reject"}),
    "signoff": frozenset({"approve", "approve_with_conditions", "reject"}),
}

_FOUR_EYES_GATES: frozenset[GateName] = frozenset({"model", "signoff"})


class ApprovalError(ValueError):
    pass


class GateMismatchError(ApprovalError):
    pass


class FourEyesError(ApprovalError):
    pass


class LoopBoundError(ApprovalError):
    pass


class InvalidDecisionError(ApprovalError):
    pass


class BlockingFindingsError(ApprovalError):
    pass


def _diff(old: Any, new: Any, path: str, out: list[PlanDiffEntry]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in [*old, *(k for k in new if k not in old)]:
            child = f"{path}.{key}" if path else str(key)
            _diff(old.get(key), new.get(key), child, out)
    elif old != new:
        out.append(PlanDiffEntry(path=path, old=old, new=new))


def plan_diff(old: ModelingPlan, new: ModelingPlan) -> list[PlanDiffEntry]:
    """Changed leaves as dotted paths over the JSON dumps; lists are compared whole."""
    out: list[PlanDiffEntry] = []
    _diff(old.model_dump(mode="json"), new.model_dump(mode="json"), "", out)
    return out


def _validate_decision(run_id: str, decision: GateDecision) -> None:
    if decision.decision not in VALID_DECISIONS[decision.gate]:
        raise InvalidDecisionError(
            f"run {run_id}: {decision.decision!r} is not a valid decision at gate {decision.gate}"
        )
    if decision.decision in ("edit", "adjust") and decision.plan is None:
        raise InvalidDecisionError(f"run {run_id}: {decision.decision!r} requires a plan")
    if decision.decision == "adjust" and decision.adjust_target is None:
        raise InvalidDecisionError(f"run {run_id}: 'adjust' requires an adjust_target")
    if decision.decision == "approve_with_conditions" and not decision.conditions:
        raise InvalidDecisionError(
            f"run {run_id}: 'approve_with_conditions' requires at least one condition"
        )


def record_approval(
    conn: Connection[DictRow],
    run_id: str,
    decision: GateDecision,
    *,
    current_plan: ModelingPlan | None,
    loop_bound: int = 3,
) -> ApprovalRecord:
    """Validate and record a gate decision; call inside a transaction. Raises before any write."""
    if conn.info.transaction_status == TransactionStatus.IDLE:
        raise RuntimeError("record_approval requires an open transaction")
    run = conn.execute(
        "select status, requester from runs where id = %s for update", (run_id,)
    ).fetchone()
    if run is None:
        raise RunNotFound(run_id)
    expected = f"awaiting_{decision.gate}"
    if run["status"] != expected:
        raise GateMismatchError(
            f"run {run_id} is {run['status']}, not awaiting gate {decision.gate}"
        )
    _validate_decision(run_id, decision)
    if decision.gate in _FOUR_EYES_GATES and decision.approver == run["requester"]:
        raise FourEyesError(
            f"run {run_id}: approver {decision.approver} requested the run and cannot"
            f" decide gate {decision.gate}"
        )
    if decision.gate == "signoff" and decision.decision == "approve":
        blocking = blocking_findings(conn, run_id)
        if blocking:
            listed = ", ".join(
                f"{f['id'].removeprefix(f'{run_id}/')} ({f['severity']}) {f['title']}"
                for f in blocking
            )
            raise BlockingFindingsError(
                f"run {run_id}: approve is refused while findings are open or disputed at"
                f" critical or high severity: {listed}; use approve_with_conditions or reject"
            )
    if decision.decision == "adjust":
        count = conn.execute(
            "select count(*) as n from approvals where run_id = %s and decision = 'adjust'",
            (run_id,),
        ).fetchone()
        assert count is not None
        if count["n"] >= loop_bound:
            raise LoopBoundError(
                f"run {run_id}: {count['n']} adjust decisions already recorded (bound {loop_bound})"
            )
    diff = (
        plan_diff(current_plan, decision.plan)
        if decision.plan is not None and current_plan is not None
        else None
    )
    row = conn.execute(
        "insert into approvals (id, run_id, gate, decision, approver, comment, payload,"
        " plan_diff, conditions, adjust_target, ts)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp()) returning *",
        (
            new_id("appr"),
            run_id,
            decision.gate,
            decision.decision,
            decision.approver,
            decision.comment,
            Jsonb(decision.model_dump(mode="json")),
            Jsonb([e.model_dump() for e in diff]) if diff is not None else None,
            decision.conditions or None,
            decision.adjust_target,
        ),
    ).fetchone()
    assert row is not None
    append_event(
        conn,
        run_id,
        "gate.decided",
        {"gate": decision.gate, "decision": decision.decision, "approver": decision.approver},
    )
    return ApprovalRecord.model_validate(row)


def list_approvals(
    conn: Connection[DictRow], run_id: str, *, gate: GateName | None = None
) -> list[ApprovalRecord]:
    if gate is None:
        rows = conn.execute("select * from approvals where run_id = %s order by ts, id", (run_id,))
    else:
        rows = conn.execute(
            "select * from approvals where run_id = %s and gate = %s order by ts, id",
            (run_id, gate),
        )
    return [ApprovalRecord.model_validate(r) for r in rows]
