import numpy as np
import pandas as pd
import pytest
from modelforge_core.binning import fit_binning
from modelforge_core.calibration import apply_calibration, calibrate_split, fit_calibration
from modelforge_core.logistic import fit_woe_logistic
from modelforge_core.schemas import CalibrationMap
from modelforge_core.scorecard import predict_pd
from modelforge_core.scoring import score_frame
from modelforge_core.splits import apply_split, make_splits


def test_platt_recovers_identity_on_calibrated_scores() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0.01, 0.5, 20000)
    y = rng.random(20000) < p
    cal = fit_calibration(p, y.astype(int), method="platt", fitted_on="val")
    assert cal.platt_a == pytest.approx(1.0, abs=0.1) and cal.platt_b == pytest.approx(0.0, abs=0.1)
    np.testing.assert_allclose(apply_calibration(cal, p), p, atol=0.03)


def test_isotonic_is_monotone_and_bounded() -> None:
    rng = np.random.default_rng(1)
    s = rng.uniform(size=5000)
    y = (rng.random(5000) < s**2).astype(int)
    cal = fit_calibration(s, y, method="isotonic", fitted_on="val")
    grid = np.linspace(0, 1, 101)
    out = apply_calibration(cal, grid)
    assert np.all(np.diff(out) >= -1e-12) and out.min() >= 0 and out.max() <= 1


def test_none_is_identity_and_round_trips() -> None:
    cal = fit_calibration(np.array([0.1, 0.9]), np.array([0, 1]), method="none", fitted_on="val")
    assert CalibrationMap.model_validate_json(cal.model_dump_json()) == cal
    np.testing.assert_array_equal(
        apply_calibration(cal, np.array([0.2, 0.7])), np.array([0.2, 0.7])
    )


def test_score_frame_applies_calibration(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    cal = CalibrationMap(method="platt", fitted_on="val", platt_a=1.0, platt_b=1.0)
    scores = score_frame(synthetic, manifest, lambda f: np.full(len(f), 0.1), calibration=cal)
    assert np.allclose(scores["score"], 0.1) and np.allclose(
        scores["pd"], 1 / (1 + np.exp(-(np.log(0.1 / 0.9) + 1.0)))
    )


def test_calibrate_split_matches_manual_fit(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    train = apply_split(synthetic, manifest, "train")
    tables = fit_binning(train, ["f_1", "f_2"])
    sc = fit_woe_logistic(train, tables, ["f_1", "f_2"], model_name="m")
    val = apply_split(synthetic, manifest, "val")
    manual = fit_calibration(
        predict_pd(sc, val), val["default_flag"].to_numpy(), method="platt", fitted_on="val"
    )
    cal = calibrate_split(
        synthetic, manifest, lambda f: predict_pd(sc, f), split="val", method="platt"
    )
    assert cal == manual
