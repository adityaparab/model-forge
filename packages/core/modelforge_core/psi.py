"""Population stability index: quantile bucketing with an explicit missing bucket."""

from collections.abc import Sequence
from typing import cast

import numpy as np
import pandas as pd

from modelforge_core.datasets.base import feature_columns
from modelforge_core.schemas import Band, PSIEntry, PSIReport, Thresholds
from modelforge_core.standards import DEFAULT_N_BINS, DEFAULT_THRESHOLDS, PSI_EPS


def psi_from_shares(expected: np.ndarray, actual: np.ndarray, *, eps: float = PSI_EPS) -> float:
    """PSI between two share vectors; zero shares are floored to ``eps`` on both sides."""
    e = np.where(expected == 0, eps, expected).astype(float)
    a = np.where(actual == 0, eps, actual).astype(float)
    return float(np.sum((a - e) * np.log(a / e)))


def quantile_edges(values: np.ndarray, n_bins: int) -> np.ndarray:
    """Unique interior quantile edges of the non-NaN values (empty if fewer than two distinct)."""
    arr = np.asarray(values, dtype=float)
    finite = arr[~np.isnan(arr)]
    if np.unique(finite).size < 2:
        return np.array([], dtype=float)
    return np.unique(np.quantile(finite, np.linspace(0, 1, n_bins + 1)[1:-1]))


def bucket_shares(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Shares over the ``len(edges)+1`` right-closed intervals then one NaN bucket."""
    arr = np.asarray(values, dtype=float)
    n_buckets = len(edges) + 2
    if arr.size == 0:
        return np.zeros(n_buckets, dtype=float)
    missing = np.isnan(arr)
    idx = np.searchsorted(edges, arr[~missing], side="left")
    counts = np.bincount(idx, minlength=n_buckets - 1).astype(float)
    counts = np.append(counts, float(missing.sum()))
    return counts / arr.size


def psi(
    expected: np.ndarray,
    actual: np.ndarray,
    *,
    n_bins: int = DEFAULT_N_BINS,
    edges: np.ndarray | None = None,
    eps: float = PSI_EPS,
) -> float:
    """PSI of ``actual`` against ``expected``; edges default to quantiles of ``expected``."""
    if edges is None:
        edges = quantile_edges(expected, n_bins)
    return psi_from_shares(bucket_shares(expected, edges), bucket_shares(actual, edges), eps=eps)


def psi_band(value: float, thresholds: Thresholds) -> Band:
    """Band a PSI value: pass below warn, warn below fail, else fail."""
    if value < thresholds.psi_warn:
        return "pass"
    if value < thresholds.psi_fail:
        return "warn"
    return "fail"


def compute_psi(
    frame: pd.DataFrame,
    *,
    base_vintages: Sequence[int],
    compare_vintages: Sequence[int],
    features: Sequence[str] | None = None,
    n_bins: int = DEFAULT_N_BINS,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> PSIReport:
    """Per-feature PSI of the compare vintages against the base vintages."""
    cols = list(features) if features is not None else feature_columns(frame)
    base = cast(pd.DataFrame, frame[frame["vintage"].isin(list(base_vintages))])
    compare = cast(pd.DataFrame, frame[frame["vintage"].isin(list(compare_vintages))])
    entries: list[PSIEntry] = []
    for col in cols:
        value = psi(
            cast(pd.Series, base[col]).to_numpy(dtype=float),
            cast(pd.Series, compare[col]).to_numpy(dtype=float),
            n_bins=n_bins,
        )
        entries.append(PSIEntry(feature=col, psi=value, band=psi_band(value, thresholds)))
    return PSIReport(
        base_vintages=list(base_vintages),
        compare_vintages=list(compare_vintages),
        n_bins=n_bins,
        entries=entries,
    )
