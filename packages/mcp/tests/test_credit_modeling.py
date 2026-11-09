from collections.abc import Callable
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult
from modelforge_core.schemas import (
    SCORES_COLUMNS,
    BinningTables,
    CalibrationMap,
    FeatureSelection,
    GBMModel,
    Scorecard,
    StrictModel,
)
from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.common import ServerName
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

CallFn = Callable[[ServerName, str, StrictModel], CallToolResult]
FEATURES = [f"f_{i}" for i in range(1, 7)]


@pytest.fixture(scope="module")
def split_id(call: CallFn, dataset_id: str) -> str:
    result = call(
        "data-profiler",
        "make_splits",
        MakeSplitsInput(dataset_id=dataset_id, strategy="vintage", oot_vintages=[5], seed=42),
    )
    assert not result.is_error, result.content
    return ArtifactRef.model_validate(result.structured_content).id


@pytest.fixture(scope="module")
def fitted(call: CallFn, dataset_id: str, split_id: str) -> SimpleNamespace:
    binning = ArtifactRef.model_validate(
        call(
            "credit-modeling",
            "bin_features_woe",
            BinFeaturesInput(dataset_id=dataset_id, split_id=split_id, features=FEATURES),
        ).structured_content
    )
    selection = ArtifactRef.model_validate(
        call(
            "credit-modeling",
            "select_features",
            SelectFeaturesInput(
                dataset_id=dataset_id,
                split_id=split_id,
                binning_id=binning.id,
                iv_min=0.02,
                iv_max=0.5,
                corr_max=0.7,
                max_features=20,
            ),
        ).structured_content
    )
    fit = FitLogisticOutput.model_validate(
        call(
            "credit-modeling",
            "fit_logistic_pd",
            FitLogisticInput(
                dataset_id=dataset_id,
                split_id=split_id,
                binning_id=binning.id,
                selection_id=selection.id,
                model_name="m",
            ),
        ).structured_content
    )
    return SimpleNamespace(binning=binning, selection=selection, scorecard=fit.scorecard)


def test_bin_select_fit_chain(call, store, dataset_id, split_id) -> None:
    binning = ArtifactRef.model_validate(
        call(
            "credit-modeling",
            "bin_features_woe",
            BinFeaturesInput(dataset_id=dataset_id, split_id=split_id, features=FEATURES),
        ).structured_content
    )
    tables = store.get_model(binning.id, BinningTables)
    assert [f.feature for f in tables.features] == FEATURES and binning.summary["iv"]["f_1"] > 0.1
    selection = ArtifactRef.model_validate(
        call(
            "credit-modeling",
            "select_features",
            SelectFeaturesInput(
                dataset_id=dataset_id,
                split_id=split_id,
                binning_id=binning.id,
                iv_min=0.02,
                iv_max=0.5,
                corr_max=0.7,
                max_features=20,
            ),
        ).structured_content
    )
    assert set(selection.summary["selected"]) >= {"f_1", "f_2", "f_3"}
    fit = FitLogisticOutput.model_validate(
        call(
            "credit-modeling",
            "fit_logistic_pd",
            FitLogisticInput(
                dataset_id=dataset_id,
                split_id=split_id,
                binning_id=binning.id,
                selection_id=selection.id,
                model_name="m",
            ),
        ).structured_content
    )
    sc = store.get_model(fit.scorecard.id, Scorecard)
    merged = store.get_model(fit.selection.id, FeatureSelection)
    assert sc.fit_method == "stepwise_wrong_sign" and merged.selected == [
        f.feature for f in sc.features
    ]
    assert all(f.coefficient < 0 for f in sc.features)
    assert fit.scorecard.summary["features"] == merged.selected


def test_gbm_calibrate_score(call, store, dataset_id, split_id, fitted) -> None:
    gbm = ArtifactRef.model_validate(
        call(
            "credit-modeling",
            "fit_gbm_challenger",
            FitGbmInput(
                dataset_id=dataset_id,
                split_id=split_id,
                features=fitted.scorecard.summary["features"],
                model_name="g",
                seed=42,
            ),
        ).structured_content
    )
    assert gbm.summary["num_trees"] >= 1
    assert store.get_model(gbm.id, GBMModel).model_text.startswith("tree")
    cal = ArtifactRef.model_validate(
        call(
            "credit-modeling",
            "calibrate",
            CalibrateInput(model_id=fitted.scorecard.id, dataset_id=dataset_id, split_id=split_id),
        ).structured_content
    )
    assert store.get_model(cal.id, CalibrationMap).fitted_on == "val"
    assert cal.summary["method"] == "platt"
    champ = ArtifactRef.model_validate(
        call(
            "credit-modeling",
            "score",
            ScoreInput(
                model_id=fitted.scorecard.id,
                dataset_id=dataset_id,
                split_id=split_id,
                calibration_id=cal.id,
            ),
        ).structured_content
    )
    chal = ArtifactRef.model_validate(
        call(
            "credit-modeling",
            "score",
            ScoreInput(model_id=gbm.id, dataset_id=dataset_id, split_id=split_id),
        ).structured_content
    )
    frame = store.get_frame(champ.id)
    assert list(frame.columns) == list(SCORES_COLUMNS)
    assert set(frame["split"]) == {"train", "val", "oot"} and champ.summary["rows"]["oot"] > 0
    assert store.get_frame(chal.id)["pd"].between(0, 1).all()


def test_score_rejects_non_model_artifact(call, dataset_id, split_id) -> None:
    r = call(
        "credit-modeling",
        "score",
        ScoreInput(model_id=split_id, dataset_id=dataset_id, split_id=split_id),
    )
    assert r.is_error and "invalid_input" in r.content[0].text


def test_fit_without_stepwise_keeps_selection(call, store, dataset_id, split_id, fitted) -> None:
    fit = FitLogisticOutput.model_validate(
        call(
            "credit-modeling",
            "fit_logistic_pd",
            FitLogisticInput(
                dataset_id=dataset_id,
                split_id=split_id,
                binning_id=fitted.binning.id,
                selection_id=fitted.selection.id,
                model_name="m",
                stepwise=False,
            ),
        ).structured_content
    )
    assert fit.selection.id == fitted.selection.id
    assert store.get_model(fit.scorecard.id, Scorecard).fit_method == "single"
