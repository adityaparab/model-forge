"""LangGraph nodes, one module each; every node runs inside a `Step`."""

from importlib.resources import files
from typing import Any

import yaml
from modelforge_core.schemas import ModelingPlan
from modelforge_lineage import runs
from modelforge_lineage.store import ArtifactStore

from modelforge_orchestrator.briefs import Brief, parse_brief
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.schemas import Manifest, RunState

__all__ = ["brief_of", "load_fixture", "original_manifest", "plan_of"]


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


def load_fixture(name: str) -> dict[str, Any]:
    """A YAML mapping from the package's ``fixtures`` directory."""
    text = (files("modelforge_orchestrator") / "fixtures" / name).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise RuntimeError(f"fixture {name} is not a mapping")
    return data
