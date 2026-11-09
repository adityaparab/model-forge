"""Pydantic models for every artifact the ModelForge toolkit produces."""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Type aliases ---

Split = Literal["train", "val", "oot"]
Band = Literal["pass", "warn", "fail"]
Status = Literal["pass", "warn", "fail", "not_applicable"]
Trend = Literal["increasing", "decreasing", "none"]

# --- Constants ---

SCORES_COLUMNS: tuple[str, ...] = ("obligor_id", "vintage", "default_flag", "split", "score", "pd")
ID_COLUMNS: tuple[str, ...] = ("obligor_id", "vintage", "default_flag")
FEATURE_PATTERN = re.compile(r"^f_\d+$")


class StrictModel(BaseModel):
    """Base for all artifacts: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", ser_json_inf_nan="strings")


# --- Column dictionary ---


class ColumnEntry(StrictModel):
    name: str
    original: str
    description: str | None = None


class ColumnDictionary(StrictModel):
    dataset: str
    columns: list[ColumnEntry]

    @property
    def feature_names(self) -> list[str]:
        return [c.name for c in self.columns if FEATURE_PATTERN.match(c.name)]


# --- Profiling ---


class FeatureProfile(StrictModel):
    name: str
    dtype: str
    missing_share: float
    n_unique: int
    mean: float | None
    std: float | None
    min: float | None
    p25: float | None
    p50: float | None
    p75: float | None
    max: float | None


class ProfileArtifact(StrictModel):
    dataset: str
    n_rows: int
    n_features: int
    target_rate: float
    rows_by_vintage: dict[int, int]
    target_rate_by_vintage: dict[int, float]
    features: list[FeatureProfile]


# --- Leakage ---


class LeakageFlag(StrictModel):
    feature: str
    kind: Literal["high_single_auc", "perfect_separation", "identifier_like", "constant"]
    value: float | None = None
    detail: str


class LeakageReport(StrictModel):
    dataset: str
    auc_threshold: float
    duplicate_rows: int
    flags: list[LeakageFlag]


# --- PSI ---


class PSIEntry(StrictModel):
    feature: str
    psi: float
    band: Band


class PSIReport(StrictModel):
    base_vintages: list[int]
    compare_vintages: list[int]
    n_bins: int
    entries: list[PSIEntry]


# --- Splits ---


class SplitManifest(StrictModel):
    strategy: Literal["vintage", "stratified_random"]
    seed: int
    oot_available: bool
    oot_vintages: list[int]
    val_share: float
    row_ids: dict[Split, list[str]]
    digests: dict[Split, str]

    def ids(self, split: Split) -> list[str]:
        return self.row_ids.get(split, [])


# --- Binning ---


class Bin(StrictModel):
    index: int
    lower: float | None
    upper: float | None
    is_missing: bool
    count: int
    events: int
    non_events: int
    event_rate: float
    woe: float
    iv_contribution: float


class FeatureBinning(StrictModel):
    feature: str
    method: Literal["quantile", "tree"]
    monotonic: bool
    trend: Trend
    iv: float
    bins: list[Bin]


class BinningTables(StrictModel):
    target: str
    n_rows: int
    smoothing: float
    features: list[FeatureBinning]

    def get(self, feature: str) -> FeatureBinning:
        for fb in self.features:
            if fb.feature == feature:
                return fb
        raise KeyError(feature)


# --- Feature selection ---


class DroppedFeature(StrictModel):
    feature: str
    reason: Literal[
        "iv_below_min", "iv_above_max", "correlated", "constant", "max_features", "wrong_sign"
    ]
    detail: str


class FeatureSelection(StrictModel):
    iv_min: float
    iv_max: float
    corr_max: float
    selected: list[str]
    dropped: list[DroppedFeature]
    max_features: int | None = None


# --- Scorecard ---


class PointsScaling(StrictModel):
    pdo: float = 20.0
    base_score: float = 600.0
    base_odds: float = 50.0


class ScorecardBin(StrictModel):
    lower: float | None
    upper: float | None
    is_missing: bool
    woe: float
    points: int


class ScorecardFeature(StrictModel):
    feature: str
    coefficient: float
    bins: list[ScorecardBin]


class Scorecard(StrictModel):
    model_name: str
    intercept: float
    features: list[ScorecardFeature]
    scaling: PointsScaling
    penalty: Literal["l2", "none"]
    c: float
    n_rows: int
    woe_convention: Literal["ln_good_over_bad"] = "ln_good_over_bad"
    fit_method: Literal["single", "stepwise_wrong_sign"] = "single"


# --- Challenger ---


class GBMModel(StrictModel):
    model_name: str
    features: list[str]
    params: dict[str, float | int | str | bool]
    num_trees: int
    seed: int
    model_text: str


# --- Calibration map ---


class CalibrationMap(StrictModel):
    method: Literal["platt", "isotonic", "none"]
    fitted_on: Split
    platt_a: float | None = None
    platt_b: float | None = None
    isotonic_x: list[float] | None = None
    isotonic_y: list[float] | None = None


# --- Discrimination ---


class SplitMetrics(StrictModel):
    split: Split
    n: int
    events: int
    auc: float
    auc_ci_low: float
    auc_ci_high: float
    gini: float
    ks: float
    ks_ci_low: float
    ks_ci_high: float


class DiscriminationMetrics(StrictModel):
    model_name: str
    score_col: str
    bootstrap_samples: int
    seed: int
    by_split: list[SplitMetrics]

    def get(self, split: Split) -> SplitMetrics | None:
        for m in self.by_split:
            if m.split == split:
                return m
        return None


# --- Calibration tests ---


class GradeTest(StrictModel):
    grade: int
    n: int
    expected_events: float
    observed_events: int
    expected_pd: float
    observed_rate: float
    p_value: float
    passed: bool


class CalibrationTests(StrictModel):
    split: Split
    alpha: float
    n_grades: int
    grades: list[GradeTest]
    hl_statistic: float
    hl_p_value: float
    expected_total: float
    observed_total: int
    eo_ratio: float


# --- Stability ---


class StabilityTests(StrictModel):
    base_split: Split
    compare_split: Split
    n_bins: int
    score_psi: float
    score_psi_band: Band
    feature_csi: list[PSIEntry]


# --- Sensitivity ---


class SignCheck(StrictModel):
    feature: str
    coefficient: float
    expected_sign: Literal["negative"]
    ok: bool


class MonotonicityCheck(StrictModel):
    feature: str
    trend: Trend
    monotone: bool


class BinShift(StrictModel):
    feature: str
    shift: int
    mean_abs_pd_delta: float
    max_abs_pd_delta: float


class SensitivityResults(StrictModel):
    sign_checks: list[SignCheck]
    monotonicity: list[MonotonicityCheck]
    bin_shifts: list[BinShift]
    all_signs_ok: bool
    all_monotone: bool


# --- Benchmark ---


class BenchmarkSplit(StrictModel):
    split: Split
    champion_auc: float
    challenger_auc: float
    auc_delta: float
    auc_delta_ci_low: float
    auc_delta_ci_high: float
    champion_ks: float
    challenger_ks: float
    ks_delta: float


class BenchmarkResults(StrictModel):
    champion: str
    challenger: str
    gap_threshold: float
    decided_on: Split
    by_split: list[BenchmarkSplit]
    champion_within_gap: bool


# --- Thresholds ---


class Thresholds(StrictModel):
    auc_min: float = 0.70
    auc_warn: float = 0.65
    ks_min: float = 0.25
    gap_max: float = 0.05
    gap_fail: float = 0.10
    psi_warn: float = 0.10
    psi_fail: float = 0.25
    calibration_alpha: float = 0.05
    grade_fail_share_warn: float = 0.20
    hl_p_min: float = 0.05
    eo_low: float = 0.80
    eo_high: float = 1.25
    iv_min: float = 0.02
    iv_max: float = 0.50
    corr_max: float = 0.70
    benchmark_gap: float = 0.03


# --- Validation report ---


class ValidationTest(StrictModel):
    test_id: str
    name: str
    status: Status
    value: float | None
    threshold: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(StrictModel):
    model_name: str
    thresholds: Thresholds
    tests: list[ValidationTest]
    overall: Band

    def get(self, test_id: str) -> ValidationTest:
        for t in self.tests:
            if t.test_id == test_id:
                return t
        raise KeyError(test_id)


# --- Modeling plan ---


class TargetDefinition(StrictModel):
    event: str
    horizon: str


class VintagePolicy(StrictModel):
    oot_vintages: list[int]


class FeaturePolicy(StrictModel):
    exclusions: list[str] = Field(default_factory=list)
    max_features: int = 20
    iv_min: float = 0.02
    iv_max: float = 0.5
    corr_max: float = 0.7


class Methodology(StrictModel):
    champion: Literal["woe_logistic"] = "woe_logistic"
    challenger: Literal["lightgbm"] = "lightgbm"


class ModelingPlan(StrictModel):
    model_name: str
    portfolio: str
    target: TargetDefinition
    vintage_policy: VintagePolicy
    feature_policy: FeaturePolicy
    methodology: Methodology
    thresholds: Thresholds
    regulatory_scope: list[str]
    intended_use: str
    tier: Literal[1, 2, 3]
