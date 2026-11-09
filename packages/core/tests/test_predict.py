import numpy as np
import pandas as pd
import pytest
from modelforge_core.binning import fit_binning
from modelforge_core.gbm import fit_gbm, predict_gbm
from modelforge_core.logistic import fit_woe_logistic
from modelforge_core.predict import parse_model, predict_model
from modelforge_core.schemas import GBMModel, Scorecard
from modelforge_core.scorecard import predict_pd, to_json


def test_parse_and_predict_dispatch(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2"], model_name="m")
    gbm = fit_gbm(synthetic, ["f_1", "f_2"], model_name="g", seed=0)
    assert isinstance(parse_model(to_json(sc)), Scorecard)
    assert isinstance(parse_model(gbm.model_dump_json()), GBMModel)
    np.testing.assert_allclose(predict_model(sc, synthetic), predict_pd(sc, synthetic))
    np.testing.assert_allclose(predict_model(gbm, synthetic), predict_gbm(gbm, synthetic))
    with pytest.raises(ValueError):
        parse_model("{}")
