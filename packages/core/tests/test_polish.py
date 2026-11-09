from pathlib import Path
from typing import cast

import pandas as pd
import pytest
from modelforge_core.datasets.base import validate_canonical
from modelforge_core.datasets.polish import (
    POLISH_ATTRIBUTE_DESCRIPTIONS,
    PolishBankruptcyAdapter,
    parse_arff,
)


def test_descriptions_has_64_entries_and_known_endpoints() -> None:
    assert len(POLISH_ATTRIBUTE_DESCRIPTIONS) == 64
    assert POLISH_ATTRIBUTE_DESCRIPTIONS[0] == "net profit / total assets"
    assert POLISH_ATTRIBUTE_DESCRIPTIONS[63] == "sales / fixed assets"


def test_parse_arff_reads_fixture_with_missing(fixtures: Path) -> None:
    frame = parse_arff(fixtures / "mini.arff")
    assert list(frame.columns) == ["Attr1", "Attr2", "Attr3", "class"]
    assert frame.shape == (6, 4)
    assert cast(pd.Series, frame["Attr2"]).isna().sum() == 1
    label = cast(pd.Series, frame["class"])
    assert label.dtype.kind == "i" and label.sum() == 3


@pytest.mark.slow
def test_polish_load_matches_published_counts(data_dir: Path) -> None:
    ds = PolishBankruptcyAdapter().load(data_dir)
    validate_canonical(ds.frame)
    assert ds.oot_capable is True
    assert len(ds.frame) == 43405
    assert int(cast(pd.Series, ds.frame["default_flag"]).sum()) == 2091
    assert ds.frame.groupby("vintage").size().to_dict() == {
        1: 7027,
        2: 10173,
        3: 10503,
        4: 9792,
        5: 5910,
    }
    assert ds.dictionary.feature_names == [f"f_{i}" for i in range(1, 65)]
    assert ds.dictionary.columns[0].original == "Attr1"
    assert ds.frame["obligor_id"].iloc[0] == "p1_00000"
