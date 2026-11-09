import pandas as pd
from modelforge_core.binning import fit_binning
from modelforge_core.logistic import fit_woe_logistic


def test_fit_produces_negative_coefficients_and_bins(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2", "f_3"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2", "f_3"], model_name="m")
    assert [f.feature for f in sc.features] == ["f_1", "f_2", "f_3"]
    assert all(f.coefficient < 0 for f in sc.features)
    assert len(sc.features[0].bins) == len(tables.get("f_1").bins)
    assert sc.n_rows == len(synthetic) and sc.penalty == "l2" and sc.c == 1.0
