import pytest
from langgraph.runtime import Runtime
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.challenge import challenge
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.remediate import remediate
from modelforge_orchestrator.schemas import RunState

from .conftest import apply_node


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
    state = apply_node(state, challenge(state, runtime))
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
