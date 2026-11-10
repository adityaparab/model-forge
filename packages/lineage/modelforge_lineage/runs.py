"""Repository for the ``runs`` table; lifecycle changes are written to the event log."""

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
) -> RunRecord:
    row = conn.execute(
        "insert into runs (id, requester, status, profile, brief_hash, brief_ref, dataset_ref,"
        " seed, replay_of, cost_usd, cost_cap_usd, rerun_llm)"
        " values (%s, %s, 'queued', %s, %s, %s, %s, %s, %s, 0, %s, %s) returning *",
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
        ),
    ).fetchone()
    assert row is not None
    record = RunRecord.model_validate(row)
    append_event(conn, run_id, "run.created", record.model_dump(mode="json"))
    return record


def get_run(conn: Connection[DictRow], run_id: str) -> RunRecord:
    row = conn.execute("select * from runs where id = %s", (run_id,)).fetchone()
    if row is None:
        raise RunNotFound(run_id)
    return RunRecord.model_validate(row)


def list_runs(conn: Connection[DictRow], *, limit: int = 50) -> list[RunRecord]:
    rows = conn.execute("select * from runs order by created_at desc, id limit %s", (limit,))
    return [RunRecord.model_validate(r) for r in rows]


def set_status(conn: Connection[DictRow], run_id: str, status: RunStatus) -> None:
    row = conn.execute("select status from runs where id = %s for update", (run_id,)).fetchone()
    if row is None:
        raise RunNotFound(run_id)
    old = row["status"]
    conn.execute("update runs set status = %s, updated_at = now() where id = %s", (status, run_id))
    append_event(conn, run_id, "run.status_changed", {"from": old, "to": status})


def _set_column(conn: Connection[DictRow], run_id: str, column: str, value: object) -> None:
    query = sql.SQL("update runs set {} = %s, updated_at = now() where id = %s").format(
        sql.Identifier(column)
    )
    cur = conn.execute(query, (value, run_id))
    if cur.rowcount == 0:
        raise RunNotFound(run_id)


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
