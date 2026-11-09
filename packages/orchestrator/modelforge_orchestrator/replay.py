"""Replay: creating a replay run, and comparing its manifest with its original's."""

from modelforge_lineage import queue, runs
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import RunRecord
from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_orchestrator.schemas import (
    DETERMINISTIC_REFS,
    Manifest,
    RefName,
    ReplayMismatch,
    ReplayReport,
)

__all__ = ["ReplayError", "compare_refs", "create_replay"]


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


class ReplayError(ValueError):
    """A replay was requested for a run that has not been sealed."""


def create_replay(conn: Connection[DictRow], original_run_id: str) -> RunRecord:
    """Create and enqueue a replay of a `completed` or `rejected` run; call inside a transaction."""
    original = runs.get_run(conn, original_run_id)
    if original.status not in ("completed", "rejected"):
        raise ReplayError(
            f"run {original.id} is {original.status!r}; only a completed or rejected run replays"
        )
    run_id = new_id("run")
    record = runs.create_run(
        conn,
        run_id=run_id,
        requester="replay",
        profile=original.profile,
        brief_hash=original.brief_hash,
        brief_ref=original.brief_ref,
        dataset_ref=original.dataset_ref,
        seed=original.seed,
        replay_of=original.id,
    )
    queue.enqueue(conn, run_id, "replay")
    return record
