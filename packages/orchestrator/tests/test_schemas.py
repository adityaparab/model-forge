from typing import get_args

from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.schemas import DatasetRef
from modelforge_orchestrator.schemas import DETERMINISTIC_REFS, RefName, RunState, merge_dicts


def test_run_state_round_trips_and_merges() -> None:
    state = RunState(
        run_id="run_a",
        requester="alice",
        seed=42,
        brief_hash="h",
        brief_ref="b" * 64,
        dataset_ref=DatasetRef(adapter="synthetic_credit"),
    )
    assert RunState.model_validate_json(state.model_dump_json()) == state
    ref = ArtifactRef(id="a" * 64, kind="profile", media_type="application/json", size_bytes=1)
    assert merge_dicts({"profile": ref}, {"split": ref}) == {"profile": ref, "split": ref}
    assert set(DETERMINISTIC_REFS) < set(get_args(RefName))
    assert "document_sections" not in DETERMINISTIC_REFS
    assert len(get_args(RefName)) == 27 and len(DETERMINISTIC_REFS) == 22
