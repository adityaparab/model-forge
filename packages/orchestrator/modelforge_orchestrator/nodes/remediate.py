"""`remediate`: a deterministic stand-in for the developer's round-1 responses to findings."""

from typing import Any

from langgraph.runtime import Runtime
from modelforge_lineage.findings import set_response

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import original_manifest
from modelforge_orchestrator.schemas import (
    FindingResponse,
    FindingsList,
    ResponsesList,
    RunState,
)
from modelforge_orchestrator.steps import Step

__all__ = ["ACTION", "build_responses", "remediate"]

ACTION = "Documented as a known limitation in the limitations section."


def build_responses(findings: FindingsList) -> ResponsesList:
    return ResponsesList(
        round=1,
        responses=[
            FindingResponse(finding_id=f.id, decision="accept", action=ACTION, round=1)
            for f in findings.findings
        ],
    )


def remediate(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "remediate") as step:
        if state.replay_of is not None:
            original = original_manifest(state, deps).refs["responses"]
            responses = deps.store.get_model(original.id, ResponsesList)
            ref = step.store(responses, kind="responses", summary=original.summary)
        else:
            findings = deps.store.get_model(state.refs["findings"].id, FindingsList)
            responses = build_responses(findings)
            ref = step.store(
                responses, kind="responses", summary={"n_responses": len(responses.responses)}
            )
        with deps.lineage.transaction() as conn:
            for response in responses.responses:
                set_response(
                    conn,
                    state.run_id,
                    f"{state.run_id}/{response.finding_id}",
                    status="accepted",
                    response=response.action,
                    response_evidence=[],
                )
        return step.complete(
            {
                "refs": {"responses": ref},
                "counters": state.counters.model_copy(update={"remediation_rounds": 1}),
            }
        )
