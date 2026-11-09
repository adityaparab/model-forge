"""Repository for the ``findings`` table: challenger findings and the developer's responses.

The table's primary key is ``id`` alone, so callers pass row ids unique across runs (the
orchestrator uses ``f"{run_id}/{finding_id}"``).
"""

from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb


class FindingNotFound(KeyError):  # noqa: N818 - matches RunNotFound and InventoryNotFound
    pass


def insert_findings(conn: Connection[DictRow], run_id: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        conn.execute(
            "insert into findings (id, run_id, round, severity, category, title, description,"
            " evidence, required_action, status, response, response_evidence)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                row["id"],
                run_id,
                row["round"],
                row["severity"],
                row["category"],
                row["title"],
                row["description"],
                Jsonb(row["evidence"]),
                row["required_action"],
                row["status"],
                row["response"],
                Jsonb(row["response_evidence"]) if row["response_evidence"] is not None else None,
            ),
        )


def set_response(
    conn: Connection[DictRow],
    run_id: str,
    finding_id: str,
    *,
    status: str,
    response: str,
    response_evidence: list[str],
) -> None:
    cur = conn.execute(
        "update findings set status = %s, response = %s, response_evidence = %s"
        " where id = %s and run_id = %s",
        (status, response, Jsonb(response_evidence), finding_id, run_id),
    )
    if cur.rowcount == 0:
        raise FindingNotFound(finding_id)


def list_findings(conn: Connection[DictRow], run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("select * from findings where run_id = %s order by id", (run_id,))
    return [dict(r) for r in rows]
