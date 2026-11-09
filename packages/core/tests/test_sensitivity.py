import pandas as pd
from modelforge_core.binning import fit_binning
from modelforge_core.logistic import fit_woe_logistic
from modelforge_core.sensitivity import sensitivity_analysis


def test_sensitivity_signs_monotonicity_and_shifts(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2", "f_3"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2", "f_3"], model_name="m")
    res = sensitivity_analysis(sc, tables, synthetic)
    assert res.all_signs_ok and res.all_monotone
    assert {(b.feature, b.shift) for b in res.bin_shifts} == {
        (f, k) for f in ("f_1", "f_2", "f_3") for k in (-1, 1)
    }
    assert all(b.max_abs_pd_delta >= b.mean_abs_pd_delta > 0 for b in res.bin_shifts)


def test_sensitivity_flags_positive_coefficient(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1"], model_name="m")
    flipped = sc.model_copy(
        update={"features": [sc.features[0].model_copy(update={"coefficient": 0.5})]}
    )
    assert sensitivity_analysis(flipped, tables, synthetic).all_signs_ok is False
