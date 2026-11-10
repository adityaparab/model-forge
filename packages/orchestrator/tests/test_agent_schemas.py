import pytest
from modelforge_orchestrator.agent_schemas import (
    ChallengeQueryPlan,
    FindingDraft,
    FindingResponseDraft,
    QueryPlan,
    ReReviewDraft,
    ReviewDraft,
    ScopeNotes,
    SectionDraft,
    Verdict,
)
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


def test_challenger_and_remediation_schemas() -> None:
    assert ChallengeQueryPlan(queries=["a"] * 5).queries == ["a"] * 5
    with pytest.raises(ValidationError):
        ChallengeQueryPlan(queries=["a", "b", "c", "d", "e", "f"])
    draft = FindingDraft(
        severity="high",
        category="performance",
        title="t",
        description="d",
        evidence=["e"],
        required_action="a",
    )
    assert ReviewDraft(findings=[draft]).findings == [draft]
    verdict = Verdict(finding_id="F-001", status="closed", note="n")
    assert ReReviewDraft(verdicts=[verdict]).verdicts == [verdict]
    with pytest.raises(ValidationError):
        Verdict.model_validate({"finding_id": "F-001", "status": "maybe", "note": "n"})
    response = FindingResponseDraft(decision="dispute", action="x")
    assert response.evidence == [] and response.sections_to_regenerate == []
    with pytest.raises(ValidationError):
        FindingDraft.model_validate(draft.model_dump() | {"category": "other"})
