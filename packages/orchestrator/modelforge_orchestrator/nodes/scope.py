"""`scope`: the initial modeling plan, from the brief or, on replay, from the original run."""

from typing import Any

from langgraph.runtime import Runtime
from modelforge_core.schemas import ModelingPlan
from modelforge_lineage import runs

from modelforge_orchestrator.briefs import plan_from_brief
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import brief_of
from modelforge_orchestrator.schemas import Manifest, RunState
from modelforge_orchestrator.steps import Step


def _original_plan(deps: Deps, replay_of: str) -> ModelingPlan:
    with deps.lineage.connection() as conn:
        original = runs.get_run(conn, replay_of)
    if original.manifest_hash is None:
        raise RuntimeError(f"run {replay_of} has no manifest to replay")
    manifest = deps.store.get_model(original.manifest_hash, Manifest)
    return deps.store.get_model(manifest.initial_plan_ref, ModelingPlan)


def scope(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "scope") as step:
        if state.replay_of is None:
            plan = plan_from_brief(brief_of(state, deps.store))
        else:
            plan = _original_plan(deps, state.replay_of)
        ref = step.store(plan, kind="modeling_plan")
        with deps.lineage.transaction() as conn:
            runs.set_plan_ref(conn, state.run_id, ref.id)
        return step.complete({"plan": plan})
