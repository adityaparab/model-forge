"""Score calibration: Platt scaling, isotonic regression, or identity."""

from collections.abc import Callable
from typing import Literal, cast

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from modelforge_core.schemas import CalibrationMap, Split, SplitManifest
from modelforge_core.splits import apply_split

_EPS = 1e-6


def _logit(scores: np.ndarray) -> np.ndarray:
    s = np.clip(np.asarray(scores, dtype=np.float64), _EPS, 1 - _EPS)
    return np.log(s / (1 - s))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def fit_calibration(
    scores: np.ndarray,
    y: np.ndarray,
    *,
    method: Literal["platt", "isotonic", "none"],
    fitted_on: Split,
) -> CalibrationMap:
    """Fit a calibration map from raw scores to observed default rates."""
    s = np.asarray(scores, dtype=np.float64)
    target = np.asarray(y)
    if method == "platt":
        # Unpenalised fit: C=inf with l1_ratio=0 (sklearn >= 1.8 idiom; `penalty` is deprecated).
        lr = LogisticRegression(C=np.inf, l1_ratio=0.0, solver="lbfgs", max_iter=1000)
        lr.fit(_logit(s).reshape(-1, 1), target)
        return CalibrationMap(
            method="platt",
            fitted_on=fitted_on,
            platt_a=float(np.asarray(lr.coef_)[0, 0]),
            platt_b=float(np.asarray(lr.intercept_)[0]),
        )
    if method == "isotonic":
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(s, target)
        return CalibrationMap(
            method="isotonic",
            fitted_on=fitted_on,
            isotonic_x=[float(v) for v in iso.X_thresholds_],
            isotonic_y=[float(v) for v in iso.y_thresholds_],
        )
    return CalibrationMap(method="none", fitted_on=fitted_on)


def apply_calibration(cal: CalibrationMap, scores: np.ndarray) -> np.ndarray:
    """Map raw scores to calibrated PDs; raises ValueError if the map is incomplete."""
    s = np.asarray(scores, dtype=np.float64)
    if cal.method == "platt":
        if cal.platt_a is None or cal.platt_b is None:
            raise ValueError("platt calibration map requires platt_a and platt_b")
        return _sigmoid(cal.platt_a * _logit(s) + cal.platt_b)
    if cal.method == "isotonic":
        if cal.isotonic_x is None or cal.isotonic_y is None:
            raise ValueError("isotonic calibration map requires isotonic_x and isotonic_y")
        return np.interp(s, cal.isotonic_x, cal.isotonic_y)
    return s


def calibrate_split(
    frame: pd.DataFrame,
    manifest: SplitManifest,
    predict: Callable[[pd.DataFrame], np.ndarray],
    *,
    split: Split,
    method: Literal["platt", "isotonic", "none"],
) -> CalibrationMap:
    """Score the rows of ``split`` with ``predict`` and fit a calibration map on them."""
    rows = apply_split(frame, manifest, split)
    y = cast(pd.Series, rows["default_flag"]).to_numpy()
    return fit_calibration(predict(rows), y, method=method, fitted_on=split)
