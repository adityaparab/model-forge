from typing import cast

import pytest
from modelforge_core.datasets.registry import get_adapter
from modelforge_core.datasets.synthetic import SyntheticCreditAdapter


def test_registry_resolves_and_rejects() -> None:
    assert get_adapter("polish_bankruptcy").name == "polish_bankruptcy"
    with pytest.raises(KeyError):
        get_adapter("nope")


def test_registry_passes_seed_to_seeded_adapters() -> None:
    assert cast(SyntheticCreditAdapter, get_adapter("synthetic_credit", seed=9)).seed == 9
    assert get_adapter("polish_bankruptcy").name == "polish_bankruptcy"
