import numpy as np
import pandas as pd
from modelforge_core.binning import fit_binning, transform_woe
from modelforge_core.logistic import fit_woe_logistic
from modelforge_core.schemas import PointsScaling
from modelforge_core.scorecard import from_json, predict_pd, predict_points, to_json


def test_scorecard_pd_matches_sklearn_probability(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2"], model_name="m")
    woe = transform_woe(synthetic, tables, ["f_1", "f_2"])
    logit = sc.intercept + sum(f.coefficient * woe[f.feature].to_numpy() for f in sc.features)
    expected = 1 / (1 + np.exp(-logit))
    np.testing.assert_allclose(predict_pd(sc, synthetic), expected, atol=1e-12)


def test_scorecard_json_round_trip_reproduces_pd(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2"], model_name="m")
    again = from_json(to_json(sc))
    assert again == sc
    np.testing.assert_allclose(predict_pd(again, synthetic), predict_pd(sc, synthetic), atol=0)


def test_points_scale_and_direction(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(
        synthetic,
        tables,
        ["f_1", "f_2"],
        model_name="m",
        scaling=PointsScaling(pdo=20, base_score=600, base_odds=50),
    )
    pts, pds = predict_points(sc, synthetic), predict_pd(sc, synthetic)
    assert pts.dtype.kind == "i"
    assert np.corrcoef(pts, pds)[0, 1] < -0.9
    # a pd equal to 1/51 (odds 1:50) should score about 600 points
    assert abs(float(np.interp(1 / 51, np.sort(pds), pts[np.argsort(pds)])) - 600) < 40
