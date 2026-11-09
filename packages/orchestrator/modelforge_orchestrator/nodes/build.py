"""`build`: fit the WoE logistic champion and GBM challenger, calibrate, and score both."""

from typing import Any

from langgraph.runtime import Runtime
from modelforge_mcp.servers.credit_modeling import (
    CalibrateInput,
    FitGbmInput,
    FitLogisticInput,
    FitLogisticOutput,
    ScoreInput,
)

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import plan_of
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.steps import Step

SERVER = "credit-modeling"


def build(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "build") as step:
        plan = plan_of(state)
        dataset_id = state.refs["dataset"].id
        split_id = state.refs["split"].id
        fit: FitLogisticOutput = step.tool(
            SERVER,
            "fit_logistic_pd",
            FitLogisticInput(
                dataset_id=dataset_id,
                split_id=split_id,
                binning_id=state.refs["binning"].id,
                selection_id=state.refs["selection"].id,
                model_name=plan.model_name,
                stepwise=True,
            ),
        ).output
        champion = fit.scorecard
        challenger = step.tool(
            SERVER,
            "fit_gbm_challenger",
            FitGbmInput(
                dataset_id=dataset_id,
                split_id=split_id,
                features=champion.summary["features"],
                model_name=f"{plan.model_name}_gbm",
                seed=state.seed,
            ),
        ).refs[0]
        calibration = step.tool(
            SERVER,
            "calibrate",
            CalibrateInput(
                model_id=champion.id,
                dataset_id=dataset_id,
                split_id=split_id,
                fit_split="val",
                method="platt",
            ),
        ).refs[0]
        champion_scores = step.tool(
            SERVER,
            "score",
            ScoreInput(
                model_id=champion.id,
                dataset_id=dataset_id,
                split_id=split_id,
                calibration_id=calibration.id,
            ),
        ).refs[0]
        challenger_scores = step.tool(
            SERVER,
            "score",
            ScoreInput(model_id=challenger.id, dataset_id=dataset_id, split_id=split_id),
        ).refs[0]
        return step.complete(
            {
                "refs": {
                    "champion": champion,
                    "selection": fit.selection,
                    "challenger_model": challenger,
                    "calibration": calibration,
                    "champion_scores": champion_scores,
                    "challenger_scores": challenger_scores,
                }
            }
        )
