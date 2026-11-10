from typing import Any

import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.findings import (
    FindingNotFound,
    blocking_findings,
    insert_findings,
    list_findings,
    set_response,
    set_verdict,
)
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


@pytest.mark.db
def test_findings_new_columns_verdict_and_blocking(db: Database) -> None:
    with db.transaction() as conn:
        create_test_run(conn)
        insert_findings(
            conn,
            "run_a",
            [
                {
                    **_row("F-1"),
                    "source": "precheck",
                    "check_key": "undiscussed_test:cal.hl",
                    "severity": "high",
                },
                _row("F-2"),
            ],
        )
    with db.connection() as conn:
        rows = list_findings(conn, "run_a")
    first = rows[0]
    assert (first["source"], first["check_key"], first["verdict"], first["closed_round"]) == (
        "precheck",
        "undiscussed_test:cal.hl",
        None,
        None,
    )
    assert (rows[1]["source"], rows[1]["check_key"]) == ("review", None)
    with db.connection() as conn:
        assert [r["id"] for r in blocking_findings(conn, "run_a")] == ["run_a/F-1"]  # F-2 is medium
    with db.transaction() as conn:
        set_verdict(
            conn,
            "run_a",
            "run_a/F-1",
            status="closed",
            verdict="check passes after remediation",
            closed_round=2,
        )
    with db.connection() as conn:
        row = list_findings(conn, "run_a")[0]
        assert (row["status"], row["verdict"], row["closed_round"]) == (
            "closed",
            "check passes after remediation",
            2,
        )
        assert blocking_findings(conn, "run_a") == []
    with db.transaction() as conn, pytest.raises(FindingNotFound):
        set_verdict(conn, "run_a", "run_a/F-9", status="closed", verdict="x", closed_round=2)


@pytest.mark.db
def test_insert_findings_carries_verdict_and_closed_round(db: Database) -> None:
    closed = {**_row("F-1"), "status": "closed", "verdict": "check passes", "closed_round": 2}
    with db.transaction() as conn:
        create_test_run(conn)
        insert_findings(conn, "run_a", [closed, _row("F-2")])
    with db.connection() as conn:
        rows = list_findings(conn, "run_a")
    assert [(r["status"], r["verdict"], r["closed_round"]) for r in rows] == [
        ("closed", "check passes", 2),
        ("open", None, None),
    ]
