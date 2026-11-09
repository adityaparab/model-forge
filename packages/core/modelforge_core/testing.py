"""Synthetic canonical credit frame used across the core test suite."""

import numpy as np
import pandas as pd


def make_synthetic_credit_frame(
    n: int = 4000,
    *,
    seed: int = 0,
    n_features: int = 6,
    vintages: tuple[int, ...] = (1, 2, 3, 4, 5),
    missing_share: float = 0.05,
) -> pd.DataFrame:
    """Canonical-shaped synthetic credit frame with a known logistic default process.

    Draw order (fixed for reproducibility): f_1..f_n, Bernoulli uniforms, vintages, missing mask.
    """
    rng = np.random.default_rng(seed)
    features = {f"f_{j}": rng.normal(size=n) for j in range(1, n_features + 1)}
    coefficients = {"f_1": 0.7, "f_2": -0.6, "f_3": 0.4}
    logit = np.full(n, -2.6)
    for name, coef in coefficients.items():
        if name in features:
            logit = logit + coef * features[name]
    default_flag = (rng.random(n) < 1.0 / (1.0 + np.exp(-logit))).astype("int64")
    vintage = rng.choice(np.asarray(vintages), size=n).astype("int64")
    obligor_id = [f"s_{i:05d}" for i in range(n)]
    if "f_1" in features:
        missing = rng.choice(n, size=round(missing_share * n), replace=False)
        features["f_1"][missing] = np.nan
    return pd.DataFrame(
        {"obligor_id": obligor_id, "vintage": vintage, "default_flag": default_flag, **features}
    )
