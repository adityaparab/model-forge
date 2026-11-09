"""Dataset profiling: per-feature summary statistics and target rates by vintage."""

from typing import cast

import numpy as np
import pandas as pd

from modelforge_core.datasets.base import feature_columns
from modelforge_core.schemas import FeatureProfile, ProfileArtifact


def _feature_profile(name: str, series: pd.Series) -> FeatureProfile:
    values = series.dropna().to_numpy(dtype="float64")
    stats: dict[str, float | None]
    if values.size == 0:
        stats = dict.fromkeys(("mean", "std", "min", "p25", "p50", "p75", "max"))
    else:
        p25, p50, p75 = np.quantile(values, [0.25, 0.5, 0.75])
        stats = {
            "mean": float(values.mean()),
            "std": float(values.std(ddof=1)) if values.size > 1 else 0.0,
            "min": float(values.min()),
            "p25": float(p25),
            "p50": float(p50),
            "p75": float(p75),
            "max": float(values.max()),
        }
    return FeatureProfile(
        name=name,
        dtype=str(series.dtype),
        missing_share=float(series.isna().mean()),
        n_unique=int(series.nunique(dropna=True)),
        **stats,
    )


def profile(frame: pd.DataFrame, *, dataset: str) -> ProfileArtifact:
    """Summarise a canonical frame: row counts, target rates by vintage, feature stats."""
    target = cast(pd.Series, frame["default_flag"]).to_numpy(dtype="float64")
    vintage = cast(pd.Series, frame["vintage"]).to_numpy()
    rows_by_vintage: dict[int, int] = {}
    target_rate_by_vintage: dict[int, float] = {}
    for v in np.unique(vintage):
        in_vintage = vintage == v
        rows_by_vintage[int(v)] = int(in_vintage.sum())
        target_rate_by_vintage[int(v)] = float(target[in_vintage].mean())
    features = feature_columns(frame)
    return ProfileArtifact(
        dataset=dataset,
        n_rows=len(frame),
        n_features=len(features),
        target_rate=float(target.mean()),
        rows_by_vintage=rows_by_vintage,
        target_rate_by_vintage=target_rate_by_vintage,
        features=[_feature_profile(name, cast(pd.Series, frame[name])) for name in features],
    )
