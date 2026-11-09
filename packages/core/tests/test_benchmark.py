import numpy as np
import pandas as pd
from modelforge_core.benchmark import benchmark
from modelforge_core.scoring import score_frame
from modelforge_core.splits import make_splits


def test_benchmark_prefers_oot_and_reports_gap(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])

    def true_logit(f: pd.DataFrame) -> np.ndarray:
        logit = -2.6 + 0.7 * f["f_1"].fillna(0) - 0.6 * f["f_2"] + 0.4 * f["f_3"]
        return np.asarray(1 / (1 + np.exp(-logit)), dtype=float)

    def weak(f: pd.DataFrame) -> np.ndarray:
        return np.asarray(1 / (1 + np.exp(-(-2.6 + 0.7 * f["f_1"].fillna(0)))), dtype=float)

    champ, chal = (
        score_frame(synthetic, manifest, weak),
        score_frame(synthetic, manifest, true_logit),
    )
    b = benchmark(champ, chal, champion="weak", challenger="true", gap_threshold=0.01, seed=0)
    assert b.decided_on == "oot" and [s.split for s in b.by_split] == ["train", "val", "oot"]
    oot = next(s for s in b.by_split if s.split == "oot")
    assert (
        oot.auc_delta > 0.01
        and oot.auc_delta_ci_low < oot.auc_delta < oot.auc_delta_ci_high
        and b.champion_within_gap is False
    )
    same = benchmark(chal, chal, champion="a", challenger="a", gap_threshold=0.01, seed=0)
    assert same.champion_within_gap is True and all(s.auc_delta == 0 for s in same.by_split)
