"""`split`: deterministic train/val(/OOT) splits of the loaded dataset."""

from typing import Any

from langgraph.runtime import Runtime
from modelforge_mcp.servers.data_profiler import MakeSplitsInput

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import plan_of
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.steps import Step

VAL_SHARE = 0.2


def split(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "split") as step:
        dataset = state.refs["dataset"]
        oot = plan_of(state).vintage_policy.oot_vintages
        vintage = dataset.summary.get("oot_capable") is True and bool(oot)
        request = MakeSplitsInput(
            dataset_id=dataset.id,
            strategy="vintage" if vintage else "stratified_random",
            oot_vintages=sorted(oot) if vintage else [],
            val_share=VAL_SHARE,
            seed=state.seed,
        )
        call = step.tool("data-profiler", "make_splits", request)
        return step.complete({"refs": {"split": call.refs[0]}})
