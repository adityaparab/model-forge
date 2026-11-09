"""`validate`: run the validation battery on the scored models and evaluate the thresholds."""

from typing import Any

from langgraph.runtime import Runtime
from modelforge_mcp.servers.validation import (
    BenchmarkInput,
    CalibrationTestsInput,
    DiscriminationInput,
    EvaluateThresholdsInput,
    SensitivityInput,
    StabilityInput,
)

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import plan_of
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.steps import Step

SERVER = "validation"


def validate(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "validate") as step:
        plan = plan_of(state)
        refs = state.refs
        dataset_id = refs["dataset"].id
        binning_id = refs["binning"].id
        champion = refs["champion"]
        champion_scores_id = refs["champion_scores"].id
        oot_available: bool = refs["split"].summary["oot_available"]
        eval_split = "oot" if oot_available else "val"
        discrimination = step.tool(
            SERVER,
            "discrimination_metrics",
            DiscriminationInput(
                scores_id=champion_scores_id, model_name=plan.model_name, seed=state.seed
            ),
        ).refs[0]
        calibration_tests = step.tool(
            SERVER,
            "calibration_tests",
            CalibrationTestsInput(
                scores_id=champion_scores_id,
                split=eval_split,
                alpha=plan.thresholds.calibration_alpha,
            ),
        ).refs[0]
        stability = step.tool(
            SERVER,
            "stability_tests",
            StabilityInput(
                scores_id=champion_scores_id,
                dataset_id=dataset_id,
                binning_id=binning_id,
                features=champion.summary["features"],
                compare_split=eval_split,
                thresholds=plan.thresholds,
            ),
        ).refs[0]
        sensitivity = step.tool(
            SERVER,
            "sensitivity_analysis",
            SensitivityInput(model_id=champion.id, binning_id=binning_id, dataset_id=dataset_id),
        ).refs[0]
        benchmark = step.tool(
            SERVER,
            "benchmark_models",
            BenchmarkInput(
                champion_scores_id=champion_scores_id,
                challenger_scores_id=refs["challenger_scores"].id,
                champion=plan.model_name,
                challenger=f"{plan.model_name}_gbm",
                gap_threshold=plan.thresholds.benchmark_gap,
                seed=state.seed,
            ),
        ).refs[0]
        validation = step.tool(
            SERVER,
            "evaluate_thresholds",
            EvaluateThresholdsInput(
                model_name=plan.model_name,
                thresholds=plan.thresholds,
                discrimination_id=discrimination.id,
                calibration_id=calibration_tests.id,
                stability_id=stability.id,
                sensitivity_id=sensitivity.id,
                benchmark_id=benchmark.id,
                selection_id=refs["selection"].id,
                binning_id=binning_id,
                oot_available=oot_available,
            ),
        ).refs[0]
        return step.complete(
            {
                "refs": {
                    "discrimination": discrimination,
                    "calibration_tests": calibration_tests,
                    "stability": stability,
                    "sensitivity": sensitivity,
                    "benchmark": benchmark,
                    "validation": validation,
                }
            }
        )
