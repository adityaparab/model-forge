from typing import Any, Literal, cast

import numpy as np
import pandas as pd
from modelforge_core.benchmark import benchmark
from modelforge_core.binning import fit_binning, transform_woe
from modelforge_core.calibration import fit_calibration
from modelforge_core.calibration_tests import calibration_tests
from modelforge_core.gbm import fit_gbm, predict_gbm
from modelforge_core.logistic import fit_woe_logistic
from modelforge_core.metrics import discrimination_metrics
from modelforge_core.schemas import Thresholds
from modelforge_core.scorecard import predict_pd
from modelforge_core.scoring import score_frame
from modelforge_core.selection import select_features
from modelforge_core.sensitivity import sensitivity_analysis
from modelforge_core.splits import apply_split, make_splits
from modelforge_core.stability import stability_tests
from modelforge_core.thresholds import evaluate_thresholds

_CACHE: dict[tuple[tuple[int, ...], str], dict[str, Any]] = {}


def _pipeline(
    synthetic: pd.DataFrame,
    *,
    oot_vintages: tuple[int, ...] = (5,),
    strategy: Literal["vintage", "stratified_random"] = "vintage",
) -> dict[str, Any]:
    """Run the core toolkit end to end; cached per argument set (the fixture is deterministic)."""
    key = (tuple(oot_vintages), strategy)
    if key not in _CACHE:
        manifest = make_splits(
            synthetic,
            strategy=strategy,
            seed=42,
            oot_vintages=list(oot_vintages) if strategy == "vintage" else [],
        )
        train = apply_split(synthetic, manifest, "train")
        val = apply_split(synthetic, manifest, "val")
        tables = fit_binning(train, [f"f_{i}" for i in range(1, 7)])
        selection = select_features(
            tables, transform_woe(train, tables), iv_min=0.02, iv_max=0.5, corr_max=0.7
        )
        features = selection.selected
        sc = fit_woe_logistic(train, tables, features, model_name="champion")
        gbm = fit_gbm(train, features, model_name="challenger", seed=42, val_frame=val)
        cal = fit_calibration(
            predict_pd(sc, val),
            cast(np.ndarray, val["default_flag"].to_numpy()),
            method="platt",
            fitted_on="val",
        )

        def champion_predict(f: pd.DataFrame) -> np.ndarray:
            return predict_pd(sc, f)

        def challenger_predict(f: pd.DataFrame) -> np.ndarray:
            return predict_gbm(gbm, f)

        champ_scores = score_frame(synthetic, manifest, champion_predict, calibration=cal)
        chal_scores = score_frame(synthetic, manifest, challenger_predict)
        eval_split = "oot" if manifest.oot_available else "val"
        _CACHE[key] = {
            "discrimination": discrimination_metrics(champ_scores, model_name="champion", seed=42),
            "calibration": calibration_tests(champ_scores, split=eval_split),
            "stability": stability_tests(champ_scores, synthetic, tables, compare_split=eval_split),
            "sensitivity": sensitivity_analysis(sc, tables, synthetic),
            "benchmark": benchmark(
                champ_scores,
                chal_scores,
                champion="champion",
                challenger="challenger",
                gap_threshold=0.03,
                seed=42,
            ),
            "selection": selection,
            "tables": tables,
        }
    return dict(_CACHE[key])


def test_report_on_synthetic_is_complete_and_passes(synthetic: pd.DataFrame) -> None:
    report = evaluate_thresholds(
        **_pipeline(synthetic), model_name="m", thresholds=Thresholds(), oot_available=True
    )
    assert {t.test_id for t in report.tests} == {
        "disc.auc.oot",
        "disc.auc.val",
        "disc.ks.oot",
        "disc.gap.train_oot",
        "cal.binomial",
        "cal.hl",
        "cal.eo",
        "stab.psi.score",
        "stab.csi.max",
        "sens.signs",
        "sens.monotonic",
        "bench.gap",
        "feat.iv_range",
    }
    assert (
        report.get("disc.auc.oot").status in ("pass", "warn")
        and report.get("sens.signs").status == "pass"
    )
    assert report.overall in ("pass", "warn")


def test_oot_tests_not_applicable_without_oot(synthetic: pd.DataFrame) -> None:
    inputs = _pipeline(synthetic, strategy="stratified_random")  # stability/calibration on val
    report = evaluate_thresholds(
        **inputs, model_name="m", thresholds=Thresholds(), oot_available=False
    )
    assert all(
        report.get(t).status == "not_applicable"
        for t in ("disc.auc.oot", "disc.ks.oot", "disc.gap.train_oot")
    )
    assert "out-of-time" in report.get("disc.auc.oot").message.lower()
    assert report.get("disc.auc.val").status != "not_applicable"


def test_bands_and_overall_worst(synthetic: pd.DataFrame) -> None:
    inputs = _pipeline(synthetic)
    strict = Thresholds(auc_min=0.99, auc_warn=0.98)
    report = evaluate_thresholds(**inputs, model_name="m", thresholds=strict, oot_available=True)
    assert report.get("disc.auc.oot").status == "fail" and report.overall == "fail"
    lenient = Thresholds(
        auc_min=0.50,
        auc_warn=0.40,
        ks_min=0.0,
        gap_max=1.0,
        gap_fail=1.0,
        psi_fail=9.0,
        psi_warn=8.0,
        calibration_alpha=0.0,
        hl_p_min=0.0,
        eo_low=0.0,
        eo_high=99.0,
        grade_fail_share_warn=1.0,
        benchmark_gap=1.0,
        iv_max=9.0,
    )
    assert (
        evaluate_thresholds(
            **inputs, model_name="m", thresholds=lenient, oot_available=True
        ).overall
        == "pass"
    )


def test_hl_nan_is_warn_with_message(synthetic: pd.DataFrame) -> None:
    inputs = _pipeline(synthetic)
    inputs["calibration"] = inputs["calibration"].model_copy(
        update={"hl_p_value": float("nan"), "n_grades": 2}
    )
    t = evaluate_thresholds(
        **inputs, model_name="m", thresholds=Thresholds(), oot_available=True
    ).get("cal.hl")
    assert t.status == "warn" and "grades" in t.message
    assert t.value is None


def test_sensitivity_evidence_lists_feature_names(synthetic: pd.DataFrame) -> None:
    report = evaluate_thresholds(
        **_pipeline(synthetic), model_name="m", thresholds=Thresholds(), oot_available=True
    )
    for test_id in ("sens.signs", "sens.monotonic"):
        assert isinstance(report.get(test_id).evidence["features"], list)


def test_negative_gap_message_says_oot_exceeds_train(synthetic: pd.DataFrame) -> None:
    inputs = _pipeline(synthetic)
    disc = inputs["discrimination"]
    inputs["discrimination"] = disc.model_copy(
        update={
            "by_split": [
                m.model_copy(update={"auc": 0.5}) if m.split == "train" else m
                for m in disc.by_split
            ]
        }
    )
    t = evaluate_thresholds(
        **inputs, model_name="m", thresholds=Thresholds(), oot_available=True
    ).get("disc.gap.train_oot")
    assert t.value is not None and t.value < 0
    assert t.message.startswith("Out-of-time AUC exceeds train AUC by ")


def test_single_class_oot_is_not_applicable_with_reason(synthetic: pd.DataFrame) -> None:
    inputs = _pipeline(synthetic)
    disc = inputs["discrimination"]
    inputs["discrimination"] = disc.model_copy(
        update={"by_split": [m for m in disc.by_split if m.split != "oot"]}
    )
    report = evaluate_thresholds(
        **inputs, model_name="m", thresholds=Thresholds(), oot_available=True
    )
    for test_id in ("disc.auc.oot", "disc.ks.oot", "disc.gap.train_oot"):
        t = report.get(test_id)
        assert t.status == "not_applicable"
        assert "out-of-time" in t.message.lower() and "single class" in t.message
