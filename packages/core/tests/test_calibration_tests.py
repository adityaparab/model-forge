import math

import numpy as np
import pandas as pd
from modelforge_core.calibration_tests import calibration_tests


def test_binomial_grades_on_perfect_calibration() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0.01, 0.4, 50000)
    y = (rng.random(50000) < p).astype(int)
    scores = pd.DataFrame(
        {
            "obligor_id": [str(i) for i in range(50000)],
            "vintage": 1,
            "default_flag": y,
            "split": "val",
            "score": p,
            "pd": p,
        }
    )
    ct = calibration_tests(scores, split="val")
    assert (
        ct.n_grades == 10
        and ct.grades[0].grade == 1
        and ct.grades[0].expected_pd < ct.grades[-1].expected_pd
    )
    assert sum(not g.passed for g in ct.grades) <= 2
    assert ct.hl_p_value > 0.01 and 0.95 < ct.eo_ratio < 1.05


def test_binomial_detects_underprediction() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0.01, 0.2, 20000)
    y = (rng.random(20000) < 2 * p).astype(int)
    scores = pd.DataFrame(
        {
            "obligor_id": [str(i) for i in range(20000)],
            "vintage": 1,
            "default_flag": y,
            "split": "val",
            "score": p,
            "pd": p,
        }
    )
    ct = calibration_tests(scores, split="val")
    assert sum(not g.passed for g in ct.grades) >= 8 and ct.eo_ratio < 0.6 and ct.hl_p_value < 0.001


def test_heavy_ties_collapse_grades_and_hl_is_nan() -> None:
    pdv = np.array([0.1] * 500 + [0.3] * 500)
    y = (np.random.default_rng(2).random(1000) < pdv).astype(int)
    scores = pd.DataFrame(
        {
            "obligor_id": [str(i) for i in range(1000)],
            "vintage": 1,
            "default_flag": y,
            "split": "val",
            "score": pdv,
            "pd": pdv,
        }
    )
    ct = calibration_tests(scores, split="val")
    assert ct.n_grades < 3 and math.isnan(
        ct.hl_p_value
    )  # qcut with duplicates="drop" collapses to 1 or 2 grades
