from typing import cast

import pandas as pd
import pytest
from modelforge_core.splits import apply_split, make_splits, split_digest


def test_vintage_split_holds_out_oot_and_stratifies(synthetic: pd.DataFrame) -> None:
    m = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5], val_share=0.25)
    assert m.oot_available and m.oot_vintages == [5]
    oot = apply_split(synthetic, m, "oot")
    assert set(oot["vintage"]) == {5}
    train, val = apply_split(synthetic, m, "train"), apply_split(synthetic, m, "val")
    assert len(train) + len(val) + len(oot) == len(synthetic)
    assert abs(len(val) / (len(train) + len(val)) - 0.25) < 0.01
    assert (
        abs(
            cast(pd.Series, train["default_flag"]).mean()
            - cast(pd.Series, val["default_flag"]).mean()
        )
        < 0.02
    )
    assert set(train["obligor_id"]).isdisjoint(val["obligor_id"])


def test_splits_are_deterministic_and_digested(synthetic: pd.DataFrame) -> None:
    a = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    b = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    assert a == b
    assert a.digests["train"] == split_digest(a.row_ids["train"]) and len(a.digests["train"]) == 64
    assert (
        make_splits(synthetic, strategy="vintage", seed=7, oot_vintages=[5]).row_ids["val"]
        != a.row_ids["val"]
    )


def test_stratified_random_has_no_oot(synthetic: pd.DataFrame) -> None:
    m = make_splits(synthetic, strategy="stratified_random", seed=1)
    assert m.oot_available is False and m.ids("oot") == []


def test_vintage_split_requires_non_empty_oot(synthetic: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        make_splits(synthetic, strategy="vintage", seed=1, oot_vintages=[99])
