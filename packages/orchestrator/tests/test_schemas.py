from typing import get_args

from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.schemas import DatasetRef
from modelforge_orchestrator.schemas import (
    DETERMINISTIC_REFS,
    ApprovalSummary,
    Manifest,
    RefName,
    ReplayReport,
    RunState,
    merge_dicts,
)


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
    assert len(get_args(RefName)) == 29 and len(DETERMINISTIC_REFS) == 23


def test_manifest_carries_no_timestamp() -> None:
    assert "ts" not in ApprovalSummary.model_fields
    manifest = Manifest(
        run_id="run_a",
        replay_of=None,
        brief_hash="h",
        dataset_ref=DatasetRef(adapter="synthetic_credit"),
        dataset_id="d" * 64,
        seed=42,
        profile="default",
        initial_plan_ref="p" * 64,
        plan_ref="p" * 64,
        refs={},
        tool_versions={},
        approvals=[ApprovalSummary(gate="scope", decision="approve", approver="bob")],
        outcome="completed",
        core_version="0.1.0",
        orchestrator_version="0.1.0",
        events_head_hash="e" * 64,
    )

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    assert "ts" not in keys(manifest.model_dump(mode="json"))


def test_run_state_and_replay_report_new_fields() -> None:
    state = RunState(
        run_id="run_a",
        requester="alice",
        seed=42,
        brief_hash="h",
        brief_ref="b" * 64,
        dataset_ref=DatasetRef(adapter="synthetic_credit"),
    )
    assert state.rerun_llm is False
    report = ReplayReport(
        original_run_id="run_a",
        replay_run_id="run_b",
        matched=[],
        mismatched=[],
        missing=[],
        ok=True,
        regenerated=["document_md"],
    )
    assert report.regenerated == ["document_md"]
    assert ReplayReport.model_validate(report.model_dump() | {"regenerated": []}).regenerated == []
    assert "knowledge_index" in DETERMINISTIC_REFS and DETERMINISTIC_REFS[-1] == "knowledge_index"
    assert {"scope_notes", "knowledge_index"} <= set(get_args(RefName))
    assert "scope_notes" not in DETERMINISTIC_REFS
