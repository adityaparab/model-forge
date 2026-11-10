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


def apply(db: Database) -> list[int]:
    """Apply every migration not yet recorded; return the versions applied in this call."""
    with db.transaction() as conn:
        conn.execute(
            "create table if not exists schema_migrations (version int primary key,"
            " name text not null, applied_at timestamptz not null default now())"
        )
    with db.connection() as conn:
        done = {r["version"] for r in conn.execute("select version from schema_migrations")}
    applied: list[int] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version in done:
            continue
        with db.transaction() as conn:
            conn.execute(path.read_bytes(), prepare=False)
            conn.execute(
                "insert into schema_migrations (version, name) values (%s, %s)",
                (version, path.stem),
            )
        applied.append(version)
    return applied
