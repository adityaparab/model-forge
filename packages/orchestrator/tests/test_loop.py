"""The challenge and remediation loop: re-review routing, the bound and early exits."""

import pytest
from langgraph.runtime import Runtime
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.challenge import challenge
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.remediate import remediate
from modelforge_orchestrator.schemas import Finding, FindingsList, RunState

from .conftest import RemediationStub, RuntimeWithModel, apply_node


@pytest.fixture
def documented_state(validated_state: RunState, runtime: Runtime[Deps]) -> RunState:
    return apply_node(validated_state, document(validated_state, runtime))


def _findings(store: ArtifactStore, state: RunState) -> list[Finding]:
    return store.get_model(state.refs["findings"].id, FindingsList).findings


@pytest.mark.db
def test_fake_loop_closes_remediated_findings_and_keeps_disputes(
    documented_state: RunState, runtime: Runtime[Deps], store: ArtifactStore
) -> None:
    state = documented_state
    first = challenge(state, runtime)
    assert first.goto == "remediate"
    state = apply_node(state, first)
    state = apply_node(state, remediate(state, runtime))
    command = challenge(state, runtime)
    assert command.goto == "gate_signoff"
    state = apply_node(state, command)
    assert (state.counters.challenge_rounds, state.counters.remediation_rounds) == (2, 1)
    findings = _findings(store, state)
    assert findings
    for finding in findings:
        assert (finding.status, finding.closed_round) in (("closed", 2), ("disputed", None))
    monitoring = next(f for f in findings if f.title.startswith("Monitoring"))
    governance = next(f for f in findings if f.title.startswith("Approver"))
    assert monitoring.status == "closed" and governance.status == "disputed"
    for finding in findings:
        if (finding.check_key or "").startswith("undiscussed_test:"):
            assert finding.status == "closed"


@pytest.mark.db
def test_loop_exits_with_zero_rounds_and_when_everything_is_disputed(
    documented_state: RunState,
    runtime: Runtime[Deps],
    runtime_with_model: RuntimeWithModel,
    store: ArtifactStore,
) -> None:
    review_only = documented_state.model_copy(update={"max_remediation_rounds": 0})
    command = challenge(review_only, runtime)
    assert command.goto == "gate_signoff"
    findings = _findings(store, apply_node(review_only, command))
    assert findings and all(f.status == "open" for f in findings)

    disputing = runtime_with_model(RemediationStub(response="dispute_all"))
    state = apply_node(documented_state, challenge(documented_state, disputing))
    state = apply_node(state, remediate(state, disputing))
    command = challenge(state, disputing)
    assert command.goto == "gate_signoff"
    state = apply_node(state, command)
    assert state.counters.challenge_rounds == 2
    findings = _findings(store, state)
    assert findings and all(f.status == "disputed" for f in findings)


@pytest.mark.db
def test_loop_stops_at_the_bound_with_findings_open(
    documented_state: RunState, runtime_with_model: RuntimeWithModel, store: ArtifactStore
) -> None:
    stubborn = runtime_with_model(RemediationStub(re_review_open=True))
    state = documented_state.model_copy(update={"max_remediation_rounds": 1})
    state = apply_node(state, challenge(state, stubborn))
    state = apply_node(state, remediate(state, stubborn))
    command = challenge(state, stubborn)
    assert command.goto == "gate_signoff"
    state = apply_node(state, command)
    assert state.counters.remediation_rounds == 1
    assert any(f.status == "open" for f in _findings(store, state))
