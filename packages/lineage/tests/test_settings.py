import pytest
from modelforge_lineage import settings
from modelforge_lineage.db import Database


@pytest.mark.db
def test_seed_writes_only_absent_keys(db: Database) -> None:
    defaults = {"default_profile": "fake", "default_cost_cap_usd": 10.0, "auto_replay": False}
    with db.transaction() as conn:
        written = settings.seed(conn, defaults)
    assert sorted(written) == sorted(defaults)
    with db.transaction() as conn:
        again = settings.seed(conn, {**defaults, "default_profile": "default"})
    assert again == []
    with db.connection() as conn:
        assert settings.get_all(conn) == defaults
        assert settings.list_audit(conn) == []


@pytest.mark.db
def test_set_value_writes_audit_and_reports_change(db: Database) -> None:
    with db.transaction() as conn:
        assert settings.set_value(conn, "auto_replay", True, updated_by="carol") is True
    with db.transaction() as conn:
        assert settings.set_value(conn, "auto_replay", True, updated_by="carol") is False
    with db.connection() as conn:
        assert settings.get(conn, "auto_replay") is True
        audit = settings.list_audit(conn)
    assert len(audit) == 1
    row = audit[0]
    assert (row.key, row.old_value, row.new_value, row.updated_by) == (
        "auto_replay",
        None,
        True,
        "carol",
    )


@pytest.mark.db
def test_list_audit_is_newest_first(db: Database) -> None:
    with db.transaction() as conn:
        settings.set_value(conn, "default_cost_cap_usd", 5.0, updated_by="carol")
        settings.set_value(conn, "default_cost_cap_usd", 7.5, updated_by="dave")
    with db.connection() as conn:
        audit = settings.list_audit(conn, limit=1)
    assert [(r.old_value, r.new_value, r.updated_by) for r in audit] == [(5.0, 7.5, "dave")]


@pytest.mark.db
def test_set_value_rejects_unknown_key(db: Database) -> None:
    with db.transaction() as conn, pytest.raises(ValueError, match="identity"):
        settings.set_value(conn, "identity", "x", updated_by="carol")


@pytest.mark.db
def test_get_default_when_absent(db: Database) -> None:
    with db.connection() as conn:
        assert settings.get(conn, "auto_replay", False) is False
        assert settings.get(conn, "auto_replay") is None
