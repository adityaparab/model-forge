"""`features`: WoE-bin the candidate features and select the champion's feature pool."""

from typing import Any

from langgraph.runtime import Runtime
from modelforge_core.schemas import ColumnDictionary
from modelforge_mcp.servers.credit_modeling import BinFeaturesInput, SelectFeaturesInput

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import plan_of
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.steps import Step

SERVER = "credit-modeling"


def features(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "features") as step:
        policy = plan_of(state).feature_policy
        dataset_id = state.refs["dataset"].id
        split_id = state.refs["split"].id
        dictionary = deps.store.get_model(state.refs["dictionary"].id, ColumnDictionary)
        dropped = set(state.refs["leakage"].summary["flagged"]) | set(policy.exclusions)
        candidates = [name for name in dictionary.feature_names if name not in dropped]
        binning = step.tool(
            SERVER,
            "bin_features_woe",
            BinFeaturesInput(dataset_id=dataset_id, split_id=split_id, features=candidates),
        ).refs[0]
        selection = step.tool(
            SERVER,
            "select_features",
            SelectFeaturesInput(
                dataset_id=dataset_id,
                split_id=split_id,
                binning_id=binning.id,
                iv_min=policy.iv_min,
                iv_max=policy.iv_max,
                corr_max=policy.corr_max,
                max_features=policy.max_features,
            ),
        ).refs[0]
        return step.complete({"refs": {"binning": binning, "selection": selection}})
