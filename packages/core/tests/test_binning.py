import math
from typing import cast

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from modelforge_core.binning import fit_binning, transform_woe

from tests.synthetic import make_synthetic_credit_frame


def test_woe_golden_two_bins() -> None:
    frame = pd.DataFrame(
        {"f_1": [1, 1, 1, 1, 2, 2, 2, 2.0], "default_flag": [0, 0, 0, 1, 0, 1, 1, 1]}
    )
    tables = fit_binning(frame, ["f_1"], max_bins=2, min_bin_share=0.0, monotonic=True)
    b = tables.get("f_1")
    assert len(b.bins) == 2 and b.trend == "increasing"
    assert b.bins[0].woe == pytest.approx(math.log(7 / 3), abs=1e-6)
    assert b.bins[1].woe == pytest.approx(-math.log(7 / 3), abs=1e-6)
    assert b.iv == pytest.approx(0.677838, abs=1e-5)
    assert (b.bins[0].count, b.bins[0].events, b.bins[1].events) == (4, 1, 3)


def test_missing_gets_its_own_bin_and_counts_sum(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    f1 = tables.get("f_1")
    assert f1.bins[-1].is_missing and f1.bins[-1].count == int(
        cast(pd.Series, synthetic["f_1"]).isna().sum()
    )
    assert sum(b.count for b in f1.bins) == len(synthetic)
    assert not any(b.is_missing for b in tables.get("f_2").bins)


@given(seed=st.integers(0, 10_000))
@settings(max_examples=25, deadline=None)
def test_monotonic_binning_gives_monotone_event_rates(seed: int) -> None:
    frame = make_synthetic_credit_frame(2000, seed=seed, missing_share=0.0)
    for fb in fit_binning(frame, ["f_1", "f_2", "f_3"], monotonic=True).features:
        rates = [b.event_rate for b in fb.bins if not b.is_missing]
        assert rates == sorted(rates) or rates == sorted(rates, reverse=True)
        assert fb.trend in ("increasing", "decreasing")


def test_transform_woe_handles_unseen_missing_and_out_of_range(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_2"])  # f_2 has no missing in training
    scoring = pd.DataFrame({"f_2": [np.nan, 1e9, -1e9]})
    out = transform_woe(scoring, tables, ["f_2"])
    assert out["f_2"].iloc[0] == 0.0
    assert out["f_2"].iloc[1] == tables.get("f_2").bins[-1].woe
    assert out["f_2"].iloc[2] == tables.get("f_2").bins[0].woe
    assert not cast(pd.Series, out["f_2"]).isna().any()


def test_constant_feature_yields_single_bin_and_zero_iv(synthetic: pd.DataFrame) -> None:
    frame = synthetic.assign(f_7=1.0)
    fb = fit_binning(frame, ["f_7"]).get("f_7")
    assert (
        len([b for b in fb.bins if not b.is_missing]) == 1
        and fb.iv == pytest.approx(0.0, abs=1e-12)
        and fb.trend == "none"
    )


def test_tree_method_runs_and_respects_max_bins(synthetic: pd.DataFrame) -> None:
    fb = fit_binning(synthetic, ["f_1"], method="tree", max_bins=4).get("f_1")
    assert 1 <= len([b for b in fb.bins if not b.is_missing]) <= 4
