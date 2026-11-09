import shutil
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pytest
from modelforge_core.datasets.base import validate_canonical
from modelforge_core.datasets.taiwan import TaiwanBankruptcyAdapter, assign_pseudo_vintages
from modelforge_core.schemas import ColumnEntry


def test_pseudo_vintages_are_balanced_and_deterministic() -> None:
    y = np.array([0] * 90 + [1] * 10)
    v1 = assign_pseudo_vintages(y, n_vintages=5, seed=7)
    v2 = assign_pseudo_vintages(y, n_vintages=5, seed=7)
    assert np.array_equal(v1, v2)
    assert set(v1) == {1, 2, 3, 4, 5}
    assert all((v1[y == 1] == k).sum() == 2 for k in range(1, 6))


def test_taiwan_fixture_parses(fixtures: Path, tmp_path: Path) -> None:
    (tmp_path / "taiwan").mkdir()
    shutil.copy(fixtures / "mini_taiwan.csv", tmp_path / "taiwan" / "data.csv")  # cached: no fetch
    ds = TaiwanBankruptcyAdapter(seed=1, n_pseudo_vintages=2).load(tmp_path)
    validate_canonical(ds.frame)
    assert ds.oot_capable is False
    assert ds.dictionary.feature_names == ["f_1", "f_2", "f_3"]
    assert ds.dictionary.columns[2] == ColumnEntry(
        name="f_3", original="Net Income Flag", description="Net Income Flag"
    )
    assert cast(pd.Series, ds.frame["f_3"]).nunique() == 1


@pytest.mark.slow
def test_taiwan_load_matches_published_counts(data_dir: Path) -> None:
    ds = TaiwanBankruptcyAdapter().load(data_dir)
    assert len(ds.frame) == 6819
    assert int(cast(pd.Series, ds.frame["default_flag"]).sum()) == 220
    assert len(ds.dictionary.feature_names) == 95
    assert cast(pd.Series, ds.frame["obligor_id"]).iloc[0] == "t_00000"
