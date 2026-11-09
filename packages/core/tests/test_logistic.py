import pandas as pd
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
