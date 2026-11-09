"""Scorecard bin lookup, prediction, and JSON serialization (the JSON is the model)."""

from collections.abc import Sequence
from typing import cast

import numpy as np
import pandas as pd

from modelforge_core.logistic import bin_points
from modelforge_core.schemas import Scorecard, ScorecardBin


def bin_index(values: pd.Series | np.ndarray, bins: Sequence[ScorecardBin]) -> np.ndarray:
    """Position of each value's bin; NaN maps to the missing bin, or ``-1`` if there is none."""
    arr = np.asarray(values, dtype=float)
    upper_edges = np.array(
        [
            b.upper
            for b in bins
            if not b.is_missing and b.upper is not None and np.isfinite(b.upper)
        ],
        dtype=float,
    )
    out = np.searchsorted(upper_edges, arr, side="left").astype(np.int64)
    missing_index = next((i for i, b in enumerate(bins) if b.is_missing), -1)
    out[np.isnan(arr)] = missing_index
    return out


def predict_pd(scorecard: Scorecard, frame: pd.DataFrame) -> np.ndarray:
    """Probability of default: logistic of intercept plus coefficient-weighted bin WoE."""
    logit = np.full(len(frame), scorecard.intercept, dtype=float)
    for feat in scorecard.features:
        idx = bin_index(cast(pd.Series, frame[feat.feature]), feat.bins)
        woe = np.array([b.woe for b in feat.bins], dtype=float)
        logit += feat.coefficient * np.where(idx >= 0, woe[np.clip(idx, 0, None)], 0.0)
    return (1.0 / (1.0 + np.exp(-logit))).astype(np.float64)


def predict_points(scorecard: Scorecard, frame: pd.DataFrame) -> np.ndarray:
    """Total scorecard points per row (int64); unbinnable NaN gets the neutral points."""
    n = len(scorecard.features)
    neutral = bin_points(0.0, 0.0, scorecard.intercept, n, scorecard.scaling)
    total = np.zeros(len(frame), dtype=np.int64)
    for feat in scorecard.features:
        idx = bin_index(cast(pd.Series, frame[feat.feature]), feat.bins)
        points = np.array([b.points for b in feat.bins], dtype=np.int64)
        total += np.where(idx >= 0, points[np.clip(idx, 0, None)], neutral)
    return total


def to_json(scorecard: Scorecard) -> str:
    return scorecard.model_dump_json(indent=2)


def from_json(text: str) -> Scorecard:
    return Scorecard.model_validate_json(text)
