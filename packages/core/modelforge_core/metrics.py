"""Discrimination metrics: AUC, Gini, KS with seeded bootstrap confidence intervals, per split."""

from collections.abc import Callable
from typing import get_args

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, roc_curve

from modelforge_core.schemas import DiscriminationMetrics, Split, SplitMetrics
from modelforge_core.standards import DEFAULT_BOOTSTRAP_SAMPLES, DEFAULT_SEED


def auc(y: np.ndarray, s: np.ndarray) -> float:
    """Area under the ROC curve."""
    return float(roc_auc_score(y, s))


def gini(y: np.ndarray, s: np.ndarray) -> float:
    """Gini coefficient, ``2 * auc - 1``."""
    return 2 * auc(y, s) - 1


def ks(y: np.ndarray, s: np.ndarray) -> float:
    """Kolmogorov-Smirnov statistic: the maximum of ``tpr - fpr`` over the ROC curve."""
    fpr, tpr, _ = roc_curve(y, s)
    return float(np.max(tpr - fpr))


def bootstrap_ci(
    y: np.ndarray,
    s: np.ndarray,
    stat: Callable[[np.ndarray, np.ndarray], float],
    *,
    n_samples: int,
    seed: int,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap interval for ``stat``; resamples with a single class are skipped."""
    rng = np.random.default_rng(seed)
    vals: list[float] = []
    for _ in range(n_samples):
        idx = rng.integers(0, len(y), len(y))
        y_b = y[idx]
        if len(np.unique(y_b)) < 2:
            continue
        vals.append(stat(y_b, s[idx]))
    if len(vals) < 2:
        return float("nan"), float("nan")
    return float(np.percentile(vals, 100 * alpha / 2)), float(
        np.percentile(vals, 100 * (1 - alpha / 2))
    )


def discrimination_metrics(
    scores: pd.DataFrame,
    *,
    model_name: str,
    score_col: str = "pd",
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> DiscriminationMetrics:
    """One ``SplitMetrics`` per split (train, val, oot) that is present and has both classes."""
    by_split: list[SplitMetrics] = []
    split: Split
    for split in get_args(Split):
        rows = scores[scores["split"] == split]
        if len(rows) == 0:
            continue
        y = rows["default_flag"].to_numpy()
        if len(np.unique(y)) < 2:
            continue
        s = rows[score_col].to_numpy()
        auc_lo, auc_hi = bootstrap_ci(y, s, auc, n_samples=bootstrap_samples, seed=seed)
        ks_lo, ks_hi = bootstrap_ci(y, s, ks, n_samples=bootstrap_samples, seed=seed)
        by_split.append(
            SplitMetrics(
                split=split,
                n=len(rows),
                events=int(y.sum()),
                auc=auc(y, s),
                auc_ci_low=auc_lo,
                auc_ci_high=auc_hi,
                gini=gini(y, s),
                ks=ks(y, s),
                ks_ci_low=ks_lo,
                ks_ci_high=ks_hi,
            )
        )
    return DiscriminationMetrics(
        model_name=model_name,
        score_col=score_col,
        bootstrap_samples=bootstrap_samples,
        seed=seed,
        by_split=by_split,
    )
