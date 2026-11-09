"""Population (score) and characteristic (feature) stability between two splits."""

from collections.abc import Sequence
from typing import cast

import numpy as np
import pandas as pd

from modelforge_core.binning import assign_bins
from modelforge_core.psi import psi, psi_band, psi_from_shares, quantile_edges
from modelforge_core.schemas import BinningTables, PSIEntry, Split, StabilityTests, Thresholds
from modelforge_core.standards import DEFAULT_N_BINS, DEFAULT_THRESHOLDS


def _split_rows(scores: pd.DataFrame, split: Split) -> pd.DataFrame:
    rows = cast(pd.DataFrame, scores[scores["split"] == split])
    if rows.empty:
        raise ValueError(f"scores has no rows in split {split!r}")
    return rows


def _bin_shares(idx: np.ndarray, n_bins: int) -> np.ndarray:
    """Shares over ``n_bins`` bins plus one trailing bucket for index ``-1``."""
    mapped = np.where(idx < 0, n_bins, idx)
    counts = np.bincount(mapped, minlength=n_bins + 1).astype(float)
    return counts / max(idx.size, 1)


def _merge_frame(rows: pd.DataFrame, frame: pd.DataFrame, split: Split) -> pd.DataFrame:
    merged = rows[["obligor_id"]].merge(frame, on="obligor_id", how="inner")
    if len(merged) != len(rows):
        raise ValueError(
            f"frame matched {len(merged)} of {len(rows)} scored rows in split {split!r} "
            "on obligor_id"
        )
    return merged


def stability_tests(
    scores: pd.DataFrame,
    frame: pd.DataFrame,
    tables: BinningTables,
    *,
    base_split: Split = "train",
    compare_split: Split,
    n_bins: int = DEFAULT_N_BINS,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    features: Sequence[str] | None = None,
) -> StabilityTests:
    """Score PSI on base-split pd quantiles and per-feature CSI over binning-table bins.

    ``features`` restricts CSI to those features, in the given order (default: every
    feature in ``tables``).
    """
    base = _split_rows(scores, base_split)
    compare = _split_rows(scores, compare_split)
    base_pd = cast(pd.Series, base["pd"]).to_numpy(dtype=float)
    compare_pd = cast(pd.Series, compare["pd"]).to_numpy(dtype=float)
    score_psi = psi(base_pd, compare_pd, edges=quantile_edges(base_pd, n_bins))

    base_frame = _merge_frame(base, frame, base_split)
    compare_frame = _merge_frame(compare, frame, compare_split)
    by_name = {b.feature: b for b in tables.features}
    names = list(by_name) if features is None else list(features)
    missing = [n for n in names if n not in by_name]
    if missing:
        raise ValueError(f"features not in binning tables: {missing}")
    entries: list[PSIEntry] = []
    for binning in (by_name[n] for n in names):
        b = assign_bins(cast(pd.Series, base_frame[binning.feature]), binning)
        c = assign_bins(cast(pd.Series, compare_frame[binning.feature]), binning)
        k = len(binning.bins)
        value = psi_from_shares(_bin_shares(b, k), _bin_shares(c, k))
        entries.append(
            PSIEntry(feature=binning.feature, psi=value, band=psi_band(value, thresholds))
        )
    return StabilityTests(
        base_split=base_split,
        compare_split=compare_split,
        n_bins=n_bins,
        score_psi=score_psi,
        score_psi_band=psi_band(score_psi, thresholds),
        feature_csi=entries,
    )
