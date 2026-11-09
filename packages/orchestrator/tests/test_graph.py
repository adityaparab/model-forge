import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from modelforge_lineage.models import GateDecision
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.graph import build_graph
from modelforge_orchestrator.schemas import GatePrompt, RunState


@pytest.mark.db
def test_graph_runs_to_first_gate_and_resumes(
    deps: Deps, checkpointer: PostgresSaver, synthetic_state: RunState
) -> None:
    graph = build_graph(checkpointer)
    config: RunnableConfig = {"configurable": {"thread_id": synthetic_state.run_id}}
    result = graph.invoke(synthetic_state, config, context=deps)
    prompt = GatePrompt.model_validate(result["__interrupt__"][0].value)
    assert prompt.gate == "scope" and "plan" in prompt.summary
    result = graph.invoke(
        Command(
            resume=GateDecision(gate="scope", decision="approve", approver="alice").model_dump(
                mode="json"
            )
        ),
        config,
        context=deps,
    )
    prompt = GatePrompt.model_validate(result["__interrupt__"][0].value)
    assert prompt.gate == "model"
    assert set(result["refs"]) >= {"dataset", "split", "binning", "champion", "validation"}
    result = graph.invoke(
        Command(
            resume=GateDecision(gate="model", decision="reject", approver="bob").model_dump(
                mode="json"
            )
        ),
        config,
        context=deps,
    )
    assert "__interrupt__" not in result and result["status"] == "rejected"
