from typing import Any, cast

import pytest
from modelforge_core.schemas import (
    FeaturePolicy,
    Methodology,
    ModelingPlan,
    TargetDefinition,
    Thresholds,
    VintagePolicy,
)
from modelforge_lineage.approvals import (
    BlockingFindingsError,
    FourEyesError,
    GateMismatchError,
    InvalidDecisionError,
    LoopBoundError,
    list_approvals,
    record_approval,
)
from modelforge_lineage.db import Database
from modelforge_lineage.events import list_events
from modelforge_lineage.findings import insert_findings, set_response, set_verdict
from modelforge_lineage.models import GateDecision, GateName, PlanDiffEntry, RunStatus
from modelforge_lineage.runs import set_status
from modelforge_lineage.testing import create_test_run
from psycopg import Connection
from psycopg.rows import DictRow

PLAN = ModelingPlan(
    model_name="m",
    portfolio="p",
    target=TargetDefinition(event="e", horizon="h"),
    vintage_policy=VintagePolicy(oot_vintages=[5]),
    feature_policy=FeaturePolicy(),
    methodology=Methodology(),
    thresholds=Thresholds(),
    regulatory_scope=[],
    intended_use="u",
    tier=2,
)


def _awaiting(conn: Connection[DictRow], gate: GateName) -> None:
    create_test_run(conn)
    set_status(conn, "run_a", cast(RunStatus, f"awaiting_{gate}"))


@pytest.mark.db
def test_scope_edit_records_diff_and_event(db: Database) -> None:
    edited = PLAN.model_copy(
        update={"feature_policy": PLAN.feature_policy.model_copy(update={"max_features": 12})}
    )
    with db.transaction() as conn:
        _awaiting(conn, "scope")
        rec = record_approval(
            conn,
            "run_a",
            GateDecision(gate="scope", decision="edit", approver="alice", plan=edited),
            current_plan=PLAN,
        )
    assert rec.plan_diff == [PlanDiffEntry(path="feature_policy.max_features", old=20, new=12)]
    with db.connection() as conn:
        assert list_events(conn, "run_a")[-1].type == "gate.decided"
        assert list_approvals(conn, "run_a", gate="scope") == [rec]


@pytest.mark.db
def test_four_eyes_on_model_and_signoff(db: Database) -> None:
    cases: tuple[tuple[GateName, str], ...] = (("model", "accept"), ("signoff", "approve"))
    for gate, decision in cases:
        with db.transaction() as conn:
            _awaiting(conn, gate)
            with pytest.raises(FourEyesError):
                record_approval(
                    conn,
                    "run_a",
                    GateDecision(gate=gate, decision=decision, approver="alice"),
                    current_plan=PLAN,
                )
            record_approval(
                conn,
                "run_a",
                GateDecision(gate=gate, decision=decision, approver="bob"),
                current_plan=PLAN,
            )
            conn.execute("delete from approvals")
            conn.execute("delete from events")
            conn.execute("delete from runs")


@pytest.mark.db
def test_record_approval_rejects_wrong_gate(db: Database) -> None:
    with db.transaction() as conn:
        _awaiting(conn, "model")
        with pytest.raises(GateMismatchError):
            record_approval(
                conn,
                "run_a",
                GateDecision(gate="scope", decision="approve", approver="bob"),
                current_plan=PLAN,
            )
        set_status(conn, "run_a", "completed")
        with pytest.raises(GateMismatchError):
            record_approval(
                conn,
                "run_a",
                GateDecision(gate="model", decision="accept", approver="bob"),
                current_plan=PLAN,
            )
    with db.connection() as conn:
        assert list_approvals(conn, "run_a") == []


@pytest.mark.db
def test_invalid_decisions_and_loop_bound(db: Database) -> None:
    with db.transaction() as conn:
        _awaiting(conn, "model")
        with pytest.raises(InvalidDecisionError):
            record_approval(
                conn,
                "run_a",
                GateDecision(gate="model", decision="approve", approver="bob"),
                current_plan=PLAN,
            )
        with pytest.raises(InvalidDecisionError):  # adjust without plan/target
            record_approval(
                conn,
                "run_a",
                GateDecision(gate="model", decision="adjust", approver="bob"),
                current_plan=PLAN,
            )
        adjust = GateDecision(
            gate="model", decision="adjust", approver="bob", plan=PLAN, adjust_target="build"
        )
        for _ in range(3):
            record_approval(conn, "run_a", adjust, current_plan=PLAN)
        with pytest.raises(LoopBoundError):
            record_approval(conn, "run_a", adjust, current_plan=PLAN)


@pytest.mark.db
def test_record_approval_requires_transaction(db: Database) -> None:
    with db.transaction() as conn:
        _awaiting(conn, "scope")
    with db.connection() as conn:
        with pytest.raises(RuntimeError, match="record_approval requires an open transaction"):
            record_approval(
                conn,
                "run_a",
                GateDecision(gate="scope", decision="approve", approver="alice"),
                current_plan=PLAN,
            )
        assert list_approvals(conn, "run_a") == []


def _high_finding(short_id: str, title: str) -> dict[str, Any]:
    return {
        "id": f"run_a/{short_id}",
        "round": 1,
        "severity": "high",
        "category": "performance",
        "title": title,
        "description": "d",
        "evidence": ["e" * 64],
        "required_action": "a",
        "status": "open",
        "response": None,
        "response_evidence": [],
    }


@pytest.mark.db
def test_signoff_approve_blocked_by_open_high_finding(db: Database) -> None:
    approve = GateDecision(gate="signoff", decision="approve", approver="bob")
    with db.transaction() as conn:
        _awaiting(conn, "signoff")
        insert_findings(conn, "run_a", [_high_finding("F-1", "Calibration drifts")])
    with db.transaction() as conn, pytest.raises(BlockingFindingsError) as exc:
        record_approval(conn, "run_a", approve, current_plan=None)
    assert "F-1 (high) Calibration drifts" in str(exc.value)
    assert "approve_with_conditions" in str(exc.value)
    with db.connection() as conn:
        assert list_approvals(conn, "run_a") == []

    with db.transaction() as conn:
        rec = record_approval(
            conn,
            "run_a",
            GateDecision(
                gate="signoff",
                decision="approve_with_conditions",
                approver="bob",
                conditions=["recalibrate before go-live"],
            ),
            current_plan=None,
        )
    assert rec.decision == "approve_with_conditions"
    with db.connection() as conn:
        assert [a.decision for a in list_approvals(conn, "run_a")] == ["approve_with_conditions"]

    with db.transaction() as conn:
        set_response(
            conn,
            "run_a",
            "run_a/F-1",
            status="disputed",
            response="drift is within tolerance",
            response_evidence=["r" * 64],
        )
    with db.transaction() as conn, pytest.raises(BlockingFindingsError) as exc:
        record_approval(conn, "run_a", approve, current_plan=None)
    assert "F-1 (high) Calibration drifts" in str(exc.value)

    with db.transaction() as conn:
        set_verdict(
            conn, "run_a", "run_a/F-1", status="closed", verdict="dispute upheld", closed_round=2
        )
        rec = record_approval(conn, "run_a", approve, current_plan=None)
    assert rec.decision == "approve"
    with db.connection() as conn:
        assert [a.decision for a in list_approvals(conn, "run_a")] == [
            "approve_with_conditions",
            "approve",
        ]
