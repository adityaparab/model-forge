import pytest
from langgraph.runtime import Runtime
from modelforge_lineage.approvals import record_approval
from modelforge_lineage.events import list_events
from modelforge_lineage.models import GateDecision
from modelforge_lineage.runs import get_run, set_status
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import gates
from modelforge_orchestrator.nodes.gates import gate_model, gate_scope, gate_summary
from modelforge_orchestrator.schemas import RunState

from .conftest import REPO, make_run


@pytest.mark.db
def test_gate_scope_edit_stores_plan_and_routes(
    deps: Deps, runtime: Runtime[Deps], scoped_state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert scoped_state.plan is not None
    policy = scoped_state.plan.feature_policy.model_copy(update={"max_features": 5})
    edited = scoped_state.plan.model_copy(update={"feature_policy": policy})
    monkeypatch.setattr(
        gates,
        "interrupt",
        lambda value: GateDecision(
            gate="scope", decision="edit", approver="alice", plan=edited
        ).model_dump(mode="json"),
    )
    cmd = gate_scope(scoped_state, runtime)
    assert cmd.update is not None
    assert cmd.goto == "profile"
    assert cmd.update["plan"].feature_policy.max_features == 5
    assert cmd.update["gates"]["scope"].decision == "edit"
    with deps.lineage.connection() as conn:
        run = get_run(conn, scoped_state.run_id)
        assert run.plan_ref == deps.store.put_model(edited, kind="modeling_plan").id
        assert [e.type for e in list_events(conn, scoped_state.run_id)][-4:] == [
            "step.started",
            "gate.applied",
            "artifact.stored",
            "step.finished",
        ]


@pytest.mark.db
def test_gate_model_adjust_and_reject(
    deps: Deps, runtime: Runtime[Deps], validated_state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gates,
        "interrupt",
        lambda value: GateDecision(
            gate="model",
            decision="adjust",
            approver="bob",
            plan=validated_state.plan,
            adjust_target="build",
        ).model_dump(mode="json"),
    )
    cmd = gate_model(validated_state, runtime)
    assert cmd.update is not None
    assert cmd.goto == "build" and cmd.update["counters"].build_loops == 1
    monkeypatch.setattr(
        gates,
        "interrupt",
        lambda value: GateDecision(gate="model", decision="reject", approver="bob").model_dump(
            mode="json"
        ),
    )
    cmd = gate_model(validated_state, runtime)
    assert cmd.update is not None
    assert cmd.goto == "seal" and cmd.update["status"] == "rejected"


@pytest.mark.db
def test_gate_summary_contents(deps: Deps, validated_state: RunState) -> None:
    s = gate_summary("model", validated_state, deps.store)
    assert set(s) == {"validation", "selected", "build_loops"} and "overall" in s["validation"]


@pytest.mark.db
def test_replay_mode_uses_recorded_approvals_without_interrupt(
    deps: Deps, runtime: Runtime[Deps], scoped_state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gates, "interrupt", lambda value: pytest.fail("interrupt must not be called in replay mode")
    )
    original = scoped_state.run_id
    with deps.lineage.transaction() as conn:
        set_status(conn, original, "awaiting_scope")
        record_approval(
            conn,
            original,
            GateDecision(gate="scope", decision="approve", approver="alice"),
            current_plan=scoped_state.plan,
        )
    replay_state = make_run(
        deps.lineage.db,
        deps.store,
        REPO / "examples/briefs/synthetic.yaml",
        requester="replay",
        replay_of=original,
    ).model_copy(update={"plan": scoped_state.plan})
    assert gate_scope(replay_state, runtime).goto == "profile"


@pytest.mark.db
def test_gate_scope_summary_shows_rationale(deps: Deps, scoped_state: RunState) -> None:
    summary = gate_summary("scope", scoped_state, deps.store)
    assert set(summary) == {"plan", "rationale", "assumptions"}
    assert summary["rationale"] == scoped_state.refs["scope_notes"].summary["rationale"]
    assert summary["assumptions"]
