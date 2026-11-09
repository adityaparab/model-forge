from typing import Any

import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.findings import insert_findings, list_findings, set_response
from modelforge_lineage.testing import create_test_run


def _row(short_id: str) -> dict[str, Any]:
    return {
        "id": f"run_a/{short_id}",
        "round": 1,
        "severity": "medium",
        "category": "data",
        "title": f"title {short_id}",
        "description": "d",
        "evidence": ["e" * 64],
        "required_action": "a",
        "status": "open",
        "response": None,
        "response_evidence": [],
    }


@pytest.mark.db
def test_insert_set_response_and_list(db: Database) -> None:
    with db.transaction() as conn:
        create_test_run(conn)
        insert_findings(conn, "run_a", [_row("F-2"), _row("F-1")])
        set_response(
            conn,
            "run_a",
            "run_a/F-1",
            status="accepted",
            response="documented",
            response_evidence=["r" * 64],
        )
    with db.connection() as conn:
        rows = list_findings(conn, "run_a")
    assert [r["id"] for r in rows] == ["run_a/F-1", "run_a/F-2"]
    assert (rows[0]["status"], rows[0]["response"], rows[0]["response_evidence"]) == (
        "accepted",
        "documented",
        ["r" * 64],
    )
    assert (rows[1]["status"], rows[1]["response"], rows[1]["evidence"]) == (
        "open",
        None,
        ["e" * 64],
    )


@pytest.mark.db
def test_insert_findings_twice_is_idempotent(db: Database) -> None:
    with db.transaction() as conn:
        create_test_run(conn)
        insert_findings(conn, "run_a", [_row("F-1"), _row("F-2")])
    with db.transaction() as conn:
        insert_findings(conn, "run_a", [_row("F-1"), _row("F-2")])
    with db.connection() as conn:
        rows = list_findings(conn, "run_a")
    assert [r["id"] for r in rows] == ["run_a/F-1", "run_a/F-2"]
