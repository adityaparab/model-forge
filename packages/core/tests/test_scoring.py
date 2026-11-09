import numpy as np
import pandas as pd
from modelforge_core.binning import fit_binning
from modelforge_core.logistic import fit_woe_logistic
from modelforge_core.schemas import SCORES_COLUMNS
from modelforge_core.scorecard import predict_pd
from modelforge_core.scoring import score_frame
from modelforge_core.splits import make_splits


def test_score_frame_columns_and_splits(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2"], model_name="m")
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    scores = score_frame(synthetic, manifest, lambda f: predict_pd(sc, f))
    assert tuple(scores.columns) == SCORES_COLUMNS
    assert len(scores) == len(synthetic)
    assert set(scores["split"]) == {"train", "val", "oot"}
    assert np.array_equal(scores["score"].to_numpy(), scores["pd"].to_numpy())
    assert scores["pd"].between(0, 1).all()
