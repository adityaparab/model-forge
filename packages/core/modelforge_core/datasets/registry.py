"""Resolve dataset adapters by name."""

from collections.abc import Callable
from pathlib import Path

from modelforge_core.datasets.base import DatasetAdapter, LoadedDataset
from modelforge_core.datasets.polish import PolishBankruptcyAdapter
from modelforge_core.datasets.taiwan import TaiwanBankruptcyAdapter

ADAPTERS: dict[str, Callable[[], DatasetAdapter]] = {
    "polish_bankruptcy": PolishBankruptcyAdapter,
    "taiwan_bankruptcy": TaiwanBankruptcyAdapter,
}


def get_adapter(name: str) -> DatasetAdapter:
    try:
        factory = ADAPTERS[name]
    except KeyError:
        raise KeyError(f"unknown dataset {name!r}; known: {sorted(ADAPTERS)}") from None
    return factory()


def load_dataset(name: str, cache_dir: Path) -> LoadedDataset:
    return get_adapter(name).load(cache_dir)
