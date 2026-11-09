from typing import cast

import numpy as np
import pandas as pd
import pytest
from modelforge_core.metrics import auc, bootstrap_ci, discrimination_metrics, gini, ks
from modelforge_core.scoring import score_frame
from modelforge_core.splits import make_splits


def test_auc_gini_ks_golden() -> None:
    y = np.array([0, 0, 1, 1])
    s = np.array([0.1, 0.4, 0.35, 0.8])
    assert (
        auc(y, s) == pytest.approx(0.75)
        and gini(y, s) == pytest.approx(0.5)
        and ks(y, s) == pytest.approx(0.5)
    )


def test_bootstrap_ci_brackets_point_estimate_and_is_seeded(synthetic: pd.DataFrame) -> None:
    y = synthetic["default_flag"].to_numpy()
    s = synthetic["f_1"].fillna(0).to_numpy()
    lo, hi = bootstrap_ci(y, s, auc, n_samples=200, seed=1)
    assert lo < auc(y, s) < hi and (hi - lo) < 0.1
    assert bootstrap_ci(y, s, auc, n_samples=200, seed=1) == (lo, hi)


def test_discrimination_metrics_per_split_and_single_class_skipped(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    scores = score_frame(
        synthetic,
        manifest,
        lambda f: cast(
            np.ndarray,
            1 / (1 + np.exp(-(-2.6 + 0.7 * f["f_1"].fillna(0) - 0.6 * f["f_2"] + 0.4 * f["f_3"]))),
        ),
    )
    dm = discrimination_metrics(scores, model_name="true_logit", seed=0)
    assert [m.split for m in dm.by_split] == ["train", "val", "oot"]
    assert all(0.70 < m.auc < 0.82 and m.auc_ci_low < m.auc < m.auc_ci_high for m in dm.by_split)
    no_oot_events = scores.copy()
    no_oot_events.loc[no_oot_events["split"] == "oot", "default_flag"] = 0
    assert discrimination_metrics(no_oot_events, model_name="x", seed=0).get("oot") is None
