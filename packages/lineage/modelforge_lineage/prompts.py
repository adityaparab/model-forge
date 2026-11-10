"""Repository for the ``prompts`` table: the registry of versioned prompt templates."""

from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_lineage.models import PromptRow


class PromptHashConflict(ValueError):  # noqa: N818 - name fixed by the lineage interface
    pass


def upsert_prompt(conn: Connection[DictRow], row: PromptRow) -> None:
    """Record ``row``; a no-op when it already exists, an error if its hash changed."""
    conn.execute(
        "insert into prompts (name, version, hash, path) values (%s, %s, %s, %s)"
        " on conflict (name, version) do nothing",
        (row.name, row.version, row.hash, row.path),
    )
    existing = conn.execute(
        "select hash from prompts where name = %s and version = %s", (row.name, row.version)
    ).fetchone()
    assert existing is not None
    if existing["hash"] != row.hash:
        raise PromptHashConflict(
            f"prompt {row.name} v{row.version} is recorded with hash {existing['hash']},"
            f" not {row.hash}"
        )
