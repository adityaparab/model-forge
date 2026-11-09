"""LangGraph nodes, one module each; every node runs inside a `Step`."""

from modelforge_core.schemas import ModelingPlan
from modelforge_lineage.store import ArtifactStore

from modelforge_orchestrator.briefs import Brief, parse_brief
from modelforge_orchestrator.schemas import RunState

__all__ = ["brief_of", "plan_of"]


def brief_of(state: RunState, store: ArtifactStore) -> Brief:
    """The run's brief, parsed from the `brief` artifact the run was created with."""
    return parse_brief(store.get(state.brief_ref))


def plan_of(state: RunState) -> ModelingPlan:
    if state.plan is None:
        raise RuntimeError(f"run {state.run_id} has no modeling plan yet")
    return state.plan
