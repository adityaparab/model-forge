"""Repository for the server-side ``settings`` table and its ``settings_audit`` trail."""

from collections.abc import Mapping
from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from modelforge_lineage.models import SettingsAuditRow

EDITABLE_KEYS: tuple[str, ...] = ("default_profile", "default_cost_cap_usd", "auto_replay")


def get_all(conn: Connection[DictRow]) -> dict[str, Any]:
    return {r["key"]: r["value"] for r in conn.execute("select key, value from settings")}


def get(conn: Connection[DictRow], key: str, default: Any = None) -> Any:
    row = conn.execute("select value from settings where key = %s", (key,)).fetchone()
    return default if row is None else row["value"]


def set_value(conn: Connection[DictRow], key: str, value: Any, *, updated_by: str) -> bool:
    """Store ``value`` under ``key`` with an audit row; False when it is already stored.

    Call inside a transaction.
    """
    if key not in EDITABLE_KEYS:
        raise ValueError(f"setting {key!r} is not editable; editable: {', '.join(EDITABLE_KEYS)}")
    row = conn.execute("select value from settings where key = %s for update", (key,)).fetchone()
    old = None if row is None else row["value"]
    if row is not None and old == value:
        return False
    conn.execute(
        "insert into settings (key, value, updated_at, updated_by) values (%s, %s, now(), %s)"
        " on conflict (key) do update set value = excluded.value,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, Jsonb(value), updated_by),
    )
    conn.execute(
        "insert into settings_audit (key, old_value, new_value, updated_by)"
        " values (%s, %s, %s, %s)",
        (key, Jsonb(old) if row is not None else None, Jsonb(value), updated_by),
    )
    return True


def seed(conn: Connection[DictRow], defaults: Mapping[str, Any]) -> list[str]:
    """Write each default whose key is absent (no audit rows); return the keys written."""
    written: list[str] = []
    for key, value in defaults.items():
        cur = conn.execute(
            "insert into settings (key, value) values (%s, %s) on conflict (key) do nothing",
            (key, Jsonb(value)),
        )
        if cur.rowcount == 1:
            written.append(key)
    return written


def list_audit(conn: Connection[DictRow], limit: int = 20) -> list[SettingsAuditRow]:
    rows = conn.execute("select * from settings_audit order by ts desc, id desc limit %s", (limit,))
    return [SettingsAuditRow.model_validate(r) for r in rows]
