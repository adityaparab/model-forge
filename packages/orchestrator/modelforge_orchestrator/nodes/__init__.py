"""LangGraph nodes, one module each; every node runs inside a `Step`."""

from collections.abc import Callable
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import Command
from modelforge_core.schemas import ModelingPlan
from modelforge_lineage import runs
from modelforge_lineage.store import ArtifactStore

from modelforge_orchestrator.briefs import Brief, parse_brief
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.schemas import Manifest, RunState

__all__ = ["brief_of", "invoke_node", "original_manifest", "plan_of"]


def brief_of(state: RunState, store: ArtifactStore) -> Brief:
    """The run's brief, parsed from the `brief` artifact the run was created with."""
    return parse_brief(store.get(state.brief_ref))


def plan_of(state: RunState) -> ModelingPlan:
    if state.plan is None:
        raise RuntimeError(f"run {state.run_id} has no modeling plan yet")
    return state.plan


def original_manifest(state: RunState, deps: Deps) -> Manifest:
    """The sealed manifest of the run ``state`` replays."""
    if state.replay_of is None:
        raise RuntimeError(f"run {state.run_id} is not a replay")
    with deps.lineage.connection() as conn:
        manifest_hash = runs.get_run(conn, state.replay_of).manifest_hash
    if manifest_hash is None:
        raise RuntimeError(f"run {state.replay_of} has no manifest to replay")
    return deps.store.get_model(manifest_hash, Manifest)


def invoke_node(
    node: Callable[..., Any], state: RunState, deps: Deps
) -> tuple[dict[str, Any], str | None]:
    """Run ``node`` on ``state`` outside a graph; return its update and its `Command` goto.

    ``goto`` is ``None`` for a node that returns a plain update. Callers outside the
    orchestrator drive nodes through this without importing LangGraph types.
    """
    result = node(state, Runtime(context=deps))
    if not isinstance(result, Command):
        return dict(result), None
    goto: Any = result.goto
    if not isinstance(goto, str):
        raise RuntimeError(f"node {node.__name__} routed to {goto!r}, not one node name")
    return dict(result.update or {}), goto
