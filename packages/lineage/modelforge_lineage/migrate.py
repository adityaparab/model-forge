"""Apply the numbered SQL migrations shipped in ``migrations/``."""

from pathlib import Path

from modelforge_lineage.db import Database

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

TABLES: tuple[str, ...] = (
    "tool_calls",
    "steps",
    "events",
    "artifacts",
    "approvals",
    "findings",
    "model_inventory",
    "run_queue",
    "runs",
    "llm_calls",
    "prompts",
    "settings",
    "settings_audit",
)


MIGRATION_LOCK = 5_026_110_501
"""The `pg_advisory_xact_lock` key every migration transaction takes first, so two processes
migrating one database at first boot (the API and the worker) never run a step together."""


def apply(db: Database) -> list[int]:
    """Apply every migration not yet recorded; return the versions applied in this call.

    Each transaction takes `MIGRATION_LOCK` first and re-reads what is recorded under it, so a
    concurrent `apply` waits and then skips what the other one applied.
    """
    with db.transaction() as conn:
        conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        conn.execute(
            "create table if not exists schema_migrations (version int primary key,"
            " name text not null, applied_at timestamptz not null default now())"
        )
    applied: list[int] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        with db.transaction() as conn:
            conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
            done = conn.execute(
                "select 1 from schema_migrations where version = %s", (version,)
            ).fetchone()
            if done is not None:
                continue
            conn.execute(path.read_bytes(), prepare=False)
            conn.execute(
                "insert into schema_migrations (version, name) values (%s, %s)",
                (version, path.stem),
            )
        applied.append(version)
    return applied
