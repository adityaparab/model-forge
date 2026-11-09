"""Replay: comparing a replay run's manifest with its original's."""

from modelforge_orchestrator.schemas import (
    DETERMINISTIC_REFS,
    Manifest,
    RefName,
    ReplayMismatch,
    ReplayReport,
)

__all__ = ["compare_refs"]


def compare_refs(original: Manifest, replay: Manifest) -> ReplayReport:
    """Compare the deterministic refs; a ref absent from both manifests is ignored."""
    matched: list[RefName] = []
    mismatched: list[ReplayMismatch] = []
    missing: list[RefName] = []
    for name in DETERMINISTIC_REFS:
        if name not in original.refs:
            continue
        if name not in replay.refs:
            missing.append(name)
            continue
        original_id, replay_id = original.refs[name].id, replay.refs[name].id
        if original_id == replay_id:
            matched.append(name)
        else:
            mismatched.append(
                ReplayMismatch(ref=name, original_id=original_id, replay_id=replay_id)
            )
    return ReplayReport(
        original_run_id=original.run_id,
        replay_run_id=replay.run_id,
        matched=matched,
        mismatched=mismatched,
        missing=missing,
        ok=not mismatched and not missing,
    )
