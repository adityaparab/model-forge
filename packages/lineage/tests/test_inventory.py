from datetime import UTC, datetime

import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.inventory import create_stub, finalise, get_for_run
from modelforge_lineage.testing import create_test_run as _run


@pytest.mark.db
def test_versioning_and_finalise(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn, "run_a")
        _run(conn, "run_b")
        e1 = create_stub(
            conn, run_id="run_a", name="pd", owner="alice", intended_use="demo", tier=2
        )
        e2 = create_stub(
            conn, run_id="run_b", name="pd", owner="alice", intended_use="demo", tier=2
        )
        done = finalise(
            conn,
            e1.model_id,
            status="approved_with_conditions",
            champion_ref="c" * 64,
            validation_summary={"overall": "warn"},
            approver="bob",
            conditions=["monitor PSI"],
            approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    assert (e1.model_id, e2.model_id, e1.status) == ("pd@v1", "pd@v2", "draft")
    assert done.status == "approved_with_conditions" and done.conditions == ["monitor PSI"]
    with db.connection() as conn:
        assert get_for_run(conn, "run_a") == done and get_for_run(conn, "run_zz") is None
