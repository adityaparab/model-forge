import asyncio
import math
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.types import CallToolResult
from modelforge_core.schemas import (
    CalibrationTests,
    StabilityTests,
    StrictModel,
    Thresholds,
    ValidationReport,
)
from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.common import ServerName, json_float
from modelforge_mcp.registry import build_server
from modelforge_mcp.servers.credit_modeling import (
    BinFeaturesInput,
    CalibrateInput,
    FitGbmInput,
    FitLogisticInput,
    FitLogisticOutput,
    ScoreInput,
    SelectFeaturesInput,
)
from modelforge_mcp.servers.data_profiler import MakeSplitsInput
from modelforge_mcp.servers.validation import (
    BenchmarkInput,
    CalibrationTestsInput,
    DiscriminationInput,
    EvaluateThresholdsInput,
    SensitivityInput,
    StabilityInput,
)

CallFn = Callable[[ServerName, str, StrictModel], CallToolResult]
FEATURES = [f"f_{i}" for i in range(1, 7)]


def _ref(result: CallToolResult) -> ArtifactRef:
    assert not result.is_error, result.content
    return ArtifactRef.model_validate(result.structured_content)


@pytest.fixture(scope="module")
def ids(call: CallFn, dataset_id: str) -> SimpleNamespace:
    split = _ref(
        call(
            "data-profiler",
            "make_splits",
            MakeSplitsInput(dataset_id=dataset_id, strategy="vintage", oot_vintages=[5], seed=42),
        )
    ).id
    binning = _ref(
        call(
            "credit-modeling",
            "bin_features_woe",
            BinFeaturesInput(dataset_id=dataset_id, split_id=split, features=FEATURES),
        )
    ).id
    initial_selection = _ref(
        call(
            "credit-modeling",
            "select_features",
            SelectFeaturesInput(
                dataset_id=dataset_id,
                split_id=split,
                binning_id=binning,
                iv_min=0.02,
                iv_max=0.5,
                corr_max=0.7,
                max_features=20,
            ),
        )
    ).id
    fit_result = call(
        "credit-modeling",
        "fit_logistic_pd",
        FitLogisticInput(
            dataset_id=dataset_id,
            split_id=split,
            binning_id=binning,
            selection_id=initial_selection,
            model_name="m",
        ),
    )
    assert not fit_result.is_error, fit_result.content
    fit = FitLogisticOutput.model_validate(fit_result.structured_content)
    features: list[str] = fit.scorecard.summary["features"]
    gbm = _ref(
        call(
            "credit-modeling",
            "fit_gbm_challenger",
            FitGbmInput(
                dataset_id=dataset_id,
                split_id=split,
                features=features,
                model_name="g",
                seed=42,
            ),
        )
    ).id
    cal = _ref(
        call(
            "credit-modeling",
            "calibrate",
            CalibrateInput(model_id=fit.scorecard.id, dataset_id=dataset_id, split_id=split),
        )
    ).id
    scores = _ref(
        call(
            "credit-modeling",
            "score",
            ScoreInput(
                model_id=fit.scorecard.id,
                dataset_id=dataset_id,
                split_id=split,
                calibration_id=cal,
            ),
        )
    ).id
    chal_scores = _ref(
        call(
            "credit-modeling",
            "score",
            ScoreInput(model_id=gbm, dataset_id=dataset_id, split_id=split),
        )
    ).id
    return SimpleNamespace(
        dataset=dataset_id,
        split=split,
        binning=binning,
        selection=fit.selection.id,
        scorecard=fit.scorecard.id,
        gbm=gbm,
        features=features,
        scores=scores,
        chal_scores=chal_scores,
    )


@pytest.fixture(scope="module")
def report_inputs(call: CallFn, ids: SimpleNamespace) -> EvaluateThresholdsInput:
    disc = _ref(
        call(
            "validation",
            "discrimination_metrics",
            DiscriminationInput(scores_id=ids.scores, model_name="m", seed=42),
        )
    )
    cal = _ref(
        call(
            "validation",
            "calibration_tests",
            CalibrationTestsInput(scores_id=ids.scores, split="oot"),
        )
    )
    stab = _ref(
        call(
            "validation",
            "stability_tests",
            StabilityInput(
                scores_id=ids.scores,
                dataset_id=ids.dataset,
                binning_id=ids.binning,
                features=ids.features,
                compare_split="oot",
            ),
        )
    )
    sens = _ref(
        call(
            "validation",
            "sensitivity_analysis",
            SensitivityInput(
                model_id=ids.scorecard, binning_id=ids.binning, dataset_id=ids.dataset
            ),
        )
    )
    bench = _ref(
        call(
            "validation",
            "benchmark_models",
            BenchmarkInput(
                champion_scores_id=ids.scores,
                challenger_scores_id=ids.chal_scores,
                champion="m",
                challenger="g",
                gap_threshold=0.03,
                seed=42,
            ),
        )
    )
    return EvaluateThresholdsInput(
        model_name="m",
        thresholds=Thresholds(),
        discrimination_id=disc.id,
        calibration_id=cal.id,
        stability_id=stab.id,
        sensitivity_id=sens.id,
        benchmark_id=bench.id,
        selection_id=ids.selection,
        binning_id=ids.binning,
        oot_available=True,
    )


def test_validation_chain_produces_report(call, store, ids) -> None:
    disc = ArtifactRef.model_validate(
        call(
            "validation",
            "discrimination_metrics",
            DiscriminationInput(scores_id=ids.scores, model_name="m", seed=42),
        ).structured_content
    )
    assert set(disc.summary["auc"]) == {"train", "val", "oot"}
    assert 0.6 < disc.summary["auc"]["oot"] < 0.9
    cal = ArtifactRef.model_validate(
        call(
            "validation",
            "calibration_tests",
            CalibrationTestsInput(scores_id=ids.scores, split="oot"),
        ).structured_content
    )
    assert store.get_model(cal.id, CalibrationTests).split == "oot"
    stab = ArtifactRef.model_validate(
        call(
            "validation",
            "stability_tests",
            StabilityInput(
                scores_id=ids.scores,
                dataset_id=ids.dataset,
                binning_id=ids.binning,
                features=ids.features,
                compare_split="oot",
            ),
        ).structured_content
    )
    assert [e.feature for e in store.get_model(stab.id, StabilityTests).feature_csi] == ids.features
    sens = ArtifactRef.model_validate(
        call(
            "validation",
            "sensitivity_analysis",
            SensitivityInput(
                model_id=ids.scorecard, binning_id=ids.binning, dataset_id=ids.dataset
            ),
        ).structured_content
    )
    assert sens.summary["all_signs_ok"] is True
    bench = ArtifactRef.model_validate(
        call(
            "validation",
            "benchmark_models",
            BenchmarkInput(
                champion_scores_id=ids.scores,
                challenger_scores_id=ids.chal_scores,
                champion="m",
                challenger="g",
                gap_threshold=0.03,
                seed=42,
            ),
        ).structured_content
    )
    assert bench.summary["decided_on"] == "oot"
    report = ArtifactRef.model_validate(
        call(
            "validation",
            "evaluate_thresholds",
            EvaluateThresholdsInput(
                model_name="m",
                thresholds=Thresholds(),
                discrimination_id=disc.id,
                calibration_id=cal.id,
                stability_id=stab.id,
                sensitivity_id=sens.id,
                benchmark_id=bench.id,
                selection_id=ids.selection,
                binning_id=ids.binning,
                oot_available=True,
            ),
        ).structured_content
    )
    vr = store.get_model(report.id, ValidationReport)
    assert len(vr.tests) == 13 and report.summary["overall"] == vr.overall
    assert report.summary["statuses"]["sens.signs"] == "pass"


def test_evaluate_without_benchmark_and_sensitivity(call, store, ids, report_inputs) -> None:
    report = ArtifactRef.model_validate(
        call(
            "validation",
            "evaluate_thresholds",
            report_inputs.model_copy(update={"sensitivity_id": None, "benchmark_id": None}),
        ).structured_content
    )
    assert report.summary["statuses"]["bench.gap"] == "not_applicable"
    assert report.summary["statuses"]["sens.signs"] == "not_applicable"


def test_sensitivity_rejects_gbm(call, ids) -> None:
    r = call(
        "validation",
        "sensitivity_analysis",
        SensitivityInput(model_id=ids.gbm, binning_id=ids.binning, dataset_id=ids.dataset),
    )
    assert r.is_error and "invalid_input" in r.content[0].text


def test_json_float_encodes_non_finite() -> None:
    assert json_float(math.nan) == "NaN"
    assert json_float(math.inf) == "Infinity"
    assert json_float(-math.inf) == "-Infinity"
    assert json_float(0.25) == 0.25


def test_tool_versions_are_published(settings) -> None:
    async def _list():
        async with Client(build_server("validation", settings)) as c:
            return (await c.list_tools()).tools

    tools = asyncio.run(_list())
    assert {t.name for t in tools} == {
        "discrimination_metrics",
        "calibration_tests",
        "stability_tests",
        "sensitivity_analysis",
        "benchmark_models",
        "evaluate_thresholds",
    }
    assert all(t.meta == {"version": "1.0.0"} and t.output_schema is not None for t in tools)
