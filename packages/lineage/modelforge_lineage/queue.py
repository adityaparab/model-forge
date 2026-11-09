"""The run queue: workers claim rows with ``FOR UPDATE SKIP LOCKED`` so no run is taken twice."""

from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_lineage.events import append_event
from modelforge_lineage.models import QueuedRun, QueueKind

_CLAIM_HEAD = """
update run_queue set claimed_by = %(worker)s, claimed_at = now()
where run_id = (select run_id from run_queue where claimed_by is null"""
_CLAIM_TAIL = """
                order by enqueued_at for update skip locked limit 1)
returning run_id, kind
"""
_CLAIM_ANY = _CLAIM_HEAD + _CLAIM_TAIL
_CLAIM_ONE = _CLAIM_HEAD + " and run_id = %(run_id)s" + _CLAIM_TAIL


def enqueue(conn: Connection[DictRow], run_id: str, kind: QueueKind) -> None:
    conn.execute(
        # clock_timestamp(), not now(): runs enqueued in one transaction keep their order.
        "insert into run_queue (run_id, kind, enqueued_at) values (%s, %s, clock_timestamp())"
        " on conflict (run_id) do update set kind = excluded.kind,"
        " enqueued_at = excluded.enqueued_at,"
        " claimed_by = null, claimed_at = null",
        (run_id, kind),
    )
    append_event(conn, run_id, "run.enqueued", {"kind": kind})


def claim(
    conn: Connection[DictRow], worker_id: str, *, run_id: str | None = None
) -> QueuedRun | None:
    query = _CLAIM_ANY if run_id is None else _CLAIM_ONE
    row = conn.execute(query, {"worker": worker_id, "run_id": run_id}).fetchone()
    return QueuedRun.model_validate(row) if row is not None else None


def release(conn: Connection[DictRow], run_id: str) -> None:
    conn.execute("delete from run_queue where run_id = %s", (run_id,))
