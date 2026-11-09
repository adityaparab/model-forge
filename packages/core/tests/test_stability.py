import pandas as pd
import pytest
from modelforge_core.binning import fit_binning
from modelforge_core.logistic import fit_woe_logistic
from modelforge_core.scorecard import predict_pd
from modelforge_core.scoring import score_frame
from modelforge_core.splits import apply_split, make_splits
from modelforge_core.stability import stability_tests


def test_stability_low_on_synthetic_and_detects_shift(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    tables = fit_binning(apply_split(synthetic, manifest, "train"), ["f_1", "f_2"])
    sc = fit_woe_logistic(
        apply_split(synthetic, manifest, "train"), tables, ["f_1", "f_2"], model_name="m"
    )
    scores = score_frame(synthetic, manifest, lambda f: predict_pd(sc, f))
    st = stability_tests(scores, synthetic, tables, compare_split="oot")
    assert (
        st.score_psi < 0.1
        and st.score_psi_band == "pass"
        and [e.feature for e in st.feature_csi] == ["f_1", "f_2"]
    )
    shifted = synthetic.copy()
    shifted.loc[shifted["vintage"] == 5, "f_2"] += 2.0
    st2 = stability_tests(
        score_frame(shifted, manifest, lambda f: predict_pd(sc, f)),
        shifted,
        tables,
        compare_split="oot",
    )
    assert (
        st2.score_psi > st.score_psi
        and next(e for e in st2.feature_csi if e.feature == "f_2").band == "fail"
    )


def test_stability_requires_rows_in_both_splits(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="stratified_random", seed=1)
    tables = fit_binning(synthetic, ["f_1"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1"], model_name="m")
    with pytest.raises(ValueError):
        stability_tests(
            score_frame(synthetic, manifest, lambda f: predict_pd(sc, f)),
            synthetic,
            tables,
            compare_split="oot",
        )
