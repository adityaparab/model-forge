import threading

import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.migrate import MIGRATIONS_DIR, TABLES, apply
from modelforge_lineage.testing import temporary_database


@pytest.mark.db
def test_apply_is_idempotent_and_creates_tables(database: Database) -> None:
    assert apply(database) == []  # the session fixture already applied it
    with database.connection() as conn:
        names = {
            r["table_name"]
            for r in conn.execute(
                "select table_name from information_schema.tables where table_schema = 'public'"
            )
        }
    assert set(TABLES) | {"schema_migrations"} <= names


@pytest.mark.db
def test_db_fixture_truncates_between_tests(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute("insert into prompts (name, version, hash, path) values ('p', 1, 'h', 'x')")
    with db.connection() as conn:
        row = conn.execute("select count(*) as n from prompts").fetchone()
        assert row is not None and row["n"] == 1


@pytest.mark.db
def test_db_fixture_truncates_between_tests_2(db: Database) -> None:
    with db.connection() as conn:
        row = conn.execute("select count(*) as n from prompts").fetchone()
        assert row is not None and row["n"] == 0


@pytest.mark.db
def test_concurrent_apply_on_a_fresh_database_migrates_once(database_url: str) -> None:
    """The API and the worker migrating at first boot: both succeed, each migration runs once."""
    with temporary_database(database_url) as url:
        dbs = [Database(url), Database(url)]
        barrier = threading.Barrier(2)
        results: list[list[int]] = []
        errors: list[BaseException] = []

        def run(db: Database) -> None:
            barrier.wait()
            try:
                results.append(apply(db))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(db,)) for db in dbs]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            assert errors == []
            versions = sorted(int(p.name.split("_", 1)[0]) for p in MIGRATIONS_DIR.glob("*.sql"))
            assert sorted(v for applied in results for v in applied) == versions
            with dbs[0].connection() as conn:
                rows = conn.execute("select version from schema_migrations order by 1").fetchall()
            assert [r["version"] for r in rows] == versions
        finally:
            for db in dbs:
                db.close()
