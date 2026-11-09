"""Calibration tests: binomial test per PD grade, Hosmer-Lemeshow, expected-versus-observed."""

import numpy as np
import pandas as pd
from scipy.stats import binomtest, chi2

from modelforge_core.schemas import CalibrationTests, GradeTest, Split
from modelforge_core.standards import DEFAULT_N_GRADES

_P_FLOOR = 1e-12
_HL_EPS = 1e-12


def _grades(pd_values: np.ndarray, n_grades: int) -> np.ndarray:
    """PD-quantile grades starting at 1 (lowest PD); identical PDs collapse to a single grade."""
    try:
        labels = np.asarray(
            pd.qcut(pd_values, n_grades, labels=False, duplicates="drop"), dtype=float
        )
    except ValueError:
        return np.ones(len(pd_values), dtype=int)
    # pandas 3 returns all-NaN labels instead of raising when every PD is identical.
    if np.isnan(labels).all():
        return np.ones(len(pd_values), dtype=int)
    return labels.astype(int) + 1


def calibration_tests(
    scores: pd.DataFrame,
    *,
    split: Split,
    alpha: float = 0.05,
    n_grades: int = DEFAULT_N_GRADES,
) -> CalibrationTests:
    """Binomial test per grade, Hosmer-Lemeshow across grades, and the expected/observed ratio."""
    rows = scores[scores["split"] == split]
    if len(rows) == 0:
        raise ValueError(f"no rows for split {split!r}")
    pd_values = rows["pd"].to_numpy(dtype=float)
    y = rows["default_flag"].to_numpy(dtype=int)
    grade = _grades(pd_values, n_grades)

    grades: list[GradeTest] = []
    hl_statistic = 0.0
    for g in np.unique(grade):
        mask = grade == g
        n = int(mask.sum())
        observed = int(y[mask].sum())
        expected = float(pd_values[mask].sum())
        expected_pd = float(pd_values[mask].mean())
        p = min(max(expected_pd, _P_FLOOR), 1 - _P_FLOOR)
        p_value = float(binomtest(observed, n, p, alternative="two-sided").pvalue)
        grades.append(
            GradeTest(
                grade=int(g),
                n=n,
                expected_events=expected,
                observed_events=observed,
                expected_pd=expected_pd,
                observed_rate=observed / n,
                p_value=p_value,
                passed=p_value >= alpha,
            )
        )
        hl_statistic += (observed - expected) ** 2 / (expected * (1 - expected / n) + _HL_EPS)

    formed = len(grades)
    df = formed - 2
    hl_p_value = float(chi2.sf(hl_statistic, df)) if df >= 1 else float("nan")
    expected_total = float(pd_values.sum())
    observed_total = int(y.sum())
    eo_ratio = expected_total / observed_total if observed_total > 0 else float("inf")
    return CalibrationTests(
        split=split,
        alpha=alpha,
        n_grades=formed,
        grades=grades,
        hl_statistic=float(hl_statistic),
        hl_p_value=hl_p_value,
        expected_total=expected_total,
        observed_total=observed_total,
        eo_ratio=eo_ratio,
    )
