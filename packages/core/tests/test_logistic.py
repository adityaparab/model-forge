import pandas as pd
import pytest
from modelforge_core.binning import fit_binning
from modelforge_core.logistic import fit_woe_logistic, fit_woe_logistic_stepwise


def test_fit_produces_negative_coefficients_and_bins(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2", "f_3"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2", "f_3"], model_name="m")
    assert [f.feature for f in sc.features] == ["f_1", "f_2", "f_3"]
    assert all(f.coefficient < 0 for f in sc.features)
    assert len(sc.features[0].bins) == len(tables.get("f_1").bins)
    assert sc.n_rows == len(synthetic) and sc.penalty == "l2" and sc.c == 1.0


def test_stepwise_fit_has_negative_coefficients_and_partitions_features(
    synthetic: pd.DataFrame,
) -> None:
    features = [f"f_{i}" for i in range(1, 7)]
    tables = fit_binning(synthetic, features)
    sc, dropped = fit_woe_logistic_stepwise(synthetic, tables, features, model_name="m")
    kept = {f.feature for f in sc.features}
    removed = {d.feature for d in dropped}
    assert all(f.coefficient < 0 for f in sc.features)
    assert kept | removed == set(features) and not (kept & removed)


def test_stepwise_fit_drops_feature_with_inverted_woe(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    f2 = tables.get("f_2")
    inverted = f2.model_copy(
        update={"bins": [b.model_copy(update={"woe": -b.woe}) for b in f2.bins]}
    )
    bad_tables = tables.model_copy(update={"features": [tables.get("f_1"), inverted]})
    sc, dropped = fit_woe_logistic_stepwise(synthetic, bad_tables, ["f_1", "f_2"], model_name="m")
    assert [d.feature for d in dropped] == ["f_2"] and dropped[0].reason == "wrong_sign"
    assert dropped[0].detail.startswith("coefficient +") and "2-feature fit" in dropped[0].detail
    assert [f.feature for f in sc.features] == ["f_1"] and sc.features[0].coefficient < 0


def test_stepwise_fit_rejects_empty_features(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1"])
    with pytest.raises(ValueError):
        fit_woe_logistic_stepwise(synthetic, tables, [], model_name="m")


def test_stepwise_records_fit_method(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2", "f_3"])
    sc, _ = fit_woe_logistic_stepwise(synthetic, tables, ["f_1", "f_2", "f_3"], model_name="m")
    assert sc.fit_method == "stepwise_wrong_sign"
    assert fit_woe_logistic(synthetic, tables, ["f_1"], model_name="m").fit_method == "single"
