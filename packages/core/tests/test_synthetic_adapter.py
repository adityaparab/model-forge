from pathlib import Path

from modelforge_core.datasets.base import validate_canonical
from modelforge_core.datasets.synthetic import SyntheticCreditAdapter


def test_synthetic_adapter_is_seeded_and_canonical(tmp_path: Path) -> None:
    a = SyntheticCreditAdapter(seed=3, n=500).load(tmp_path)
    b = SyntheticCreditAdapter(seed=3, n=500).load(tmp_path)
    validate_canonical(a.frame)
    assert a.frame.equals(b.frame) and a.oot_capable and len(a.frame) == 500
    assert a.dictionary.feature_names == [f"f_{i}" for i in range(1, 7)]
    assert not any(tmp_path.iterdir())
