"""The three human gates as resumable LangGraph interrupts.

A gate node first obtains its decision (an `interrupt`, or on replay the original run's recorded
approval) and only then records anything: on resume LangGraph re-runs the node from its first
line, so nothing before `interrupt` may write to lineage. The worker appends `gate.requested`.
"""

from typing import Any, Literal

from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from modelforge_core.schemas import ModelingPlan
from modelforge_lineage import runs
from modelforge_lineage.approvals import list_approvals
from modelforge_lineage.models import GateDecision, GateName
from modelforge_lineage.store import ArtifactStore

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import plan_of
from modelforge_orchestrator.schemas import GatePrompt, RunState
from modelforge_orchestrator.steps import Step

__all__ = ["decide", "gate_model", "gate_scope", "gate_signoff", "gate_summary", "interrupt"]


def gate_summary(gate: GateName, state: RunState, store: ArtifactStore) -> dict[str, Any]:
    """What the approver sees at ``gate``; a pure read of the state and the store."""
    del store  # every summary is read from the refs' own summaries
    refs = state.refs
    if gate == "scope":
        summary: dict[str, Any] = {"plan": plan_of(state).model_dump(mode="json")}
        notes = refs.get("scope_notes")
        if notes is not None:  # SP2 runs and their replays have no scope notes
            summary["rationale"] = notes.summary["rationale"]
            summary["assumptions"] = notes.summary["assumptions"]
        return summary
    if gate == "model":
        return {
            "validation": refs["validation"].summary,
            "selected": refs["selection"].summary["selected"],
            "build_loops": state.counters.build_loops,
        }
    summary = {
        "completeness": refs["completeness"].summary,
        "findings": refs["findings"].summary,
        "validation_overall": refs["validation"].summary["overall"],
    }
    sections = refs.get("document_sections")
    if sections is not None:
        summary["document"] = sections.summary
    return summary


def decide(gate: GateName, state: RunState, deps: Deps) -> GateDecision:
    """The decision at ``gate``: the original run's on replay, else the human's via `interrupt`."""
    if state.replay_of is not None:
        with deps.lineage.connection() as conn:
            approvals = list_approvals(conn, state.replay_of, gate=gate)
        n = state.counters.build_loops if gate == "model" else 0
        if n >= len(approvals):
            raise RuntimeError(
                f"run {state.replay_of} has no recorded approval #{n} at gate {gate} to replay"
            )
        return approvals[n].payload
    prompt = GatePrompt(
        gate=gate, run_id=state.run_id, summary=gate_summary(gate, state, deps.store)
    )
    return GateDecision.model_validate(interrupt(prompt.model_dump(mode="json")))


def _applied(step: Step, decision: GateDecision) -> None:
    step.event(
        "gate.applied",
        {"gate": decision.gate, "decision": decision.decision, "approver": decision.approver},
    )


def _store_plan(step: Step, deps: Deps, state: RunState, plan: ModelingPlan) -> None:
    ref = step.store(plan, kind="modeling_plan")
    with deps.lineage.transaction() as conn:
        runs.set_plan_ref(conn, state.run_id, ref.id)


def _require_plan(decision: GateDecision) -> ModelingPlan:
    if decision.plan is None:
        raise RuntimeError(f"{decision.decision!r} at gate {decision.gate} carries no plan")
    return decision.plan


def gate_scope(state: RunState, runtime: Runtime[Deps]) -> Command[Literal["profile", "seal"]]:
    deps = runtime.context
    decision = decide("scope", state, deps)
    with Step(deps, state, "gate_scope") as step:
        _applied(step, decision)
        update: dict[str, Any] = {"gates": {"scope": decision}}
        goto: Literal["profile", "seal"] = "profile"
        if decision.decision == "edit":
            plan = _require_plan(decision)
            _store_plan(step, deps, state, plan)
            update["plan"] = plan
        elif decision.decision == "reject":
            update["status"] = "rejected"
            goto = "seal"
        step.complete(update)
    return Command(goto=goto, update=update)


def gate_model(
    state: RunState, runtime: Runtime[Deps]
) -> Command[Literal["document", "features", "build", "seal"]]:
    deps = runtime.context
    decision = decide("model", state, deps)
    with Step(deps, state, "gate_model") as step:
        _applied(step, decision)
        update: dict[str, Any] = {"gates": {"model": decision}}
        goto: Literal["document", "features", "build", "seal"] = "document"
        if decision.decision == "adjust":
            plan = _require_plan(decision)
            if decision.adjust_target is None:
                raise RuntimeError("'adjust' at gate model carries no adjust_target")
            _store_plan(step, deps, state, plan)
            update["plan"] = plan
            # Counters are replaced whole, so copy them to keep every other counter.
            update["counters"] = state.counters.model_copy(
                update={"build_loops": state.counters.build_loops + 1}
            )
            goto = decision.adjust_target
        elif decision.decision == "reject":
            update["status"] = "rejected"
            goto = "seal"
        step.complete(update)
    return Command(goto=goto, update=update)


def gate_signoff(state: RunState, runtime: Runtime[Deps]) -> Command[Literal["seal"]]:
    deps = runtime.context
    decision = decide("signoff", state, deps)
    with Step(deps, state, "gate_signoff") as step:
        _applied(step, decision)
        status = "rejected" if decision.decision == "reject" else "completed"
        update: dict[str, Any] = {"gates": {"signoff": decision}, "status": status}
        step.complete(update)
    return Command(goto="seal", update=update)
