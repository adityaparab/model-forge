import math

import pytest
from modelforge_core.schemas import (
    CalibrationTests,
    PointsScaling,
    Scorecard,
    ScorecardBin,
    ScorecardFeature,
    SplitManifest,
    Thresholds,
    ValidationReport,
)
from pydantic import ValidationError


def test_thresholds_defaults_match_spec() -> None:
    t = Thresholds()
    assert (t.auc_min, t.auc_warn, t.ks_min, t.gap_max) == (0.70, 0.65, 0.25, 0.05)
    assert (t.psi_warn, t.psi_fail, t.iv_min, t.iv_max, t.corr_max, t.benchmark_gap) == (
        0.10,
        0.25,
        0.02,
        0.5,
        0.7,
        0.03,
    )


def test_scorecard_round_trips_json() -> None:
    sc = Scorecard(
        model_name="m",
        intercept=-2.5,
        penalty="l2",
        c=1.0,
        n_rows=10,
        scaling=PointsScaling(),
        features=[
            ScorecardFeature(
                feature="f_1",
                coefficient=-0.8,
                bins=[
                    ScorecardBin(lower=None, upper=1.5, is_missing=False, woe=0.3, points=31),
                    ScorecardBin(lower=1.5, upper=None, is_missing=False, woe=-0.4, points=22),
                ],
            )
        ],
    )
    assert Scorecard.model_validate_json(sc.model_dump_json()) == sc


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PointsScaling(pdo=20, bogus=1)  # type: ignore[call-arg]


def test_validation_report_get_raises_on_unknown_id() -> None:
    report = ValidationReport(model_name="m", thresholds=Thresholds(), tests=[], overall="pass")
    with pytest.raises(KeyError):
        report.get("nope")


def test_split_manifest_ids_empty_when_absent() -> None:
    m = SplitManifest(
        strategy="stratified_random",
        seed=1,
        oot_available=False,
        oot_vintages=[],
        val_share=0.2,
        row_ids={"train": ["a"], "val": ["b"]},
        digests={"train": "x", "val": "y"},
    )
    assert m.ids("oot") == []


def test_non_finite_floats_round_trip_json() -> None:
    ct = CalibrationTests(
        split="val",
        alpha=0.05,
        n_grades=2,
        grades=[],
        hl_statistic=0.0,
        hl_p_value=float("nan"),
        expected_total=3.0,
        observed_total=0,
        eo_ratio=float("inf"),
    )
    again = CalibrationTests.model_validate_json(ct.model_dump_json())
    assert math.isnan(again.hl_p_value) and again.eo_ratio == float("inf")
