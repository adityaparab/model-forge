"""Leakage screening: single-feature separation, constants, identifier-like columns."""

from typing import cast

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from modelforge_core.datasets.base import feature_columns
from modelforge_core.schemas import LeakageFlag, LeakageReport
from modelforge_core.standards import LEAKAGE_AUC_THRESHOLD

_PERFECT = 1.0 - 1e-12


def _flag_feature(
    name: str, values: pd.Series, target: pd.Series, auc_threshold: float
) -> LeakageFlag | None:
    mask = values.notna().to_numpy()
    x = values.to_numpy(dtype="float64")[mask]
    y = target.to_numpy()[mask]
    n_non_missing = int(x.size)
    n_unique = int(np.unique(x).size)
    if n_unique <= 1:
        return LeakageFlag(
            feature=name,
            kind="constant",
            value=None,
            detail=(
                f"Feature takes {n_unique} distinct non-missing value(s), so it carries no signal."
            ),
        )
    if np.unique(y).size == 2:
        auc = float(roc_auc_score(y, x))
        single_auc = max(auc, 1.0 - auc)
        if single_auc >= _PERFECT:
            return LeakageFlag(
                feature=name,
                kind="perfect_separation",
                value=single_auc,
                detail=f"Feature alone separates the classes perfectly (AUC {single_auc:.4f}).",
            )
        if single_auc > auc_threshold:
            return LeakageFlag(
                feature=name,
                kind="high_single_auc",
                value=single_auc,
                detail=(
                    f"Single-feature AUC {single_auc:.4f} exceeds the leakage threshold "
                    f"{auc_threshold:.2f}."
                ),
            )
    if n_unique == n_non_missing and bool(np.all(np.mod(x, 1) == 0)):
        return LeakageFlag(
            feature=name,
            kind="identifier_like",
            value=None,
            detail=f"All {n_non_missing} non-missing values are distinct integers, like a row ID.",
        )
    return None


def check_leakage(
    frame: pd.DataFrame, *, dataset: str, auc_threshold: float = LEAKAGE_AUC_THRESHOLD
) -> LeakageReport:
    """Screen every feature for leakage signatures; at most one flag per feature."""
    target = cast(pd.Series, frame["default_flag"])
    features = feature_columns(frame)
    flags: list[LeakageFlag] = []
    for name in features:
        flag = _flag_feature(name, cast(pd.Series, frame[name]), target, auc_threshold)
        if flag is not None:
            flags.append(flag)
    return LeakageReport(
        dataset=dataset,
        auc_threshold=auc_threshold,
        duplicate_rows=int(frame.duplicated(subset=features).sum()),
        flags=flags,
    )
