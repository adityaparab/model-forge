import pytest
from langgraph.runtime import Runtime
from modelforge_lineage.events import list_events
from modelforge_lineage.steps import list_artifacts, list_steps, list_tool_calls
from modelforge_mcp.servers.data_profiler import LoadDatasetInput, ProfileDatasetInput
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
