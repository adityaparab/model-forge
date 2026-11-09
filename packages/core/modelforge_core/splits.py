"""Seeded train/validation/out-of-time splits with a hashed manifest."""

import hashlib
from collections.abc import Sequence
from typing import Literal, cast

import numpy as np
import pandas as pd

from modelforge_core.schemas import Split, SplitManifest


def split_digest(ids: Sequence[str]) -> str:
    """SHA-256 hex digest of the sorted ids joined by newlines."""
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def _stratified(frame: pd.DataFrame, *, seed: int, val_share: float) -> tuple[list[str], list[str]]:
    rng = np.random.default_rng(seed)
    flags = frame["default_flag"].to_numpy()
    ids = frame["obligor_id"].astype(str).to_numpy()
    train: list[str] = []
    val: list[str] = []
    for cls in (0, 1):
        class_ids = ids[flags == cls]
        permuted = [str(i) for i in rng.permutation(class_ids)]
        n_val = round(val_share * len(permuted))
        val.extend(permuted[:n_val])
        train.extend(permuted[n_val:])
    return train, val


def make_splits(
    frame: pd.DataFrame,
    *,
    strategy: Literal["vintage", "stratified_random"],
    seed: int,
    oot_vintages: Sequence[int] = (),
    val_share: float = 0.2,
) -> SplitManifest:
    """Build a deterministic split manifest; independent of input row order."""
    if not 0 <= val_share < 1:
        raise ValueError(f"val_share must be in [0, 1), got {val_share}")
    ordered = frame.sort_values("obligor_id", kind="stable").reset_index(drop=True)
    row_ids: dict[Split, list[str]] = {}
    if strategy == "vintage":
        in_oot = cast(pd.Series, ordered["vintage"]).isin(list(oot_vintages))
        if not bool(in_oot.any()):
            raise ValueError(f"no rows in oot_vintages {list(oot_vintages)}")
        if bool(in_oot.all()):
            raise ValueError("no rows remain for train/val after holding out oot_vintages")
        rest = cast(pd.DataFrame, ordered[~in_oot])
        train, val = _stratified(rest, seed=seed, val_share=val_share)
        row_ids = {
            "train": train,
            "val": val,
            "oot": [str(i) for i in ordered.loc[in_oot, "obligor_id"]],
        }
        oot_available = True
        oot_list = list(oot_vintages)
    else:
        train, val = _stratified(ordered, seed=seed, val_share=val_share)
        row_ids = {"train": train, "val": val}
        oot_available = False
        oot_list = []
    return SplitManifest(
        strategy=strategy,
        seed=seed,
        oot_available=oot_available,
        oot_vintages=oot_list,
        val_share=val_share,
        row_ids=row_ids,
        digests={s: split_digest(ids) for s, ids in row_ids.items()},
    )


def apply_split(frame: pd.DataFrame, manifest: SplitManifest, split: Split) -> pd.DataFrame:
    """Rows of ``frame`` in the given split, in frame order, with a fresh RangeIndex."""
    ids = manifest.ids(split)
    mask = cast(pd.Series, frame["obligor_id"]).astype(str).isin(ids)
    return cast(pd.DataFrame, frame[mask]).reset_index(drop=True)
