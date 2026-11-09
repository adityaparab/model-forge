import pytest
from langgraph.runtime import Runtime
from modelforge_lineage.db import Database
from modelforge_lineage.inventory import get_for_run
from modelforge_lineage.runs import get_run
from modelforge_lineage.steps import list_steps
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.briefs import BriefError
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.intake import intake
from modelforge_orchestrator.nodes.profile import profile
from modelforge_orchestrator.nodes.scope import scope
from modelforge_orchestrator.nodes.split import split
from modelforge_orchestrator.schemas import RunState

from .conftest import make_run


@pytest.mark.db
def test_intake_scope_profile_split_on_synthetic(
    deps: Deps, runtime: Runtime[Deps], synthetic_state: RunState
) -> None:
    state = synthetic_state
    intake(state, runtime)
    with deps.lineage.connection() as conn:
        entry = get_for_run(conn, state.run_id)
        assert entry is not None and entry.status == "draft"
    state = state.model_copy(update=scope(state, runtime))
    assert state.plan is not None and state.plan.model_name == "synthetic_pd"
    with deps.lineage.connection() as conn:
        assert get_run(conn, state.run_id).plan_ref is not None
    state = state.model_copy(update={"refs": profile(state, runtime)["refs"]})
    assert {"dataset", "dictionary", "profile", "leakage", "psi"} <= set(state.refs)
    assert state.refs["psi"].summary["max_psi"] < 0.1
    with deps.lineage.connection() as conn:
        assert get_run(conn, state.run_id).dataset_id == state.refs["dataset"].id
    state = state.model_copy(update={"refs": {**state.refs, **split(state, runtime)["refs"]}})
    assert state.refs["split"].summary["oot_available"] is True
    with deps.lineage.connection() as conn:
        nodes = [s.node for s in list_steps(conn, state.run_id)]
    assert nodes == ["intake", "scope", "profile", "split"]


@pytest.mark.db
def test_profile_skips_psi_and_split_is_random_without_oot(
    deps: Deps, runtime: Runtime[Deps], state_no_oot: RunState
) -> None:
    state = state_no_oot.model_copy(update=scope(state_no_oot, runtime))
    refs = profile(state, runtime)["refs"]
    assert "psi" not in refs
    state = state.model_copy(update={"refs": refs})
    assert split(state, runtime)["refs"]["split"].summary["oot_available"] is False


@pytest.mark.db
def test_intake_is_idempotent(
    deps: Deps, runtime: Runtime[Deps], synthetic_state: RunState
) -> None:
    intake(synthetic_state, runtime)
    intake(synthetic_state, runtime)
    with deps.lineage.connection() as conn:
        rows = conn.execute(
            "select model_id, status from model_inventory where run_id = %s",
            (synthetic_state.run_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "draft" and rows[0]["model_id"].endswith("@v1")


@pytest.mark.db
def test_intake_rejects_malformed_brief(
    deps: Deps, runtime: Runtime[Deps], db: Database, store: ArtifactStore
) -> None:
    state = make_run(db, store, brief_bytes=b"model_name: only\n")
    with pytest.raises(BriefError):
        intake(state, runtime)
    with deps.lineage.connection() as conn:
        assert list_steps(conn, state.run_id)[0].status == "failed"
