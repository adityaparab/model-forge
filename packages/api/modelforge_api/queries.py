"""Aggregates and composite reads the lineage package has no function for; parameterised SQL."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, LiteralString

from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.models import ArtifactRow, EventRecord, InventoryEntry
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.inspect import run_refs
from modelforge_orchestrator.schemas import ReplayReport
from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_api.schemas import (
    CostBucket,
    CostDay,
    CostGroup,
    CostRunRow,
    CostsResponse,
    CostSummary,
    CostTokens,
    FindingsCounts,
    ReplayItem,
)

__all__ = [
    "SEVERITY_ORDER",
    "cost_summary",
    "costs_window",
    "events_head",
    "findings_counts",
    "get_artifact_row",
    "inventory_names",
    "list_events_after",
    "list_events_after_global",
    "list_inventory",
    "replay_items",
]

SEVERITY_ORDER = ("critical", "high", "medium", "low")

_BUCKET = (
    "count(*) as n_calls,"
    " coalesce(sum(lc.input_tokens), 0) as input_tokens,"
    " coalesce(sum(lc.output_tokens), 0) as output_tokens,"
    " coalesce(sum(lc.cache_read_tokens), 0) as cache_read_tokens,"
    " coalesce(sum(lc.cache_write_tokens), 0) as cache_write_tokens,"
    " coalesce(sum(lc.cost_usd), 0) as cost_usd"
)
_RUN_CALLS = "from llm_calls lc join steps s on s.id = lc.step_id where s.run_id = %s"


def _bucket(row: DictRow) -> CostBucket:
    return CostBucket(
        n_calls=int(row["n_calls"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cache_read_tokens=int(row["cache_read_tokens"]),
        cache_write_tokens=int(row["cache_write_tokens"]),
        cost_usd=float(row["cost_usd"]),
    )


def cost_summary(
    conn: Connection[DictRow], run_id: str, *, spent_usd: float, cap_usd: float
) -> CostSummary:
    """The run's spend from its ``llm_calls`` rows, by purpose and by model (spec 3.3.1)."""
    totals = conn.execute(
        "select count(*) as n_calls,"
        " count(*) filter (where not lc.schema_valid) as n_schema_retries "
        f"{_RUN_CALLS}",
        (run_id,),
    ).fetchone()
    assert totals is not None
    grouped: dict[str, dict[str, CostBucket]] = {}
    for column in ("purpose", "model"):
        rows = conn.execute(
            f"select lc.{column} as key, {_BUCKET} {_RUN_CALLS} group by lc.{column} order by key",
            (run_id,),
        )
        grouped[column] = {row["key"]: _bucket(row) for row in rows}
    return CostSummary(
        spent_usd=spent_usd,
        cap_usd=cap_usd,
        n_calls=int(totals["n_calls"]),
        n_schema_retries=int(totals["n_schema_retries"]),
        by_purpose=grouped["purpose"],
        by_model=grouped["model"],
    )


_WINDOW_CALLS = (
    "from llm_calls lc join steps s on s.id = lc.step_id join runs r on r.id = s.run_id"
    " where lc.ts >= %(since)s and lc.ts <= %(until)s"
)


def _group(row: DictRow) -> CostGroup:
    return CostGroup(
        key=row["key"] if row["key"] is not None else "",
        cost_usd=float(row["cost_usd"]),
        n_runs=int(row["n_runs"]),
        n_calls=int(row["n_calls"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
    )


def costs_window(conn: Connection[DictRow], since: datetime, until: datetime) -> CostsResponse:
    """Spend across runs between ``since`` and ``until`` (spec 3.9).

    The totals and groups are over the ``llm_calls`` whose ``ts`` is in the window; ``by_run``
    lists every run created in the window with its calls in the window (zero when none).
    """
    window = {"since": since, "until": until}
    totals = conn.execute(
        "select count(distinct r.id) as n_runs, count(*) as n_calls,"
        " coalesce(sum(lc.cost_usd), 0) as cost_usd,"
        " coalesce(sum(lc.input_tokens), 0) as input_tokens,"
        " coalesce(sum(lc.output_tokens), 0) as output_tokens,"
        " coalesce(sum(lc.cache_read_tokens), 0) as cache_read_tokens,"
        " coalesce(sum(lc.cache_write_tokens), 0) as cache_write_tokens "
        f"{_WINDOW_CALLS}",
        window,
    ).fetchone()
    assert totals is not None
    by_run = [
        CostRunRow(
            run_id=row["id"],
            status=row["status"],
            profile=row["profile"],
            requester=row["requester"],
            created_at=row["created_at"],
            cost_usd=float(row["cost_usd"]),
            cost_cap_usd=float(row["cost_cap_usd"]),
            n_calls=int(row["n_calls"]),
        )
        for row in conn.execute(
            "select r.id, r.status, r.profile, r.requester, r.created_at, r.cost_cap_usd,"
            " count(c.id) as n_calls, coalesce(sum(c.cost_usd), 0) as cost_usd"
            " from runs r left join ("
            "  select s.run_id, lc.id, lc.cost_usd from llm_calls lc"
            "  join steps s on s.id = lc.step_id"
            "  where lc.ts >= %(since)s and lc.ts <= %(until)s"
            " ) c on c.run_id = r.id"
            " where r.created_at >= %(since)s and r.created_at <= %(until)s"
            " group by r.id order by r.created_at desc, r.id",
            window,
        )
    ]
    # the fields a group does not use are 0 (spec 3.9)
    groups: dict[str, LiteralString] = {
        "profile": "r.profile as key, count(distinct r.id) as n_runs, 0 as input_tokens,"
        " 0 as output_tokens",
        "model": "lc.model as key, 0 as n_runs,"
        " coalesce(sum(lc.input_tokens), 0) as input_tokens,"
        " coalesce(sum(lc.output_tokens), 0) as output_tokens",
        "purpose": "lc.purpose as key, 0 as n_runs, 0 as input_tokens, 0 as output_tokens",
    }
    grouped = {
        name: [
            _group(row)
            for row in conn.execute(
                f"select {columns}, count(*) as n_calls,"
                f" coalesce(sum(lc.cost_usd), 0) as cost_usd {_WINDOW_CALLS}"
                " group by 1 order by 1",
                window,
            )
        ]
        for name, columns in groups.items()
    }
    by_day = [
        CostDay(day=row["day"], cost_usd=float(row["cost_usd"]), n_calls=int(row["n_calls"]))
        for row in conn.execute(
            "select date_trunc('day', lc.ts at time zone 'UTC')::date as day,"
            " count(*) as n_calls, coalesce(sum(lc.cost_usd), 0) as cost_usd "
            f"{_WINDOW_CALLS} group by 1 order by 1",
            window,
        )
    ]
    return CostsResponse(
        since=since,
        until=until,
        total_usd=float(totals["cost_usd"]),
        n_runs=int(totals["n_runs"]),
        n_calls=int(totals["n_calls"]),
        tokens=CostTokens(
            input=int(totals["input_tokens"]),
            output=int(totals["output_tokens"]),
            cache_read=int(totals["cache_read_tokens"]),
            cache_write=int(totals["cache_write_tokens"]),
        ),
        by_run=by_run,
        by_profile=grouped["profile"],
        by_model=grouped["model"],
        by_purpose=grouped["purpose"],
        by_day=by_day,
    )


def findings_counts(conn: Connection[DictRow], run_ids: Sequence[str]) -> dict[str, FindingsCounts]:
    """Each run's finding count and highest open (``open`` or ``disputed``) severity."""
    rows = conn.execute(
        "select run_id, count(*) as n,"
        " min(array_position(%s::text[], severity))"
        " filter (where status in ('open', 'disputed')) as open_rank"
        " from findings where run_id = any(%s) group by run_id",
        (list(SEVERITY_ORDER), list(run_ids)),
    )
    found = {
        row["run_id"]: FindingsCounts(
            n=int(row["n"]),
            max_open_severity=(
                SEVERITY_ORDER[row["open_rank"] - 1] if row["open_rank"] is not None else None
            ),
        )
        for row in rows
    }
    empty = FindingsCounts(n=0, max_open_severity=None)
    return {run_id: found.get(run_id, empty) for run_id in run_ids}


def inventory_names(conn: Connection[DictRow], run_ids: Sequence[str]) -> dict[str, str]:
    """The model name of each run's latest inventory entry; runs before intake are absent."""
    rows = conn.execute(
        "select distinct on (run_id) run_id, name from model_inventory"
        " where run_id = any(%s) order by run_id, version desc",
        (list(run_ids),),
    )
    return {row["run_id"]: row["name"] for row in rows}


def list_inventory(conn: Connection[DictRow]) -> list[InventoryEntry]:
    """Every inventory entry, newest approval first, unapproved entries last."""
    rows = conn.execute(
        "select * from model_inventory order by approved_at desc nulls last, model_id"
    )
    return [InventoryEntry.model_validate(row) for row in rows]


def get_artifact_row(
    conn: Connection[DictRow], run_id: str, artifact_id: str
) -> ArtifactRow | None:
    """The artifact row of ``artifact_id`` for ``run_id``; ``None`` when the run lacks it."""
    row = conn.execute(
        "select id, run_id, kind, media_type, size_bytes, uri, producer_step_id, schema_version"
        " from artifacts where run_id = %s and id = %s",
        (run_id, artifact_id),
    ).fetchone()
    return ArtifactRow.model_validate(row) if row is not None else None


_SELECT_EVENTS = "select id, run_id, step_id, ts, type, payload, prev_hash, hash from events"


def _event(row: dict[str, Any]) -> EventRecord:
    return EventRecord.model_validate(
        {**row, "ts": row["ts"].astimezone(UTC).isoformat(timespec="microseconds")}
    )


def list_events_after(
    conn: Connection[DictRow], run_id: str, after: int, limit: int
) -> list[EventRecord]:
    """Up to ``limit`` of the run's events with an id above ``after``, in id order."""
    rows = conn.execute(
        f"{_SELECT_EVENTS} where run_id = %s and id > %s order by id limit %s",
        (run_id, after, limit),
    )
    return [_event(row) for row in rows]


def list_events_after_global(
    conn: Connection[DictRow], after: int, limit: int
) -> list[EventRecord]:
    """Up to ``limit`` events of every run with an id above ``after``, in id order."""
    rows = conn.execute(
        f"{_SELECT_EVENTS} where id > %s order by id limit %s",
        (after, limit),
    )
    return [_event(row) for row in rows]


def events_head(conn: Connection[DictRow]) -> int:
    """The highest event id; ``0`` when there are no events."""
    row = conn.execute("select coalesce(max(id), 0) as head from events").fetchone()
    assert row is not None
    return int(row["head"])


def replay_items(db: Database, store: ArtifactStore, run_id: str) -> list[ReplayItem]:
    """The replays of ``run_id``, oldest first, each with its report once it has one.

    Takes the `Database`, not a connection: the report ref is read from each replay's
    checkpoint through `run_refs`.
    """
    with db.connection() as conn:
        replays = runs.list_replays(conn, run_id)
    items: list[ReplayItem] = []
    for replay in replays:
        ref = run_refs(db, replay.id).get("replay_report")
        report = store.get_model(ref.id, ReplayReport) if ref is not None else None
        items.append(ReplayItem(run=replay, report=report))
    return items
