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
    DroppedFeature,
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


def fit_woe_logistic_stepwise(
    frame: pd.DataFrame,
    tables: BinningTables,
    features: Sequence[str],
    *,
    model_name: str,
    target: str = "default_flag",
    penalty: Literal["l2", "none"] = "l2",
    c: float = 1.0,
    scaling: PointsScaling | None = None,
) -> tuple[Scorecard, list[DroppedFeature]]:
    """Fit, then repeatedly drop the feature with the largest non-negative coefficient and refit.

    On WoE inputs every coefficient should be negative; a coefficient ``>= 0`` is a wrong sign,
    usually from multicollinearity. Stops when all signs are right or one feature remains.
    """
    names = list(features)
    if not names:
        raise ValueError("fit_woe_logistic_stepwise needs at least one feature")
    dropped: list[DroppedFeature] = []
    while True:
        scorecard = fit_woe_logistic(
            frame,
            tables,
            names,
            model_name=model_name,
            target=target,
            penalty=penalty,
            c=c,
            scaling=scaling,
        )
        wrong = [f for f in scorecard.features if f.coefficient >= 0]
        if not wrong or len(names) == 1:
            return scorecard.model_copy(update={"fit_method": "stepwise_wrong_sign"}), dropped
        worst = max(wrong, key=lambda f: f.coefficient)
        dropped.append(
            DroppedFeature(
                feature=worst.feature,
                reason="wrong_sign",
                detail=f"coefficient {worst.coefficient:+.4f} in a {len(names)}-feature fit",
            )
        )
        names.remove(worst.feature)
