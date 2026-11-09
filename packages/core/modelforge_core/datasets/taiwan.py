"""UCI Taiwanese bankruptcy prediction data (single CSV) as a canonical dataset."""

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from modelforge_core.datasets.base import LoadedDataset, sanitize_columns, validate_canonical
from modelforge_core.datasets.download import fetch

TAIWAN_URL = "https://archive.ics.uci.edu/static/public/572/taiwanese+bankruptcy+prediction.zip"
TAIWAN_SHA256 = "c346f5ad2618cb198e7ed8306cf2f31fe3bb2ec60acdbbe1736788d50f269aac"
TAIWAN_FILE = "data.csv"

_TARGET_COLUMN = "Bankrupt?"


def assign_pseudo_vintages(default_flag: np.ndarray, *, n_vintages: int, seed: int) -> np.ndarray:
    """Spread each class round-robin over vintages 1..n_vintages in a seeded random order."""
    rng = np.random.default_rng(seed)
    vintages = np.zeros(len(default_flag), dtype=np.int64)
    for cls in (0, 1):
        positions = rng.permutation(np.flatnonzero(default_flag == cls))
        vintages[positions] = np.arange(len(positions)) % n_vintages + 1
    return vintages


class TaiwanBankruptcyAdapter:
    name = "taiwan_bankruptcy"

    def __init__(self, *, seed: int = 42, n_pseudo_vintages: int = 5) -> None:
        self.seed = seed
        self.n_pseudo_vintages = n_pseudo_vintages

    def load(self, cache_dir: Path) -> LoadedDataset:
        root = cache_dir / "taiwan"
        path = root / TAIWAN_FILE
        if not path.exists():
            archive = fetch(TAIWAN_URL, TAIWAN_SHA256, root / "taiwan.zip")
            with zipfile.ZipFile(archive) as zf:
                path.write_bytes(zf.read(TAIWAN_FILE))

        raw = pd.read_csv(path)
        raw.columns = [str(col).strip() for col in raw.columns]
        feature_columns = [col for col in raw.columns if col != _TARGET_COLUMN]
        default_flag = raw[_TARGET_COLUMN].to_numpy(dtype=np.int64)
        raw["default_flag"] = default_flag
        raw["obligor_id"] = [f"t_{i:05d}" for i in range(len(raw))]
        raw["vintage"] = assign_pseudo_vintages(
            default_flag, n_vintages=self.n_pseudo_vintages, seed=self.seed
        )

        descriptions = {col: col for col in feature_columns}
        frame, dictionary = sanitize_columns(
            raw, feature_columns, dataset=self.name, descriptions=descriptions
        )
        validate_canonical(frame)
        return LoadedDataset(name=self.name, frame=frame, dictionary=dictionary, oot_capable=False)
