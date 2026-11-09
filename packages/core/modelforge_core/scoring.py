"""Scoring frame: one row per obligor in the manifest, with raw score and PD."""

from collections.abc import Callable
from typing import cast

import numpy as np
import pandas as pd

from modelforge_core.calibration import apply_calibration
from modelforge_core.schemas import SCORES_COLUMNS, CalibrationMap, Split, SplitManifest
from modelforge_core.splits import apply_split

_SPLIT_ORDER: tuple[Split, ...] = ("train", "val", "oot")


def score_frame(
    frame: pd.DataFrame,
    manifest: SplitManifest,
    predict: Callable[[pd.DataFrame], np.ndarray],
    *,
    calibration: CalibrationMap | None = None,
) -> pd.DataFrame:
    """Score every split in the manifest; returns exactly ``SCORES_COLUMNS``."""
    parts: list[pd.DataFrame] = []
    for split in _SPLIT_ORDER:
        if split not in manifest.row_ids:
            continue
        sub = apply_split(frame, manifest, split)
        score = np.asarray(predict(sub), dtype=np.float64)
        pd_col = score if calibration is None else apply_calibration(calibration, score)
        parts.append(
            pd.DataFrame(
                {
                    "obligor_id": cast(pd.Series, sub["obligor_id"]).to_numpy(),
                    "vintage": cast(pd.Series, sub["vintage"]).to_numpy(),
                    "default_flag": cast(pd.Series, sub["default_flag"]).to_numpy(),
                    "split": split,
                    "score": score,
                    "pd": pd_col,
                },
                columns=list(SCORES_COLUMNS),
            )
        )
    if not parts:
        return pd.DataFrame({c: [] for c in SCORES_COLUMNS}, columns=list(SCORES_COLUMNS))
    return pd.concat(parts, ignore_index=True)
