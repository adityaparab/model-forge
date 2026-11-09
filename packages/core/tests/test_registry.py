import pytest
from modelforge_core.datasets.registry import get_adapter


def test_registry_resolves_and_rejects() -> None:
    assert get_adapter("polish_bankruptcy").name == "polish_bankruptcy"
    with pytest.raises(KeyError):
        get_adapter("nope")
