import numpy as np
import pandas as pd
from modelforge_core.gbm import fit_gbm, predict_gbm
from modelforge_core.schemas import GBMModel
from modelforge_core.splits import apply_split, make_splits


def test_gbm_fits_exports_and_reloads_identically(synthetic: pd.DataFrame) -> None:
    feats = [f"f_{i}" for i in range(1, 7)]
    model = fit_gbm(synthetic, feats, model_name="chal", seed=42)
    assert model.num_trees == 300 and model.model_text.startswith("tree")
    again = GBMModel.model_validate_json(model.model_dump_json())
    np.testing.assert_allclose(predict_gbm(again, synthetic), predict_gbm(model, synthetic), atol=0)


def test_gbm_is_deterministic_for_a_seed(synthetic: pd.DataFrame) -> None:
    feats = ["f_1", "f_2", "f_3"]
    a, b = (
        fit_gbm(synthetic, feats, model_name="c", seed=3),
        fit_gbm(synthetic, feats, model_name="c", seed=3),
    )
    assert a.model_text == b.model_text


def test_gbm_early_stopping_uses_val(synthetic: pd.DataFrame) -> None:
    m = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    model = fit_gbm(
        apply_split(synthetic, m, "train"),
        ["f_1", "f_2", "f_3"],
        model_name="c",
        seed=1,
        val_frame=apply_split(synthetic, m, "val"),
    )
    assert 1 <= model.num_trees <= 300


def test_gbm_handles_missing_natively(synthetic: pd.DataFrame) -> None:
    model = fit_gbm(synthetic, ["f_1"], model_name="c", seed=1)  # f_1 has NaN
    assert np.isfinite(predict_gbm(model, synthetic)).all()
