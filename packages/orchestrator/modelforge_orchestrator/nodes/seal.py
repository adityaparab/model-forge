"""`seal`: write the run's manifest, finalise its inventory entry, and on replay compare refs.

The manifest carries no timestamp, so a replay that reproduces every artifact differs from its
original only in run ids, the event-log head and anything a human decided differently.
"""

from datetime import UTC, datetime
from typing import Any, Literal

import modelforge_core
from langgraph.runtime import Runtime
from modelforge_lineage import inventory, runs
from modelforge_lineage.approvals import list_approvals
from modelforge_lineage.events import append_event, list_events
from modelforge_lineage.steps import list_artifacts, list_tool_calls

import modelforge_orchestrator
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import original_manifest
from modelforge_orchestrator.replay import compare_refs
from modelforge_orchestrator.schemas import ApprovalSummary, Manifest, RunState
from modelforge_orchestrator.steps import Step

__all__ = ["build_manifest", "seal"]

Outcome = Literal["completed", "rejected"]


def build_manifest(
    state: RunState, deps: Deps, *, initial_plan_ref: str, outcome: Outcome
) -> Manifest:
    with deps.lineage.connection() as conn:
        run = runs.get_run(conn, state.run_id)
        tool_calls = list_tool_calls(conn, state.run_id)
        approvals = list_approvals(conn, state.run_id)
        events = list_events(conn, state.run_id)
    if run.plan_ref is None:
        raise RuntimeError(f"run {state.run_id} has no modeling plan to seal")
    return Manifest(
        run_id=state.run_id,
        replay_of=state.replay_of,
        brief_hash=state.brief_hash,
        dataset_ref=state.dataset_ref,
        # a run rejected at the scope gate is sealed before `profile` loads the dataset
        dataset_id=run.dataset_id or "",
        seed=state.seed,
        profile=state.profile,
        initial_plan_ref=initial_plan_ref,
        plan_ref=run.plan_ref,
        refs=state.refs,
        tool_versions={f"{c.server}/{c.tool}": c.version for c in tool_calls},
        approvals=[
            ApprovalSummary(gate=a.gate, decision=a.decision, approver=a.approver)
            for a in approvals
        ],
        outcome=outcome,
        core_version=modelforge_core.__version__,
        orchestrator_version=modelforge_orchestrator.__version__,
        events_head_hash=events[-1].hash,
    )


def _initial_plan_ref(deps: Deps, run_id: str) -> str:
    with deps.lineage.connection() as conn:
        plans = [a.id for a in list_artifacts(conn, run_id) if a.kind == "modeling_plan"]
    if not plans:
        raise RuntimeError(f"run {run_id} has no modeling plan artifact")
    return plans[0]


def _finalise_inventory(state: RunState, deps: Deps) -> None:
    signoff = state.gates.get("signoff")
    status = "rejected"
    if signoff is not None and signoff.decision == "approve":
        status = "approved"
    elif signoff is not None and signoff.decision == "approve_with_conditions":
        status = "approved_with_conditions"
    champion = state.refs.get("champion")
    validation = state.refs.get("validation")
    with deps.lineage.transaction() as conn:
        entry = inventory.get_for_run(conn, state.run_id)
        if entry is None:
            return
        inventory.finalise(
            conn,
            entry.model_id,
            status=status,
            champion_ref=champion.id if champion is not None else None,
            validation_summary=(
                {
                    "overall": validation.summary["overall"],
                    "statuses": validation.summary["statuses"],
                }
                if validation is not None
                else None
            ),
            approver=signoff.approver if signoff is not None else None,
            conditions=(signoff.conditions or None) if signoff is not None else None,
            approved_at=datetime.now(UTC) if status != "rejected" else None,
        )


def seal(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "seal") as step:
        outcome: Outcome = "rejected" if state.status == "rejected" else "completed"
        manifest = build_manifest(
            state, deps, initial_plan_ref=_initial_plan_ref(deps, state.run_id), outcome=outcome
        )
        manifest_ref = step.store(manifest, kind="manifest")
        with deps.lineage.transaction() as conn:
            runs.set_manifest_hash(conn, state.run_id, manifest_ref.id)
        step.event("run.sealed", {"manifest_id": manifest_ref.id, "outcome": outcome})
        refs = {"manifest": manifest_ref}
        if state.replay_of is None:
            _finalise_inventory(state, deps)
        else:
            report = compare_refs(original_manifest(state, deps), manifest)
            refs["replay_report"] = step.store(
                report,
                kind="replay_report",
                summary={
                    "ok": report.ok,
                    "n_mismatched": len(report.mismatched),
                    "n_missing": len(report.missing),
                },
            )
            payload = {"ok": report.ok, "n_mismatched": len(report.mismatched)}
            step.event("replay.completed", payload)
            with deps.lineage.transaction() as conn:
                append_event(conn, state.replay_of, "replay.completed", payload)
        return step.complete({"refs": refs, "status": outcome})
