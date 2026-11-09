"""Weight-of-evidence binning with information value.

Non-missing bins are right-closed intervals in ascending order, indexed ``0..k-1``; the first has
``lower=None`` and the last ``upper=None``. If the feature has any NaN in the fitting frame, a
missing bin is appended with index ``k``. WoE is ``ln(dist_good / dist_bad)`` with additive
smoothing on every bin, so higher WoE means lower risk.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.tree import DecisionTreeClassifier

from modelforge_core.schemas import Bin, BinningTables, FeatureBinning, Trend
from modelforge_core.standards import DEFAULT_MAX_BINS, DEFAULT_MIN_BIN_SHARE, WOE_SMOOTHING

Method = Literal["quantile", "tree"]


@dataclass
class _RawBin:
    """A bin before WoE: its interval and its counts."""

    lower: float | None
    upper: float | None
    count: int
    events: int

    @property
    def event_rate(self) -> float:
        return self.events / self.count if self.count else 0.0


# --- Edges ---


def _quantile_edges(x: np.ndarray, max_bins: int) -> np.ndarray:
    """Interior edges at data-valued quantiles, excluding any edge equal to the maximum."""
    if x.size == 0 or max_bins < 2:
        return np.array([], dtype=float)
    q = np.unique(np.quantile(x, np.linspace(0, 1, max_bins + 1), method="higher"))
    return q[q != x.max()]


def _tree_edges(x: np.ndarray, y: np.ndarray, max_bins: int, min_bin_share: float) -> np.ndarray:
    """Sorted unique split thresholds of a depth-unrestricted tree with ``max_bins`` leaves."""
    if x.size == 0 or max_bins < 2:
        return np.array([], dtype=float)
    tree = DecisionTreeClassifier(
        max_leaf_nodes=max_bins,
        min_samples_leaf=max(1, int(min_bin_share * len(x))),
        random_state=0,
    )
    tree.fit(x.reshape(-1, 1), y)
    thresholds = tree.tree_.threshold[tree.tree_.feature >= 0]  # type: ignore[attr-defined]
    return np.unique(np.asarray(thresholds, dtype=float))


def _initial_bins(x: np.ndarray, y: np.ndarray, edges: np.ndarray) -> list[_RawBin]:
    """Right-closed bins ``(-inf, e1], (e1, e2], ..., (ek, +inf)`` with their counts."""
    idx = np.searchsorted(edges, x, side="left")
    k = len(edges) + 1
    counts = np.bincount(idx, minlength=k)
    events = np.bincount(idx, weights=y, minlength=k)
    bounds: list[float | None] = [None, *(float(e) for e in edges), None]
    return [
        _RawBin(lower=bounds[i], upper=bounds[i + 1], count=int(counts[i]), events=int(events[i]))
        for i in range(k)
    ]


# --- Merging ---


def _merge(bins: list[_RawBin], i: int) -> list[_RawBin]:
    """Merge bin ``i`` with bin ``i + 1``."""
    left, right = bins[i], bins[i + 1]
    merged = _RawBin(
        lower=left.lower,
        upper=right.upper,
        count=left.count + right.count,
        events=left.events + right.events,
    )
    return [*bins[:i], merged, *bins[i + 2 :]]


def _merge_small_bins(bins: list[_RawBin], min_count: float) -> list[_RawBin]:
    """Merge empty bins rightward, then bins below ``min_count`` into the closer-rate neighbour."""
    while len(bins) > 1:
        empty = next((i for i, b in enumerate(bins) if b.count == 0), None)
        if empty is None:
            break
        bins = _merge(bins, empty if empty < len(bins) - 1 else empty - 1)
    while len(bins) > 1:
        small = [i for i, b in enumerate(bins) if b.count < min_count]
        if not small:
            break
        i = min(small, key=lambda j: bins[j].count)
        if i == 0:
            bins = _merge(bins, 0)
        elif i == len(bins) - 1:
            bins = _merge(bins, i - 1)
        else:
            rate = bins[i].event_rate
            left_gap = abs(bins[i - 1].event_rate - rate)
            right_gap = abs(bins[i + 1].event_rate - rate)
            bins = _merge(bins, i - 1 if left_gap < right_gap else i)
    return bins


def _trend_direction(x: np.ndarray, y: np.ndarray) -> int:
    """Sign of the Spearman correlation of ``x`` and ``y``; 0 if undefined or zero."""
    if np.unique(x).size < 2 or np.unique(y).size < 2:
        return 0
    stat = float(cast(float, spearmanr(x, y).statistic))  # type: ignore[attr-defined]
    if np.isnan(stat) or stat == 0.0:
        return 0
    return 1 if stat > 0 else -1


def _merge_monotonic(bins: list[_RawBin], direction: int) -> list[_RawBin]:
    """Repeatedly merge the first adjacent pair whose rates strictly violate ``direction``."""
    if direction == 0:
        return bins
    while len(bins) > 1:
        rates = [b.event_rate for b in bins]
        violation = next(
            (i for i in range(len(rates) - 1) if direction * (rates[i + 1] - rates[i]) < 0), None
        )
        if violation is None:
            break
        bins = _merge(bins, violation)
    return bins


# --- WoE and trend ---


def _woe_bins(raw: list[_RawBin], missing: _RawBin | None, smoothing: float) -> list[Bin]:
    """Smoothed WoE and IV contribution for every bin, missing bin last."""
    all_raw = [*raw, *([missing] if missing is not None else [])]
    events = np.array([b.events for b in all_raw], dtype=float)
    non_events = np.array([b.count - b.events for b in all_raw], dtype=float)
    good = non_events + smoothing
    bad = events + smoothing
    dist_good = good / good.sum()
    dist_bad = bad / bad.sum()
    woe = np.log(dist_good / dist_bad)
    iv = (dist_good - dist_bad) * woe
    return [
        Bin(
            index=i,
            lower=b.lower,
            upper=b.upper,
            is_missing=b is missing,
            count=b.count,
            events=b.events,
            non_events=b.count - b.events,
            event_rate=b.event_rate,
            woe=float(woe[i]),
            iv_contribution=float(iv[i]),
        )
        for i, b in enumerate(all_raw)
    ]


def _trend(rates: list[float]) -> Trend:
    """Direction of a sequence of event rates; a single bin has no trend."""
    if len(rates) < 2:
        return "none"
    pairs = list(zip(rates[:-1], rates[1:], strict=True))
    if all(a <= b for a, b in pairs):
        return "increasing"
    if all(a >= b for a, b in pairs):
        return "decreasing"
    return "none"


# --- Public API ---


def _bin_feature(
    values: np.ndarray,
    target: np.ndarray,
    feature: str,
    *,
    method: Method,
    max_bins: int,
    min_bin_share: float,
    monotonic: bool,
    smoothing: float,
) -> FeatureBinning:
    is_nan = np.isnan(values)
    x, y = values[~is_nan], target[~is_nan]
    if method == "quantile":
        edges = _quantile_edges(x, max_bins)
    else:
        edges = _tree_edges(x, y, max_bins, min_bin_share)
    raw = _merge_small_bins(_initial_bins(x, y, edges), min_bin_share * len(x))
    if monotonic:
        raw = _merge_monotonic(raw, _trend_direction(x, y))
    missing = None
    if is_nan.any():
        missing = _RawBin(
            lower=None, upper=None, count=int(is_nan.sum()), events=int(target[is_nan].sum())
        )
    bins = _woe_bins(raw, missing, smoothing)
    return FeatureBinning(
        feature=feature,
        method=method,
        monotonic=monotonic,
        trend=_trend([b.event_rate for b in raw]),
        iv=float(sum(b.iv_contribution for b in bins)),
        bins=bins,
    )


def fit_binning(
    frame: pd.DataFrame,
    features: Sequence[str],
    *,
    target: str = "default_flag",
    method: Method = "quantile",
    max_bins: int = DEFAULT_MAX_BINS,
    min_bin_share: float = DEFAULT_MIN_BIN_SHARE,
    monotonic: bool = True,
    smoothing: float = WOE_SMOOTHING,
) -> BinningTables:
    """Fit WoE binning tables for ``features`` against the binary ``target`` column."""
    y = cast(pd.Series, frame[target]).to_numpy(dtype=float)
    fitted = [
        _bin_feature(
            cast(pd.Series, frame[f]).to_numpy(dtype=float),
            y,
            f,
            method=method,
            max_bins=max_bins,
            min_bin_share=min_bin_share,
            monotonic=monotonic,
            smoothing=smoothing,
        )
        for f in features
    ]
    return BinningTables(target=target, n_rows=len(frame), smoothing=smoothing, features=fitted)


def assign_bins(values: pd.Series | np.ndarray, binning: FeatureBinning) -> np.ndarray:
    """Bin index per value; NaN maps to the missing bin, or ``-1`` if the binning has none."""
    arr = np.asarray(values, dtype=float)
    upper_edges = np.array(
        [b.upper for b in binning.bins if not b.is_missing and b.upper is not None], dtype=float
    )
    out = np.searchsorted(upper_edges, arr, side="left").astype(np.int64)
    missing_index = next((b.index for b in binning.bins if b.is_missing), -1)
    out[np.isnan(arr)] = missing_index
    return out


def woe_lookup(binning: FeatureBinning) -> np.ndarray:
    """WoE values indexed by bin index."""
    return np.array([b.woe for b in sorted(binning.bins, key=lambda b: b.index)], dtype=float)


def transform_woe(
    frame: pd.DataFrame, tables: BinningTables, features: Sequence[str] | None = None
) -> pd.DataFrame:
    """Copy of ``frame`` with each feature replaced by the WoE of its bin (``0.0`` for index -1)."""
    out = frame.copy()
    names = list(features) if features is not None else [fb.feature for fb in tables.features]
    for name in names:
        binning = tables.get(name)
        idx = assign_bins(cast(pd.Series, frame[name]), binning)
        lookup = woe_lookup(binning)
        out[name] = np.where(idx >= 0, lookup[np.clip(idx, 0, None)], 0.0)
    return out
