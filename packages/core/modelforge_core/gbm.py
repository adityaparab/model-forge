"""LightGBM challenger model, exported as LightGBM native text (never pickled)."""

from collections.abc import Mapping, Sequence
from typing import cast

import lightgbm
import numpy as np
import pandas as pd

from modelforge_core.schemas import GBMModel

DEFAULT_GBM_PARAMS: dict[str, float | int | str | bool] = {
    "objective": "binary",
    "n_estimators": 300,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 50,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "n_jobs": 1,
    "deterministic": True,
    "force_row_wise": True,
    "verbose": -1,
}


def fit_gbm(
    frame: pd.DataFrame,
    features: Sequence[str],
    *,
    model_name: str,
    seed: int,
    target: str = "default_flag",
    params: Mapping[str, float | int | str | bool] | None = None,
    val_frame: pd.DataFrame | None = None,
    early_stopping_rounds: int = 30,
) -> GBMModel:
    """Fit a LightGBM classifier on raw features (NaN handled natively).

    Early stopping runs only when ``val_frame`` is given; the exported text then
    holds exactly ``best_iteration_`` trees.
    """
    feats = list(features)
    merged: dict[str, float | int | str | bool] = {**DEFAULT_GBM_PARAMS, **(params or {})}
    # Typed kwargs cannot narrow a heterogeneous params dict; LightGBM validates values itself.
    clf = lightgbm.LGBMClassifier(**merged, random_state=seed)  # pyright: ignore[reportArgumentType]
    x = cast(pd.DataFrame, frame[feats])
    y = cast(pd.Series, frame[target]).to_numpy()
    if val_frame is not None:
        clf.fit(
            x,
            y,
            eval_X=(cast(pd.DataFrame, val_frame[feats]),),
            eval_y=(cast(pd.Series, val_frame[target]).to_numpy(),),
            callbacks=[lightgbm.early_stopping(early_stopping_rounds, verbose=False)],
        )
        best = int(clf.best_iteration_)
        model_text = clf.booster_.model_to_string(num_iteration=best)
        num_trees = best
    else:
        clf.fit(x, y)
        model_text = clf.booster_.model_to_string()
        num_trees = int(clf.booster_.num_trees())
    return GBMModel(
        model_name=model_name,
        features=feats,
        params=merged,
        num_trees=num_trees,
        seed=seed,
        model_text=model_text,
    )


def predict_gbm(model: GBMModel, frame: pd.DataFrame) -> np.ndarray:
    """Default probabilities from the model's native text, as float64."""
    booster = lightgbm.Booster(model_str=model.model_text)
    return np.asarray(booster.predict(cast(pd.DataFrame, frame[model.features])), dtype=np.float64)
