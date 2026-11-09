"""Repository for the ``model_inventory`` table: the register of models and their versions."""

from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from modelforge_lineage.models import InventoryEntry


class InventoryNotFound(KeyError):  # noqa: N818 - matches RunNotFound and ArtifactNotFound
    pass


def create_stub(
    conn: Connection[DictRow],
    *,
    run_id: str,
    name: str,
    owner: str,
    intended_use: str,
    tier: int,
) -> InventoryEntry:
    count = conn.execute(
        "select count(*) as n from model_inventory where name = %s", (name,)
    ).fetchone()
    assert count is not None
    version = 1 + count["n"]
    row = conn.execute(
        "insert into model_inventory (model_id, run_id, name, version, owner, intended_use,"
        " tier, status) values (%s, %s, %s, %s, %s, %s, %s, 'draft') returning *",
        (f"{name}@v{version}", run_id, name, version, owner, intended_use, tier),
    ).fetchone()
    assert row is not None
    return InventoryEntry.model_validate(row)


def finalise(
    conn: Connection[DictRow],
    model_id: str,
    *,
    status: str,
    champion_ref: str | None,
    validation_summary: dict[str, Any] | None,
    approver: str | None,
    conditions: list[str] | None,
    approved_at: datetime | None,
) -> InventoryEntry:
    row = conn.execute(
        "update model_inventory set status = %s, champion_ref = %s, validation_summary = %s,"
        " approver = %s, conditions = %s, approved_at = %s where model_id = %s returning *",
        (
            status,
            champion_ref,
            Jsonb(validation_summary) if validation_summary is not None else None,
            approver,
            conditions,
            approved_at,
            model_id,
        ),
    ).fetchone()
    if row is None:
        raise InventoryNotFound(model_id)
    return InventoryEntry.model_validate(row)


def get_entry(conn: Connection[DictRow], model_id: str) -> InventoryEntry:
    row = conn.execute("select * from model_inventory where model_id = %s", (model_id,)).fetchone()
    if row is None:
        raise InventoryNotFound(model_id)
    return InventoryEntry.model_validate(row)


def get_for_run(conn: Connection[DictRow], run_id: str) -> InventoryEntry | None:
    row = conn.execute(
        "select * from model_inventory where run_id = %s order by version desc limit 1",
        (run_id,),
    ).fetchone()
    return InventoryEntry.model_validate(row) if row is not None else None
