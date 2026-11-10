import pytest
from langgraph.runtime import Runtime
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.challenge import build_findings, challenge
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.remediate import build_responses, remediate
from modelforge_orchestrator.schemas import RunState


@pytest.mark.db
def test_findings_depend_on_calibration_status(validated_state: RunState, deps: Deps) -> None:
    base = build_findings(validated_state, deps.store)
    assert [f.id for f in base.findings][0] == "F-1" and base.findings[0].category == "data"
    validation = validated_state.refs["validation"]
    failing_summary = {
        **validation.summary,
        "statuses": {**validation.summary["statuses"], "cal.binomial": "fail"},
    }
    failing = validated_state.model_copy(
        update={
            "refs": {
                **validated_state.refs,
                "validation": validation.model_copy(update={"summary": failing_summary}),
            }
        }
    )
    assert [f.id for f in build_findings(failing, deps.store).findings] == ["F-1", "F-2"]
    responses = build_responses(base)
    assert all(r.decision == "accept" for r in responses.responses)
    assert len(responses.responses) == len(base.findings)


@pytest.mark.db
def test_document_challenge_remediate_nodes(
    deps: Deps, runtime: Runtime[Deps], validated_state: RunState
) -> None:
    state = validated_state.model_copy(
        update={"refs": {**validated_state.refs, **document(validated_state, runtime)["refs"]}}
    )
    assert (
        deps.store.get(state.refs["document_md"].id)
        .decode()
        .startswith("# synthetic_pd development document")
    )
    assert state.refs["completeness"].summary["missing_sections"] == []
    state = state.model_copy(update={"refs": {**state.refs, **challenge(state, runtime)["refs"]}})
    update = remediate(state, runtime)
    state = state.model_copy(
        update={"refs": {**state.refs, **update["refs"]}, "counters": update["counters"]}
    )
    assert state.counters.remediation_rounds == 1
    with deps.lineage.connection() as conn:
        rows = conn.execute(
            "select status, response from findings where run_id = %s", (state.run_id,)
        ).fetchall()
    assert rows and all(r["status"] == "accepted" and r["response"] for r in rows)
