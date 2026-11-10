import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.models import PromptRow
from modelforge_lineage.prompts import PromptHashConflict, upsert_prompt


@pytest.mark.db
def test_upsert_prompt_is_idempotent_and_detects_hash_change(db: Database) -> None:
    row = PromptRow(name="scope_brief", version=1, hash="h1", path="prompts/scope_brief/v1.md")
    with db.transaction() as conn:
        upsert_prompt(conn, row)
        upsert_prompt(conn, row)
    with db.connection() as conn:
        rows = conn.execute("select name, version, hash, path from prompts").fetchall()
    assert [PromptRow.model_validate(r) for r in rows] == [row]
    with db.transaction() as conn, pytest.raises(PromptHashConflict):
        upsert_prompt(conn, row.model_copy(update={"hash": "h2"}))
    assert issubclass(PromptHashConflict, ValueError)
