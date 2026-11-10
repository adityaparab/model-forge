"""`intake`: validate the brief and, for a new run, register the model as a draft."""

from typing import Any

from langgraph.runtime import Runtime
from modelforge_lineage import inventory

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import brief_of
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.steps import Step


def intake(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "intake") as step:
        brief = brief_of(state, deps.store)
        if state.replay_of is None:
            with deps.lineage.transaction() as conn:
                # Idempotent: a re-run of intake for this run keeps its existing entry.
                if inventory.get_for_run(conn, state.run_id) is None:
                    inventory.create_stub(
                        conn,
                        run_id=state.run_id,
                        name=brief.model_name,
                        owner=state.requester,
                        intended_use=brief.intended_use,
                        tier=brief.tier,
                    )
        step.event(
            "intake.validated",
            {"model_name": brief.model_name, "dataset_ref": state.dataset_ref.as_string()},
        )
        return step.complete({"max_remediation_rounds": brief.remediation_rounds})
