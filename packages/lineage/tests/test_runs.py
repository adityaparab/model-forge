from typing import Any

import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.events import list_events
from modelforge_lineage.models import RunRecord
from modelforge_lineage.runs import (
    RunNotFound,
    add_cost,
    clear_pause,
    count_runs,
    create_run,
    get_run,
    list_replays,
    list_runs,
    pause_requested,
    request_pause,
    set_cost_cap,
    set_dataset_id,
    set_status,
)
from modelforge_lineage.testing import create_test_run as _run
from psycopg import Connection
from psycopg.rows import DictRow


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


@pytest.mark.db
def test_pause_flag_round_trip(db: Database) -> None:
    with db.transaction() as conn:
        rec = _run(conn)
        assert rec.pause_requested is False
        request_pause(conn, "run_a")
    with db.connection() as conn:
        assert pause_requested(conn, "run_a") is True
        assert get_run(conn, "run_a").pause_requested is True
    with db.transaction() as conn:
        clear_pause(conn, "run_a")
    with db.connection() as conn:
        assert pause_requested(conn, "run_a") is False
        assert get_run(conn, "run_a").pause_requested is False
    with db.transaction() as conn, pytest.raises(RunNotFound):
        request_pause(conn, "run_zzz")
    with db.transaction() as conn, pytest.raises(RunNotFound):
        clear_pause(conn, "run_zzz")
    with db.connection() as conn, pytest.raises(RunNotFound):
        pause_requested(conn, "run_zzz")


@pytest.mark.db
def test_create_run_stores_trace_id(db: Database) -> None:
    with db.transaction() as conn:
        rec = create_run(
            conn,
            run_id="run_t",
            requester="alice",
            profile="default",
            brief_hash="h",
            brief_ref="b" * 64,
            dataset_ref="synthetic_credit",
            seed=42,
            trace_id="a" * 32,
        )
    assert rec.trace_id == "a" * 32
    with db.connection() as conn:
        assert get_run(conn, "run_t").trace_id == "a" * 32


def _replay(conn: Connection[DictRow], run_id: str, of: str, requester: str) -> RunRecord:
    return create_run(
        conn,
        run_id=run_id,
        requester=requester,
        profile="fake",
        brief_hash="h",
        brief_ref="b" * 64,
        dataset_ref="synthetic_credit",
        seed=42,
        replay_of=of,
    )


@pytest.mark.db
def test_list_runs_filters_and_counts(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn, "run_a", requester="alice")
        _run(conn, "run_b", requester="bob")
        set_status(conn, "run_b", "aborted")
        _replay(conn, "run_c", "run_a", "bob")
        _replay(conn, "run_d", "run_a", "alice")
    with db.connection() as conn:

        def ids(**filters: Any) -> list[str]:
            return [r.id for r in list_runs(conn, **filters)]

        assert set(ids()) == {"run_a", "run_b", "run_c", "run_d"}
        assert count_runs(conn) == 4
        assert ids(status="aborted") == ["run_b"]
        assert count_runs(conn, status="aborted") == 1
        assert set(ids(requester="bob")) == {"run_b", "run_c"}
        assert count_runs(conn, requester="bob") == 2
        assert set(ids(profile="fake")) == {"run_c", "run_d"}
        assert count_runs(conn, profile="fake") == 2
        assert set(ids(include_replays=False)) == {"run_a", "run_b"}
        assert count_runs(conn, include_replays=False) == 2
        assert ids(requester="bob", include_replays=False) == ["run_b"]
        assert count_runs(conn, requester="bob", include_replays=False) == 1
        everything = ids()
        assert ids(limit=2) == everything[:2]
        assert ids(limit=2, offset=2) == everything[2:]
        assert [r.id for r in list_replays(conn, "run_a")] == ["run_c", "run_d"]
        assert list_replays(conn, "run_b") == []
