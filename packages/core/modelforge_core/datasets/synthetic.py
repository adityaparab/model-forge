"""Seeded synthetic credit frame as a canonical dataset (no download, nothing cached)."""

from pathlib import Path

from modelforge_core.datasets.base import LoadedDataset, sanitize_columns, validate_canonical
from modelforge_core.testing import make_synthetic_credit_frame

_N_FEATURES = 6


class SyntheticCreditAdapter:
    name = "synthetic_credit"

    def __init__(self, *, seed: int = 42, n: int = 4000) -> None:
        self.seed = seed
        self.n = n

    def load(self, cache_dir: Path) -> LoadedDataset:
        """Generate the frame in memory; ``cache_dir`` is accepted for the protocol but unused."""
        raw = make_synthetic_credit_frame(self.n, seed=self.seed, n_features=_N_FEATURES)
        originals = [f"x_{k}" for k in range(1, _N_FEATURES + 1)]
        raw = raw.rename(columns={f"f_{k}": f"x_{k}" for k in range(1, _N_FEATURES + 1)})
        descriptions = {f"x_{k}": f"synthetic ratio {k}" for k in range(1, _N_FEATURES + 1)}
        frame, dictionary = sanitize_columns(
            raw, originals, dataset=self.name, descriptions=descriptions
        )
        validate_canonical(frame)
        return LoadedDataset(name=self.name, frame=frame, dictionary=dictionary, oot_capable=True)
