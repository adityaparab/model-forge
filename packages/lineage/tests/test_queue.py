import threading

import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.models import QueuedRun
from modelforge_lineage.queue import claim, enqueue, release
from modelforge_lineage.testing import create_test_run as _run


@pytest.mark.db
def test_claim_order_release_and_specific(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn, "run_a")
        _run(conn, "run_b")
        enqueue(conn, "run_a", "start")
        enqueue(conn, "run_b", "start")
    with db.transaction() as conn:
        assert claim(conn, "w1", run_id="run_b") == QueuedRun(run_id="run_b", kind="start")
        assert claim(conn, "w1") == QueuedRun(run_id="run_a", kind="start")
        assert claim(conn, "w1") is None
        release(conn, "run_a")
        release(conn, "run_b")
        enqueue(conn, "run_a", "resume")
        again = claim(conn, "w1")
        assert again is not None and again.kind == "resume"


@pytest.mark.db
def test_concurrent_claims_take_different_runs(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn, "run_a")
        _run(conn, "run_b")
        enqueue(conn, "run_a", "start")
        enqueue(conn, "run_b", "start")
    first_claimed = threading.Event()
    second_done = threading.Event()
    got: dict[str, str | None] = {}

    def worker1() -> None:
        with db.transaction() as conn:
            q = claim(conn, "w1")
            got["w1"] = q.run_id if q else None
            first_claimed.set()
            second_done.wait(5)  # hold the lock open

    def worker2() -> None:
        first_claimed.wait(5)
        with db.transaction() as conn:
            q = claim(conn, "w2")
            got["w2"] = q.run_id if q else None
        second_done.set()

    t1, t2 = threading.Thread(target=worker1), threading.Thread(target=worker2)
    t1.start()
    t2.start()
    t1.join(10)
    t2.join(10)
    assert {got["w1"], got["w2"]} == {"run_a", "run_b"}
