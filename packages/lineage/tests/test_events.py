import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.events import GENESIS, append_event, event_hash, verify_chain
from modelforge_lineage.models import ChainReport
from modelforge_lineage.testing import create_test_run as _run


@pytest.mark.db
def test_chain_links_and_verifies(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn)
        e1 = append_event(conn, "run_a", "x", {"k": 1})
        e2 = append_event(conn, "run_a", "y", {}, step_id=None)
    assert e1.prev_hash != GENESIS  # run.created came first
    assert e2.prev_hash == e1.hash
    assert e2.hash == event_hash(
        e1.hash, {"type": "y", "ts": e2.ts, "step_id": None, "payload": {}}
    )
    with db.connection() as conn:
        assert verify_chain(conn, "run_a") == ChainReport(
            run_id="run_a", events=3, ok=True, first_bad_id=None
        )


@pytest.mark.db
def test_tampered_payload_and_deleted_event_are_detected(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn)
        ids = [append_event(conn, "run_a", "t", {"i": i}).id for i in range(3)]
    with db.transaction() as conn:
        conn.execute("update events set payload = '{\"i\": 99}' where id = %s", (ids[1],))
    with db.connection() as conn:
        assert verify_chain(conn, "run_a").first_bad_id == ids[1]
    with db.transaction() as conn:
        conn.execute("update events set payload = '{\"i\": 1}' where id = %s", (ids[1],))
        conn.execute("delete from events where id = %s", (ids[1],))
    with db.connection() as conn:
        report = verify_chain(conn, "run_a")
        assert report.ok is False and report.first_bad_id == ids[2]


@pytest.mark.db
def test_append_requires_transaction(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn)
    with db.connection() as conn, pytest.raises(RuntimeError):
        append_event(conn, "run_a", "x", {})
