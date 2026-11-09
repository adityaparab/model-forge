"""Champion versus challenger benchmark: AUC/KS per split with a paired bootstrap CI."""

from typing import cast

import numpy as np
import pandas as pd

from modelforge_core.metrics import auc, ks
from modelforge_core.schemas import BenchmarkResults, BenchmarkSplit, Split
from modelforge_core.standards import DEFAULT_BOOTSTRAP_SAMPLES, DEFAULT_SEED

_SPLIT_ORDER: tuple[Split, ...] = ("train", "val", "oot")


def _paired_delta_ci(
    y: np.ndarray, champ: np.ndarray, chal: np.ndarray, *, n_samples: int, seed: int
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = y.size
    deltas: list[float] = []
    for _ in range(n_samples):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        if np.unique(yb).size < 2:
            continue
        deltas.append(auc(yb, chal[idx]) - auc(yb, champ[idx]))
    if len(deltas) < 2:
        return float("nan"), float("nan")
    low, high = np.percentile(np.asarray(deltas), [2.5, 97.5])
    return float(low), float(high)


def benchmark(
    champion_scores: pd.DataFrame,
    challenger_scores: pd.DataFrame,
    *,
    champion: str,
    challenger: str,
    gap_threshold: float,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> BenchmarkResults:
    """Compare two scores frames joined on ``obligor_id``; decide on OOT, else validation."""
    champ_frame = pd.DataFrame(
        {
            "obligor_id": champion_scores["obligor_id"],
            "default_flag": champion_scores["default_flag"],
            "split": champion_scores["split"],
            "pd_champion": champion_scores["pd"],
        }
    )
    chal_frame = pd.DataFrame(
        {"obligor_id": challenger_scores["obligor_id"], "pd_challenger": challenger_scores["pd"]}
    )
    merged = champ_frame.merge(chal_frame, on="obligor_id", how="inner")
    if merged.empty:
        raise ValueError("champion and challenger scores share no obligor_id")

    by_split: list[BenchmarkSplit] = []
    for split in _SPLIT_ORDER:
        rows = cast(pd.DataFrame, merged[merged["split"] == split])
        y = cast(pd.Series, rows["default_flag"]).to_numpy(dtype=int)
        if np.unique(y).size < 2:
            continue
        champ = cast(pd.Series, rows["pd_champion"]).to_numpy(dtype=float)
        chal = cast(pd.Series, rows["pd_challenger"]).to_numpy(dtype=float)
        champion_auc, challenger_auc = auc(y, champ), auc(y, chal)
        champion_ks, challenger_ks = ks(y, champ), ks(y, chal)
        low, high = _paired_delta_ci(y, champ, chal, n_samples=bootstrap_samples, seed=seed)
        by_split.append(
            BenchmarkSplit(
                split=split,
                champion_auc=champion_auc,
                challenger_auc=challenger_auc,
                auc_delta=challenger_auc - champion_auc,
                auc_delta_ci_low=low,
                auc_delta_ci_high=high,
                champion_ks=champion_ks,
                challenger_ks=challenger_ks,
                ks_delta=challenger_ks - champion_ks,
            )
        )

    present = {s.split: s for s in by_split}
    decided_on: Split
    if "oot" in present:
        decided_on = "oot"
    elif "val" in present:
        decided_on = "val"
    else:
        raise ValueError("benchmark needs an oot or val split with both classes")
    return BenchmarkResults(
        champion=champion,
        challenger=challenger,
        gap_threshold=gap_threshold,
        decided_on=decided_on,
        by_split=by_split,
        champion_within_gap=present[decided_on].auc_delta <= gap_threshold,
    )
