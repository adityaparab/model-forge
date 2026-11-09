"""Scorecard sensitivity: coefficient signs, WoE monotonicity, and bin-shift PD response."""

from collections.abc import Sequence
from typing import cast

import numpy as np
import pandas as pd

from modelforge_core.schemas import (
    BinningTables,
    BinShift,
    MonotonicityCheck,
    Scorecard,
    SensitivityResults,
    SignCheck,
)
from modelforge_core.scorecard import bin_index


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _contribution(coefficient: float, woe: np.ndarray, idx: np.ndarray) -> np.ndarray:
    return coefficient * np.where(idx >= 0, woe[np.clip(idx, 0, None)], 0.0)


def sensitivity_analysis(
    scorecard: Scorecard,
    tables: BinningTables,
    frame: pd.DataFrame,
    *,
    shifts: Sequence[int] = (-1, 1),
) -> SensitivityResults:
    """Sign and monotonicity checks plus PD deltas from shifting each feature's bins by ``k``."""
    sign_checks: list[SignCheck] = []
    monotonicity: list[MonotonicityCheck] = []
    indices: list[np.ndarray] = []
    woes: list[np.ndarray] = []
    contributions: list[np.ndarray] = []
    for feat in scorecard.features:
        sign_checks.append(
            SignCheck(
                feature=feat.feature,
                coefficient=feat.coefficient,
                expected_sign="negative",
                ok=feat.coefficient < 0,
            )
        )
        trend = tables.get(feat.feature).trend
        monotonicity.append(
            MonotonicityCheck(feature=feat.feature, trend=trend, monotone=trend != "none")
        )
        idx = bin_index(cast(pd.Series, frame[feat.feature]), feat.bins)
        woe = np.array([b.woe for b in feat.bins], dtype=float)
        indices.append(idx)
        woes.append(woe)
        contributions.append(_contribution(feat.coefficient, woe, idx))

    total = np.full(len(frame), scorecard.intercept, dtype=float)
    for contrib in contributions:
        total = total + contrib
    base_pd = _sigmoid(total)

    bin_shifts: list[BinShift] = []
    for feat, idx, woe, contrib in zip(
        scorecard.features, indices, woes, contributions, strict=True
    ):
        k_nm = sum(1 for b in feat.bins if not b.is_missing)
        movable = (idx >= 0) & (idx < k_nm)
        for k in shifts:
            shifted = np.where(movable, np.clip(idx + k, 0, k_nm - 1), idx)
            pd_shift = _sigmoid(total - contrib + _contribution(feat.coefficient, woe, shifted))
            delta = np.abs(pd_shift - base_pd)
            bin_shifts.append(
                BinShift(
                    feature=feat.feature,
                    shift=k,
                    mean_abs_pd_delta=float(np.mean(delta)),
                    max_abs_pd_delta=float(np.max(delta)),
                )
            )
    return SensitivityResults(
        sign_checks=sign_checks,
        monotonicity=monotonicity,
        bin_shifts=bin_shifts,
        all_signs_ok=all(s.ok for s in sign_checks),
        all_monotone=all(m.monotone for m in monotonicity),
    )
