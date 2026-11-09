import numpy as np
import pandas as pd
import pytest
from modelforge_core.datasets.base import (
    CanonicalSchemaError,
    sanitize_columns,
    validate_canonical,
)
from modelforge_core.schemas import ColumnEntry


def test_sanitize_renames_in_order_and_builds_dictionary() -> None:
    raw = pd.DataFrame(
        {
            "obligor_id": ["a", "b"],
            "vintage": [1, 1],
            "default_flag": [0, 1],
            "Attr2": [2.0, 3.0],
            "Attr1": [1, 2],
            "junk": ["x", "y"],
        }
    )
    frame, dictionary = sanitize_columns(
        raw, ["Attr1", "Attr2"], dataset="d", descriptions={"Attr1": "net profit / total assets"}
    )
    assert list(frame.columns) == ["obligor_id", "vintage", "default_flag", "f_1", "f_2"]
    assert frame["f_1"].dtype == "float64"
    assert dictionary.columns[0] == ColumnEntry(
        name="f_1", original="Attr1", description="net profit / total assets"
    )
    assert dictionary.columns[1].description is None
    assert dictionary.feature_names == ["f_1", "f_2"]


def test_validate_canonical_rejects_bad_default_flag() -> None:
    frame = pd.DataFrame({"obligor_id": ["a"], "vintage": [1], "default_flag": [2], "f_1": [0.1]})
    with pytest.raises(CanonicalSchemaError):
        validate_canonical(frame)


def test_validate_canonical_rejects_unnamed_feature() -> None:
    frame = pd.DataFrame(
        {"obligor_id": ["a"], "vintage": [1], "default_flag": [0], "income": [0.1]}
    )
    with pytest.raises(CanonicalSchemaError):
        validate_canonical(frame)


def test_validate_canonical_accepts_good_frame() -> None:
    frame = pd.DataFrame(
        {"obligor_id": ["a"], "vintage": [1], "default_flag": [0], "f_1": [0.1], "f_2": [np.nan]}
    )
    validate_canonical(frame)
