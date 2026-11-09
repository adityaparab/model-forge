import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.migrate import TABLES, apply


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
