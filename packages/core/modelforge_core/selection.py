"""Feature selection: constant and IV filters, greedy WoE-correlation pruning, optional cap."""

from typing import cast

import pandas as pd

from modelforge_core.schemas import BinningTables, DroppedFeature, FeatureBinning, FeatureSelection


def select_features(
    tables: BinningTables,
    woe_frame: pd.DataFrame,
    *,
    iv_min: float,
    iv_max: float,
    corr_max: float,
    max_features: int | None = None,
) -> FeatureSelection:
    """Drop constant, low-IV and suspiciously high-IV features, then prune correlated ones.

    Survivors are walked in IV-descending order (feature name breaks ties); a candidate is dropped
    when its Pearson correlation on ``woe_frame`` with any already-kept feature exceeds
    ``corr_max`` in absolute value. If ``max_features`` is set, only the first ``max_features``
    survivors in that order are kept; the rest are recorded with reason ``max_features``.
    """
    dropped: list[DroppedFeature] = []
    survivors: list[FeatureBinning] = []
    for fb in tables.features:
        if sum(not b.is_missing for b in fb.bins) == 1:
            dropped.append(
                DroppedFeature(
                    feature=fb.feature, reason="constant", detail="single non-missing bin"
                )
            )
        elif fb.iv < iv_min:
            dropped.append(
                DroppedFeature(
                    feature=fb.feature,
                    reason="iv_below_min",
                    detail=f"IV {fb.iv:.3f} below {iv_min}",
                )
            )
        elif fb.iv > iv_max:
            dropped.append(
                DroppedFeature(
                    feature=fb.feature,
                    reason="iv_above_max",
                    detail=f"IV {fb.iv:.3f} above {iv_max}; suspicious, possible leakage",
                )
            )
        else:
            survivors.append(fb)

    selected: list[str] = []
    for fb in sorted(survivors, key=lambda f: (-f.iv, f.feature)):
        candidate = fb.feature
        conflict: tuple[str, float] | None = None
        for kept in selected:
            pair = cast(pd.DataFrame, woe_frame[[candidate, kept]])
            r = float(pair.corr(method="pearson").iloc[0, 1])
            if abs(r) > corr_max:
                conflict = (kept, r)
                break
        if conflict is None:
            selected.append(candidate)
        else:
            kept, r = conflict
            dropped.append(
                DroppedFeature(
                    feature=candidate,
                    reason="correlated",
                    detail=f"correlation {r:.3f} with kept feature {kept} exceeds {corr_max}",
                )
            )
    if max_features is not None and len(selected) > max_features:
        for rank, feature in enumerate(selected[max_features:], start=max_features + 1):
            dropped.append(
                DroppedFeature(
                    feature=feature,
                    reason="max_features",
                    detail=f"ranked {rank} by IV; cap is {max_features}",
                )
            )
        selected = selected[:max_features]
    return FeatureSelection(
        iv_min=iv_min,
        iv_max=iv_max,
        corr_max=corr_max,
        selected=selected,
        dropped=dropped,
        max_features=max_features,
    )
