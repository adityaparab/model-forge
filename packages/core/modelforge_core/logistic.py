"""Logistic regression on WoE-transformed features, exported as a points scorecard."""

import math
from collections.abc import Sequence
from typing import Literal, cast

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from modelforge_core.binning import transform_woe
from modelforge_core.schemas import (
    BinningTables,
    PointsScaling,
    Scorecard,
    ScorecardBin,
    ScorecardFeature,
)


def bin_points(
    coefficient: float, woe: float, intercept: float, n_features: int, scaling: PointsScaling
) -> int:
    """Points for one bin: ``round(-(coef * woe + intercept / n) * factor + offset / n)``."""
    factor = scaling.pdo / math.log(2)
    offset = scaling.base_score - factor * math.log(scaling.base_odds)
    return int(round(-(coefficient * woe + intercept / n_features) * factor + offset / n_features))


def fit_woe_logistic(
    frame: pd.DataFrame,
    tables: BinningTables,
    features: Sequence[str],
    *,
    model_name: str,
    target: str = "default_flag",
    penalty: Literal["l2", "none"] = "l2",
    c: float = 1.0,
    scaling: PointsScaling | None = None,
) -> Scorecard:
    """Fit a logistic regression on the WoE of ``features`` and return it as a scorecard."""
    names = list(features)
    scaling = scaling if scaling is not None else PointsScaling()
    x = cast(pd.DataFrame, transform_woe(frame, tables, names)[names]).to_numpy(dtype=float)
    y = cast(pd.Series, frame[target]).to_numpy()
    # sklearn >= 1.8 deprecates ``penalty``: ``C=c`` (l1_ratio=0) is L2 and ``C=inf`` is none.
    model = LogisticRegression(
        C=c if penalty == "l2" else np.inf, l1_ratio=0.0, solver="lbfgs", max_iter=1000
    )
    model.fit(x, y)
    intercept = float(np.asarray(model.intercept_, dtype=float)[0])
    coefs = np.asarray(model.coef_, dtype=float)[0]
    n = len(names)
    scored: list[ScorecardFeature] = []
    for i, name in enumerate(names):
        coef = float(coefs[i])
        bins = [
            ScorecardBin(
                lower=b.lower,
                upper=b.upper,
                is_missing=b.is_missing,
                woe=b.woe,
                points=bin_points(coef, b.woe, intercept, n, scaling),
            )
            for b in tables.get(name).bins
        ]
        scored.append(ScorecardFeature(feature=name, coefficient=coef, bins=bins))
    return Scorecard(
        model_name=model_name,
        intercept=intercept,
        features=scored,
        scaling=scaling,
        penalty=penalty,
        c=c,
        n_rows=len(frame),
    )
