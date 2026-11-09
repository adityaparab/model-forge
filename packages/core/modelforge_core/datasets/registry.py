"""Resolve dataset adapters by name."""

from collections.abc import Callable
from pathlib import Path

from modelforge_core.datasets.base import DatasetAdapter, LoadedDataset
from modelforge_core.datasets.polish import PolishBankruptcyAdapter
from modelforge_core.datasets.synthetic import SyntheticCreditAdapter
from modelforge_core.datasets.taiwan import TaiwanBankruptcyAdapter
from modelforge_core.standards import DEFAULT_SEED

# Factories take the seed; adapters without randomness (Polish) ignore it.
ADAPTERS: dict[str, Callable[[int], DatasetAdapter]] = {
    "polish_bankruptcy": lambda seed: PolishBankruptcyAdapter(),
    "taiwan_bankruptcy": lambda seed: TaiwanBankruptcyAdapter(seed=seed),
    "synthetic_credit": lambda seed: SyntheticCreditAdapter(seed=seed),
}


def get_adapter(name: str, *, seed: int = DEFAULT_SEED) -> DatasetAdapter:
    try:
        factory = ADAPTERS[name]
    except KeyError:
        raise KeyError(f"unknown dataset {name!r}; known: {sorted(ADAPTERS)}") from None
    return factory(seed)


def load_dataset(name: str, cache_dir: Path, *, seed: int = DEFAULT_SEED) -> LoadedDataset:
    return get_adapter(name, seed=seed).load(cache_dir)
