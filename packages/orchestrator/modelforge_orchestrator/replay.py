"""Replay: creating a replay run, and comparing its manifest with its original's."""

from modelforge_lineage import queue, runs
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import RunRecord
from modelforge_lineage.tracing import new_trace_id
from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_orchestrator.schemas import (
    DETERMINISTIC_REFS,
    Manifest,
    RefName,
    ReplayMismatch,
    ReplayReport,
)

__all__ = ["LLM_DERIVED_REFS", "ReplayError", "compare_refs", "create_replay"]

LLM_DERIVED_REFS: tuple[RefName, ...] = (
    "document_md",
    "document_html",
    "completeness",
    "findings",
    "responses",
)
"""The refs a ``--rerun-llm`` replay regenerates rather than reproduces."""


def compare_refs(original: Manifest, replay: Manifest, *, rerun_llm: bool = False) -> ReplayReport:
    """Compare the deterministic refs; a ref absent from both manifests is ignored.

    With ``rerun_llm`` the LLM-derived refs the original carries are listed as ``regenerated``
    and not compared; ``ok`` ignores them.
    """
    matched: list[RefName] = []
    mismatched: list[ReplayMismatch] = []
    missing: list[RefName] = []
    # not every LLM-derived ref is a deterministic one, so collect them from their own list
    regenerated: list[RefName] = (
        [n for n in LLM_DERIVED_REFS if n in original.refs] if rerun_llm else []
    )
    for name in DETERMINISTIC_REFS:
        if name not in original.refs or name in regenerated:
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
        regenerated=regenerated,
        ok=not mismatched and not missing,
    )


class ReplayError(ValueError):
    """A replay was requested for a run that has not been sealed."""


def create_replay(
    conn: Connection[DictRow],
    original_run_id: str,
    *,
    rerun_llm: bool = False,
    auto: bool = False,
) -> RunRecord:
    """Create and enqueue a replay of a `completed` or `rejected` run; call inside a transaction.

    The replay inherits the original's cost cap; ``rerun_llm`` makes its LLM nodes call the
    gateway again instead of copying the original's outputs. The replay gets its own fresh trace
    id. ``auto`` marks the `run.created` event of a replay the worker enqueued on its own.
    """
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
        cost_cap_usd=original.cost_cap_usd,
        rerun_llm=rerun_llm,
        trace_id=new_trace_id(),
        event_extra={"auto": True} if auto else None,
    )
    queue.enqueue(conn, run_id, "replay")
    return record
