"""The remediation node: one response per open finding, regeneration, re-render and replay."""

import pytest
from langgraph.runtime import Runtime
from modelforge_gateway.schemas import GatewayError
from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.findings import list_findings
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.store import ArtifactStore
from modelforge_mcp.schemas import DocumentSections
from modelforge_orchestrator.agent_schemas import FindingResponseDraft
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import original_manifest
from modelforge_orchestrator.nodes.challenge import challenge
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.remediate import (
    REMEDIATE_PROMPTS,
    remediate,
    responses_summary,
    validate_response,
)
from modelforge_orchestrator.schemas import FindingResponse, FindingsList, ResponsesList, RunState

from .conftest import SYNTHETIC_BRIEF, RemediationStub, RuntimeWithModel, apply_node, make_run

TEMPLATE_IDS = {"monitoring", "governance", "limitations"}


@pytest.fixture
def challenged_state(validated_state: RunState, runtime: Runtime[Deps]) -> RunState:
    """``validated_state`` after `document` and the first `challenge`."""
    state = apply_node(validated_state, document(validated_state, runtime))
    return apply_node(state, challenge(state, runtime))


def _bodies(store: ArtifactStore, state: RunState) -> dict[str, str]:
    sections = store.get_model(state.refs["document_sections"].id, DocumentSections)
    return {s.section_id: s.body_md for s in sections.sections}


@pytest.mark.db
def test_remediate_answers_every_open_finding_and_regenerates_named_sections(
    challenged_state: RunState, deps: Deps, runtime: Runtime[Deps], db: Database
) -> None:
    state = challenged_state
    store = deps.store
    before = store.get_model(state.refs["findings"].id, FindingsList)
    open_ids = [f.id for f in before.findings if f.status == "open"]
    assert open_ids
    command = remediate(state, runtime)
    assert command.goto == "challenge" and command.update is not None
    after = apply_node(state, command)
    assert after.counters.remediation_rounds == 1
    assert after.counters.challenge_rounds == state.counters.challenge_rounds
    assert after.cost_usd >= state.cost_usd

    responses = store.get_model(after.refs["responses"].id, ResponsesList)
    assert responses.round == 1
    assert [r.finding_id for r in responses.responses] == open_ids
    assert all(r.round == 1 for r in responses.responses)
    with db.connection() as conn:
        plan_id = runs.get_run(conn, state.run_id).plan_ref
    title_id = {f.title.split()[0]: f.id for f in before.findings}
    by_id = {r.finding_id: r for r in responses.responses}
    governance, monitoring = by_id[title_id["Approver"]], by_id[title_id["Monitoring"]]
    assert governance.decision == "dispute" and governance.sections_regenerated == []
    assert governance.evidence == [f"[A:{plan_id}#model_name]"]
    assert monitoring.decision == "accept" and monitoring.sections_regenerated == ["monitoring"]
    summary = after.refs["responses"].summary
    n_accepted = sum(r.decision == "accept" for r in responses.responses)
    assert summary == {
        "n_responses": len(open_ids),
        "n_accepted": n_accepted,
        "n_disputed": len(open_ids) - n_accepted,
        "n_sections_regenerated": len(
            {s for r in responses.responses for s in r.sections_regenerated}
        ),
        "round": 1,
    }

    regenerated = {s for r in responses.responses for s in r.sections_regenerated}
    old, new = _bodies(store, state), _bodies(store, after)
    assert old.keys() == new.keys()
    assert {k for k in old if old[k] != new[k]} == regenerated
    for name in ("document_sections", "document_md", "document_html", "completeness"):
        assert after.refs[name].id != state.refs[name].id
    assert after.refs["completeness"].summary["complete"] is True
    assert after.refs["document_sections"].summary["n_flagged_sections"] == 0

    findings = {f.id: f for f in store.get_model(after.refs["findings"].id, FindingsList).findings}
    assert after.refs["findings"].summary["by_status"].get("open") is None
    for finding_id in open_ids:
        response = by_id[finding_id]
        finding = findings[finding_id]
        status = "accepted" if response.decision == "accept" else "disputed"
        assert (finding.status, finding.response) == (status, response.action)
        assert finding.response_evidence == response.evidence
    with db.connection() as conn:
        rows = {r["id"]: r for r in list_findings(conn, state.run_id)}
        calls = list_llm_calls(conn, state.run_id)
    for finding_id in open_ids:
        row = rows[f"{state.run_id}/{finding_id}"]
        assert (row["status"], row["response"]) == (
            findings[finding_id].status,
            by_id[finding_id].action,
        )
        assert row["response_evidence"] == by_id[finding_id].evidence
    remediation = [c for c in calls if c.purpose == "remediate"]
    assert [c.prompt_name for c in remediation].count("remediate/respond") == len(open_ids)
    assert {c.prompt_name for c in remediation} == {
        "remediate/respond",
        REMEDIATE_PROMPTS.plan_queries,
        REMEDIATE_PROMPTS.write,
    }


@pytest.mark.db
def test_dispute_without_evidence_is_retried_then_parks(
    challenged_state: RunState, runtime_with_model: RuntimeWithModel, db: Database
) -> None:
    stub = runtime_with_model(RemediationStub(response="no_evidence"))
    with pytest.raises(GatewayError) as caught:
        remediate(challenged_state, stub)
    assert caught.value.kind == "schema"
    with db.connection() as conn:
        responds = [
            c
            for c in list_llm_calls(conn, challenged_state.run_id)
            if c.prompt_name == "remediate/respond"
        ]
        rows = list_findings(conn, challenged_state.run_id)
    assert [c.schema_valid for c in responds] == [False, False, False]
    assert all(r["status"] == "open" and r["response"] is None for r in rows)


@pytest.mark.db
def test_round_with_nothing_accepted_does_not_rerender(
    challenged_state: RunState, runtime_with_model: RuntimeWithModel, deps: Deps
) -> None:
    state = challenged_state
    command = remediate(state, runtime_with_model(RemediationStub(response="dispute_all")))
    assert command.goto == "challenge" and command.update is not None
    after = apply_node(state, command)
    for name in ("document_sections", "document_md", "document_html", "completeness"):
        assert after.refs[name] == state.refs[name]
    findings = deps.store.get_model(after.refs["findings"].id, FindingsList).findings
    assert findings and all(f.status == "disputed" for f in findings)
    assert after.refs["responses"].summary["n_sections_regenerated"] == 0
    assert after.counters.remediation_rounds == 1


@pytest.mark.db
def test_remediate_replay_copies_responses_without_rerender(
    db: Database, completed_run_id: str, store: ArtifactStore, runtime: Runtime[Deps]
) -> None:
    # ``db`` truncates the tables, so it must come before ``completed_run_id``.
    state = make_run(db, store, SYNTHETIC_BRIEF, replay_of=completed_run_id)
    original = original_manifest(state, runtime.context).refs["responses"]
    state = apply_node(state, challenge(state, runtime))
    command = remediate(state, runtime)
    assert command.goto == "challenge" and command.update is not None
    update = command.update
    assert set(update["refs"]) == {"responses"}
    assert update["refs"]["responses"].id == original.id
    assert update["refs"]["responses"].summary == original.summary
    assert update["counters"] == state.counters.model_copy(update={"remediation_rounds": 1})
    copied = store.get_model(original.id, ResponsesList)
    findings = {f.id: f for f in store.get_model(state.refs["findings"].id, FindingsList).findings}
    with db.connection() as conn:
        rows = {r["id"]: r for r in list_findings(conn, state.run_id)}
        assert list_llm_calls(conn, state.run_id) == []
    assert copied.responses
    for response in copied.responses:
        row = rows[f"{state.run_id}/{response.finding_id}"]
        assert (row["status"], row["response"]) == (
            findings[response.finding_id].status,
            response.action,
        )


def test_validate_response_hook() -> None:
    aliases = {"plan": {"model_name": "m", "target": {"event": "e"}}, "psi": {"features": []}}

    def draft(decision: str, evidence: list[str], sections: list[str], action: str = "a") -> str:
        parsed = FindingResponseDraft.model_validate(
            {
                "decision": decision,
                "action": action,
                "evidence": evidence,
                "sections_to_regenerate": sections,
            }
        )
        return validate_response(parsed, aliases, TEMPLATE_IDS) or ""

    assert draft("dispute", ["[A:plan#model_name]"], []) == ""
    assert draft("dispute", ["[A:plan#target.event]", "monitoring"], []) == ""
    assert "evidence" in draft("dispute", [], [])
    assert "evidence" in draft("dispute", ["[A:plan#nope]", "[A:profile#n_rows]"], [])
    assert "nowhere" in draft("accept", [], ["monitoring", "nowhere"])
    assert draft("accept", [], ["monitoring"]) == ""
    assert "action" in draft("accept", [], [], action="  ")


def test_responses_summary_counts_the_cumulative_list() -> None:
    responses = [
        FindingResponse(
            finding_id="F-1", decision="accept", action="a", round=1, sections_regenerated=["x"]
        ),
        FindingResponse(finding_id="F-2", decision="dispute", action="d", round=1),
        FindingResponse(
            finding_id="F-1", decision="accept", action="a", round=2, sections_regenerated=["x"]
        ),
    ]
    assert responses_summary(responses, 2) == {
        "n_responses": 3,
        "n_accepted": 2,
        "n_disputed": 1,
        "n_sections_regenerated": 1,
        "round": 2,
    }
