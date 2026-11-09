import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.events import list_events
from modelforge_lineage.runs import RunNotFound, get_run, set_dataset_id, set_status
from modelforge_lineage.testing import create_test_run as _run


@pytest.mark.db
def test_create_get_and_status_events(db: Database) -> None:
    with db.transaction() as conn:
        rec = _run(conn)
        set_status(conn, "run_a", "running")
        set_dataset_id(conn, "run_a", "d" * 64)
        got = get_run(conn, "run_a")
    assert rec.status == "queued" and got.status == "running"
    assert got.dataset_id == "d" * 64 and got.replay_of is None
    with db.connection() as conn:
        types = [e.type for e in list_events(conn, "run_a")]
    assert types == ["run.created", "run.status_changed"]
    with db.connection() as conn, pytest.raises(RunNotFound):
        get_run(conn, "run_zz")
