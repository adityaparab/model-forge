import pytest
from modelforge_orchestrator.agent_schemas import QueryPlan, ScopeNotes, SectionDraft
from pydantic import ValidationError


def test_agent_schemas_bounds() -> None:
    assert QueryPlan(queries=[]).queries == []
    assert QueryPlan(queries=["a", "b", "c"]).queries == ["a", "b", "c"]
    with pytest.raises(ValidationError):
        QueryPlan(queries=["a", "b", "c", "d"])
    assert SectionDraft(body_md="x").citations == []
    notes = ScopeNotes(rationale="r", assumptions=[], open_questions=[], passages=["p"])
    assert notes.passages == ["p"]
    with pytest.raises(ValidationError):
        SectionDraft.model_validate({"body_md": "x", "extra": 1})
