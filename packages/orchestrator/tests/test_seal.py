from typing import Any

import pytest
from langgraph.runtime import Runtime
from modelforge_lineage.events import list_events
from modelforge_lineage.inventory import get_for_run
from modelforge_lineage.models import ArtifactRef
from modelforge_lineage.runs import get_run
from modelforge_mcp.schemas import DatasetRef
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.seal import seal
from modelforge_orchestrator.replay import compare_refs
from modelforge_orchestrator.schemas import Manifest, ReplayMismatch, RunState


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


@pytest.mark.db
def test_seal_writes_manifest_and_finalises_inventory(
    deps: Deps, runtime: Runtime[Deps], signed_off_state: RunState
) -> None:
    update = seal(signed_off_state, runtime)
    manifest = deps.store.get_model(update["refs"]["manifest"].id, Manifest)
    with deps.lineage.connection() as conn:
        run = get_run(conn, signed_off_state.run_id)
        entry = get_for_run(conn, run.id)
        events = list_events(conn, run.id)
    assert run.manifest_hash == update["refs"]["manifest"].id and manifest.run_id == run.id
    assert manifest.outcome == "completed"
    assert set(manifest.tool_versions) >= {
        "credit-modeling/score",
        "validation/evaluate_thresholds",
    }
    assert "ts" not in _keys(manifest.model_dump(mode="json"))
    assert entry is not None
    assert entry.status == "approved_with_conditions"
    assert entry.champion_ref == signed_off_state.refs["champion"].id
    assert entry.validation_summary is not None
    assert entry.validation_summary["overall"] in ("pass", "warn")
    assert any(e.type == "run.sealed" for e in events)
    assert manifest.events_head_hash in {e.hash for e in events}


def _ref(c: str) -> ArtifactRef:
    return ArtifactRef(id=c * 64, kind="k", media_type="m", size_bytes=1)


def test_compare_refs() -> None:
    base: dict[str, Any] = {
        "brief_hash": "h",
        "dataset_ref": DatasetRef(adapter="synthetic_credit"),
        "dataset_id": "d",
        "seed": 42,
        "profile": "default",
        "initial_plan_ref": "p",
        "plan_ref": "p",
        "tool_versions": {},
        "approvals": [],
        "outcome": "completed",
        "core_version": "0",
        "orchestrator_version": "0",
        "events_head_hash": "e",
    }
    original = Manifest(
        **base,
        run_id="r",
        replay_of=None,
        refs={"profile": _ref("a"), "split": _ref("b"), "document_md": _ref("c")},
    )
    replay = Manifest(
        **base, run_id="r2", replay_of="r", refs={"profile": _ref("a"), "split": _ref("x")}
    )
    report = compare_refs(original, replay)
    assert report.matched == ["profile"]
    assert report.mismatched == [
        ReplayMismatch(ref="split", original_id="b" * 64, replay_id="x" * 64)
    ]
    assert report.missing == ["document_md"] and report.ok is False
