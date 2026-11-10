from pathlib import Path

import pytest
from langgraph.runtime import Runtime
from modelforge_gateway.prompts import PromptRegistry
from modelforge_gateway.schemas import CallMetadata, CompletionRequest, GatewayError, Message
from modelforge_lineage.events import list_events
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.steps import list_artifacts, list_steps, list_tool_calls
from modelforge_mcp.servers.data_profiler import LoadDatasetInput, ProfileDatasetInput
from modelforge_orchestrator.agent_schemas import QueryPlan
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.steps import Step, state_hash
from modelforge_orchestrator.tools import ToolError


@pytest.mark.db
def test_step_records_tool_call_artifacts_and_events(
    deps: Deps, runtime: Runtime[Deps], synthetic_state: RunState
) -> None:
    with Step(deps, synthetic_state, "profile") as step:
        call = step.tool(
            "data-profiler",
            "load_dataset",
            LoadDatasetInput(dataset_ref=synthetic_state.dataset_ref),
        )
        update = step.complete({"refs": {"dataset": call.refs[0]}})
    with deps.lineage.connection() as conn:
        steps_ = list_steps(conn, synthetic_state.run_id)
        calls = list_tool_calls(conn, synthetic_state.run_id)
        types = [e.type for e in list_events(conn, synthetic_state.run_id)]
        arts = list_artifacts(conn, synthetic_state.run_id)
    expected = state_hash(synthetic_state.model_copy(update={"refs": update["refs"]}))
    assert steps_[0].status == "succeeded" and steps_[0].output_state_hash == expected
    assert calls[0].artifact_ids == [r.id for r in call.refs]
    assert {a.kind for a in arts} == {"dataset", "column_dictionary"}
    assert types[-4:] == ["tool.called", "artifact.stored", "artifact.stored", "step.finished"]
    assert types[-5] == "step.started"


@pytest.mark.db
def test_step_failure_is_recorded_and_reraised(
    deps: Deps, runtime: Runtime[Deps], synthetic_state: RunState
) -> None:
    with pytest.raises(ToolError):
        with Step(deps, synthetic_state, "profile") as step:
            step.tool("data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id="0" * 64))
    with deps.lineage.connection() as conn:
        events = list_events(conn, synthetic_state.run_id)
        steps_ = list_steps(conn, synthetic_state.run_id)
    assert steps_[0].status == "failed"
    assert [e.type for e in events][-3:] == ["tool.failed", "error.recorded", "step.finished"]
    assert events[-2].payload["kind"] == "tool"
    assert events[-2].payload["error"]["code"] == "artifact_not_found"


QUERY_PROMPT = (
    "---\npurpose: document\nversion: 1\noutput_schema: QueryPlan\ndescription: d\n---\nPlan."
)


@pytest.mark.db
def test_step_llm_accumulates_cost_and_records_rows(
    deps_priced: Deps, synthetic_state: RunState, tmp_path: Path
) -> None:
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "queries.v1.md").write_text(QUERY_PROMPT)
    deps_priced.gateway.registry = PromptRegistry(tmp_path, deps_priced.lineage)
    with Step(deps_priced, synthetic_state, "document") as step:
        assert step.spent_usd == 0.0
        request = CompletionRequest(
            purpose="document",
            prompt_name="test/queries",
            prompt_version=1,
            system="You plan retrieval queries.",
            messages=[Message(role="user", content="Plan the queries for this section.")],
            output_schema=QueryPlan,
            metadata=step.call_metadata(),
        )
        results = [step.llm(request), step.llm(request)]
        assert step.call_metadata("s1") == CallMetadata(
            run_id=synthetic_state.run_id, step_id=step.record.id, section_id="s1"
        )
    assert all(isinstance(r.parsed, QueryPlan) for r in results)
    assert step.spent_usd == sum(r.cost_usd for r in results) > 0
    with deps_priced.lineage.connection() as conn:
        rows = list_llm_calls(conn, synthetic_state.run_id)
    assert len(rows) == 2 and {r.step_id for r in rows} == {step.record.id}


@pytest.mark.db
def test_step_records_gateway_error_as_llm_kind(deps: Deps, synthetic_state: RunState) -> None:
    step = Step(deps, synthetic_state, "scope")
    with pytest.raises(GatewayError):
        with step:
            raise GatewayError("cost_cap", "x", purpose="scope")
    assert step.last_error is not None and step.last_error.kind == "llm"
    assert step.last_error.error["kind"] == "cost_cap"
    assert step.last_error.error["purpose"] == "scope"
    with deps.lineage.connection() as conn:
        events = list_events(conn, synthetic_state.run_id)
    recorded = [e for e in events if e.type == "error.recorded"]
    assert len(recorded) == 1 and recorded[0].payload["kind"] == "llm"
    assert recorded[0].payload["error"]["message"] == "x"
