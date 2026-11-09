"""Hash-chained, append-only event log: one chain per run, starting at ``GENESIS``."""

from datetime import UTC, datetime
from typing import Any

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from modelforge_lineage.canonical import canonical_json, sha256_hex
from modelforge_lineage.models import ChainReport, EventRecord

GENESIS = "0" * 64

_SELECT_EVENTS = (
    "select id, run_id, step_id, ts, type, payload, prev_hash, hash"
    " from events where run_id = %s order by id"
)


def event_hash(prev_hash: str, body: dict[str, Any]) -> str:
    return sha256_hex(prev_hash + canonical_json(body))


def _ts_string(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


def _body(type: str, ts: str, step_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": type, "ts": ts, "step_id": step_id, "payload": payload}


def _row_to_event(row: DictRow) -> EventRecord:
    return EventRecord.model_validate({**row, "ts": _ts_string(row["ts"])})


def append_event(
    conn: Connection[DictRow],
    run_id: str,
    type: str,
    payload: dict[str, Any],
    *,
    step_id: str | None = None,
) -> EventRecord:
    """Append one event to the run's chain; the caller must hold an open transaction."""
    from modelforge_lineage.runs import RunNotFound  # runs imports this module

    if conn.info.transaction_status == TransactionStatus.IDLE:
        raise RuntimeError("append_event requires an open transaction")
    if conn.execute("select id from runs where id = %s for update", (run_id,)).fetchone() is None:
        raise RunNotFound(run_id)
    last = conn.execute(
        "select hash from events where run_id = %s order by id desc limit 1", (run_id,)
    ).fetchone()
    prev_hash = last["hash"] if last is not None else GENESIS
    now = datetime.now(UTC)
    ts = now.isoformat(timespec="microseconds")
    digest = event_hash(prev_hash, _body(type, ts, step_id, payload))
    row = conn.execute(
        "insert into events (run_id, step_id, ts, type, payload, prev_hash, hash)"
        " values (%s, %s, %s, %s, %s, %s, %s) returning id",
        (run_id, step_id, now, type, Jsonb(payload), prev_hash, digest),
    ).fetchone()
    assert row is not None
    return EventRecord(
        id=row["id"],
        run_id=run_id,
        step_id=step_id,
        ts=ts,
        type=type,
        payload=payload,
        prev_hash=prev_hash,
        hash=digest,
    )


def list_events(conn: Connection[DictRow], run_id: str) -> list[EventRecord]:
    return [_row_to_event(r) for r in conn.execute(_SELECT_EVENTS, (run_id,))]


def verify_chain(conn: Connection[DictRow], run_id: str) -> ChainReport:
    """Recompute every hash from ``GENESIS``; report the first event that breaks the chain."""
    events = list_events(conn, run_id)
    running = GENESIS
    first_bad_id: int | None = None
    for event in events:
        expected = event_hash(running, _body(event.type, event.ts, event.step_id, event.payload))
        if event.prev_hash != running or event.hash != expected:
            first_bad_id = event.id
            break
        running = event.hash
    return ChainReport(
        run_id=run_id, events=len(events), ok=first_bad_id is None, first_bad_id=first_bad_id
    )
