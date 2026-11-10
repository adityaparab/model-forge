import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.events import list_events
from modelforge_lineage.runs import (
    RunNotFound,
    add_cost,
    create_run,
    get_run,
    set_cost_cap,
    set_dataset_id,
    set_status,
)
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


@pytest.mark.db
def test_create_run_records_cost_cap_and_rerun_llm(db: Database) -> None:
    with db.transaction() as conn:
        default = _run(conn)
        explicit = create_run(
            conn,
            run_id="run_b",
            requester="alice",
            profile="default",
            brief_hash="h",
            brief_ref="b" * 64,
            dataset_ref="synthetic_credit",
            seed=42,
            cost_cap_usd=2.5,
            rerun_llm=True,
        )
    assert (default.cost_cap_usd, default.rerun_llm) == (10.0, False)
    assert (explicit.cost_cap_usd, explicit.rerun_llm) == (2.5, True)
    with db.connection() as conn:
        got = get_run(conn, "run_b")
    assert (got.cost_cap_usd, got.rerun_llm) == (2.5, True)


@pytest.mark.db
def test_add_cost_accumulates_and_set_cost_cap(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn)
        assert add_cost(conn, "run_a", 0.25) == 0.25
        total = add_cost(conn, "run_a", 0.5)
        set_cost_cap(conn, "run_a", 5)
    assert total == 0.75 and isinstance(total, float)
    with db.connection() as conn:
        got = get_run(conn, "run_a")
    assert (got.cost_usd, got.cost_cap_usd) == (0.75, 5.0)
    with db.transaction() as conn, pytest.raises(RunNotFound):
        add_cost(conn, "run_zz", 0.1)
    with db.transaction() as conn, pytest.raises(RunNotFound):
        set_cost_cap(conn, "run_zz", 1.0)
