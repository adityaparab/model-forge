"""Repository for the ``runs`` table; lifecycle changes are written to the event log."""

from collections.abc import Collection, Mapping
from typing import Any

from psycopg import Connection, sql
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from modelforge_lineage.events import append_event
from modelforge_lineage.models import RunRecord, RunStatus


class RunNotFound(KeyError):  # noqa: N818 - name fixed by the lineage interface
    pass


def create_run(
    conn: Connection[DictRow],
    *,
    run_id: str,
    requester: str,
    profile: str,
    brief_hash: str,
    brief_ref: str,
    dataset_ref: str,
    seed: int,
    replay_of: str | None = None,
    cost_cap_usd: float = 10.0,
    rerun_llm: bool = False,
    trace_id: str | None = None,
    event_extra: Mapping[str, Any] | None = None,
) -> RunRecord:
    """Insert a queued run and append its `run.created` event; call inside a transaction.

    ``event_extra`` is merged into the event payload (the record) and is not stored on the run.
    """
    row = conn.execute(
        "insert into runs (id, requester, status, profile, brief_hash, brief_ref, dataset_ref,"
        " seed, replay_of, cost_usd, cost_cap_usd, rerun_llm, trace_id)"
        " values (%s, %s, 'queued', %s, %s, %s, %s, %s, %s, 0, %s, %s, %s) returning *",
        (
            run_id,
            requester,
            profile,
            brief_hash,
            brief_ref,
            dataset_ref,
            seed,
            replay_of,
            cost_cap_usd,
            rerun_llm,
            trace_id,
        ),
    ).fetchone()
    assert row is not None
    record = RunRecord.model_validate(row)
    append_event(
        conn, run_id, "run.created", {**record.model_dump(mode="json"), **(event_extra or {})}
    )
    return record


def get_run(conn: Connection[DictRow], run_id: str) -> RunRecord:
    row = conn.execute("select * from runs where id = %s", (run_id,)).fetchone()
    if row is None:
        raise RunNotFound(run_id)
    return RunRecord.model_validate(row)


def _run_filters(
    status: str | None, profile: str | None, requester: str | None, include_replays: bool
) -> tuple[sql.Composable, list[object]]:
    clauses: list[sql.Composable] = []
    params: list[object] = []
    for column, value in (("status", status), ("profile", profile), ("requester", requester)):
        if value is not None:
            clauses.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
            params.append(value)
    if not include_replays:
        clauses.append(sql.SQL("replay_of is null"))
    if not clauses:
        return sql.SQL(""), params
    return sql.SQL(" where ") + sql.SQL(" and ").join(clauses), params


def list_runs(
    conn: Connection[DictRow],
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    profile: str | None = None,
    requester: str | None = None,
    include_replays: bool = True,
) -> list[RunRecord]:
    where, params = _run_filters(status, profile, requester, include_replays)
    query = sql.SQL("select * from runs{} order by created_at desc, id limit %s offset %s").format(
        where
    )
    rows = conn.execute(query, (*params, limit, offset))
    return [RunRecord.model_validate(r) for r in rows]


def count_runs(
    conn: Connection[DictRow],
    *,
    status: str | None = None,
    profile: str | None = None,
    requester: str | None = None,
    include_replays: bool = True,
) -> int:
    where, params = _run_filters(status, profile, requester, include_replays)
    row = conn.execute(sql.SQL("select count(*) as n from runs{}").format(where), params).fetchone()
    assert row is not None
    return int(row["n"])


def list_replays(conn: Connection[DictRow], run_id: str) -> list[RunRecord]:
    rows = conn.execute(
        "select * from runs where replay_of = %s order by created_at, id", (run_id,)
    )
    return [RunRecord.model_validate(r) for r in rows]


def set_status(conn: Connection[DictRow], run_id: str, status: RunStatus) -> None:
    row = conn.execute("select status from runs where id = %s for update", (run_id,)).fetchone()
    if row is None:
        raise RunNotFound(run_id)
    old = row["status"]
    conn.execute("update runs set status = %s, updated_at = now() where id = %s", (status, run_id))
    append_event(conn, run_id, "run.status_changed", {"from": old, "to": status})


def transition(
    conn: Connection[DictRow],
    run_id: str,
    *,
    from_statuses: Collection[RunStatus],
    to: RunStatus,
) -> bool:
    """Move the run to ``to`` only if its status is one of ``from_statuses``; whether it moved.

    One conditional update, so a concurrent status change is never overwritten; on a move the
    `run.status_changed` event carries the real old status. `RunNotFound` for an unknown run.
    Call inside a transaction.
    """
    row = conn.execute(
        "with old as (select id, status from runs where id = %s for update)"
        " update runs set status = %s, updated_at = now() from old"
        " where runs.id = old.id and old.status = any(%s) returning old.status as old_status",
        (run_id, to, list(from_statuses)),
    ).fetchone()
    if row is not None:
        append_event(conn, run_id, "run.status_changed", {"from": row["old_status"], "to": to})
        return True
    if conn.execute("select 1 from runs where id = %s", (run_id,)).fetchone() is None:
        raise RunNotFound(run_id)
    return False


def _set_column(conn: Connection[DictRow], run_id: str, column: str, value: object) -> None:
    query = sql.SQL("update runs set {} = %s, updated_at = now() where id = %s").format(
        sql.Identifier(column)
    )
    cur = conn.execute(query, (value, run_id))
    if cur.rowcount == 0:
        raise RunNotFound(run_id)


def request_pause(conn: Connection[DictRow], run_id: str) -> bool:
    """Set the pause flag if the run is ``running``; whether it was set.

    One conditional update, so a run that parks or finishes concurrently is never flagged after
    its worker cleared the flag. `RunNotFound` for an unknown run.
    """
    cur = conn.execute(
        "update runs set pause_requested = true, updated_at = now()"
        " where id = %s and status = 'running'",
        (run_id,),
    )
    if cur.rowcount == 1:
        return True
    if conn.execute("select 1 from runs where id = %s", (run_id,)).fetchone() is None:
        raise RunNotFound(run_id)
    return False


def clear_pause(conn: Connection[DictRow], run_id: str) -> None:
    """Clear the pause flag whatever the run's status; `RunNotFound` for an unknown run."""
    _set_column(conn, run_id, "pause_requested", False)


def pause_requested(conn: Connection[DictRow], run_id: str) -> bool:
    """Whether a pause is pending for the run; `RunNotFound` for an unknown run."""
    row = conn.execute("select pause_requested from runs where id = %s", (run_id,)).fetchone()
    if row is None:
        raise RunNotFound(run_id)
    return bool(row["pause_requested"])


def add_cost(conn: Connection[DictRow], run_id: str, usd: float) -> float:
    """Add ``usd`` to the run's accumulated cost and return the new total."""
    row = conn.execute(
        "update runs set cost_usd = cost_usd + %s, updated_at = now() where id = %s"
        " returning cost_usd",
        (usd, run_id),
    ).fetchone()
    if row is None:
        raise RunNotFound(run_id)
    return float(row["cost_usd"])


def set_cost_cap(conn: Connection[DictRow], run_id: str, usd: float) -> None:
    _set_column(conn, run_id, "cost_cap_usd", usd)


def set_dataset_id(conn: Connection[DictRow], run_id: str, value: str) -> None:
    _set_column(conn, run_id, "dataset_id", value)


def set_plan_ref(conn: Connection[DictRow], run_id: str, value: str) -> None:
    _set_column(conn, run_id, "plan_ref", value)


def set_manifest_hash(conn: Connection[DictRow], run_id: str, value: str) -> None:
    _set_column(conn, run_id, "manifest_hash", value)


def set_error(conn: Connection[DictRow], run_id: str, error: dict[str, Any] | None) -> None:
    _set_column(conn, run_id, "error", Jsonb(error) if error is not None else None)
