"""Evaluate validation artifacts against ``Thresholds`` into a ``ValidationReport``.

Every status is recomputed from the raw values and the supplied ``Thresholds``; pass/fail
flags or bands stored on the artifacts are never copied, so ``Thresholds`` is the single
authority for the verdicts.
"""

import math

from modelforge_core.psi import psi_band
from modelforge_core.schemas import (
    Band,
    BenchmarkResults,
    BinningTables,
    CalibrationTests,
    DiscriminationMetrics,
    FeatureSelection,
    SensitivityResults,
    StabilityTests,
    Status,
    Thresholds,
    ValidationReport,
    ValidationTest,
)

_SEVERITY: dict[Band, int] = {"pass": 0, "warn": 1, "fail": 2}


def _fmt(value: float) -> str:
    return f"{value:.4f}"


def _higher_is_better(value: float, pass_at: float, warn_at: float) -> Band:
    """Pass at or above ``pass_at``, warn at or above ``warn_at``, else fail (NaN fails)."""
    if value >= pass_at:
        return "pass"
    if value >= warn_at:
        return "warn"
    return "fail"


def _lower_is_better(value: float, pass_at: float, warn_at: float) -> Band:
    """Pass at or below ``pass_at``, warn at or below ``warn_at``, else fail (NaN fails)."""
    if value <= pass_at:
        return "pass"
    if value <= warn_at:
        return "warn"
    return "fail"


def _not_applicable(test_id: str, name: str, threshold: str, message: str) -> ValidationTest:
    return ValidationTest(
        test_id=test_id,
        name=name,
        status="not_applicable",
        value=None,
        threshold=threshold,
        message=message,
    )


_NO_OOT = "No out-of-time split is available, so this test is not applicable."
_OOT_SINGLE_CLASS = (
    "The out-of-time split has a single class, so the metric is undefined and this test "
    "is not applicable."
)


def _no_oot_message(*, oot_available: bool) -> str:
    """Why an out-of-time test is skipped: no split at all, or a split with one class."""
    return _OOT_SINGLE_CLASS if oot_available else _NO_OOT


def _disc_auc_oot(
    d: DiscriminationMetrics, t: Thresholds, *, oot_available: bool
) -> ValidationTest:
    test_id, name = "disc.auc.oot", "Out-of-time AUC"
    threshold = f">= {t.auc_min:.2f} pass, >= {t.auc_warn:.2f} warn"
    oot = d.get("oot")
    if not oot_available or oot is None:
        return _not_applicable(
            test_id, name, threshold, _no_oot_message(oot_available=oot_available)
        )
    status = _higher_is_better(oot.auc, t.auc_min, t.auc_warn)
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=oot.auc,
        threshold=threshold,
        message=f"Out-of-time AUC is {_fmt(oot.auc)}, which is {status}.",
        evidence={
            "auc": oot.auc,
            "auc_ci_low": oot.auc_ci_low,
            "auc_ci_high": oot.auc_ci_high,
            "n": oot.n,
            "events": oot.events,
        },
    )


def _disc_auc_val(d: DiscriminationMetrics, t: Thresholds) -> ValidationTest:
    test_id, name = "disc.auc.val", "Validation AUC"
    threshold = f">= {t.auc_min:.2f} pass, >= {t.auc_warn:.2f} warn"
    val = d.get("val")
    if val is None:
        return _not_applicable(
            test_id, name, threshold, "No validation split metrics exist, so this test is skipped."
        )
    status = _higher_is_better(val.auc, t.auc_min, t.auc_warn)
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=val.auc,
        threshold=threshold,
        message=f"Validation AUC is {_fmt(val.auc)}, which is {status}.",
        evidence={
            "auc": val.auc,
            "auc_ci_low": val.auc_ci_low,
            "auc_ci_high": val.auc_ci_high,
            "n": val.n,
            "events": val.events,
        },
    )


def _disc_ks_oot(d: DiscriminationMetrics, t: Thresholds, *, oot_available: bool) -> ValidationTest:
    test_id, name = "disc.ks.oot", "Out-of-time KS"
    threshold = f">= {t.ks_min:.2f} pass, else fail"
    oot = d.get("oot")
    if not oot_available or oot is None:
        return _not_applicable(
            test_id, name, threshold, _no_oot_message(oot_available=oot_available)
        )
    status: Band = "pass" if oot.ks >= t.ks_min else "fail"
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=oot.ks,
        threshold=threshold,
        message=f"Out-of-time KS is {_fmt(oot.ks)}, which is {status}.",
        evidence={"ks": oot.ks, "ks_ci_low": oot.ks_ci_low, "ks_ci_high": oot.ks_ci_high},
    )


def _disc_gap_train_oot(
    d: DiscriminationMetrics, t: Thresholds, *, oot_available: bool
) -> ValidationTest:
    test_id, name = "disc.gap.train_oot", "Train to out-of-time AUC gap"
    threshold = f"<= {t.gap_max:.2f} pass, <= {t.gap_fail:.2f} warn"
    train, oot = d.get("train"), d.get("oot")
    if not oot_available or oot is None:
        return _not_applicable(
            test_id, name, threshold, _no_oot_message(oot_available=oot_available)
        )
    if train is None:
        return _not_applicable(
            test_id,
            name,
            threshold,
            "No train split metrics exist to compare with out-of-time, so this is skipped.",
        )
    gap = train.auc - oot.auc
    status = _lower_is_better(gap, t.gap_max, t.gap_fail)
    if gap < 0:
        message = f"Out-of-time AUC exceeds train AUC by {_fmt(abs(gap))}, which is {status}."
    else:
        message = f"Train AUC exceeds out-of-time AUC by {_fmt(gap)}, which is {status}."
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=gap,
        threshold=threshold,
        message=message,
        evidence={"train_auc": train.auc, "oot_auc": oot.auc, "gap": gap},
    )


def _cal_binomial(c: CalibrationTests, t: Thresholds) -> ValidationTest:
    test_id, name = "cal.binomial", "Per-grade binomial test"
    threshold = (
        f"share of grades with p < {t.calibration_alpha:.2f}: 0 pass, "
        f"<= {t.grade_fail_share_warn:.2f} warn"
    )
    n = len(c.grades)
    if n == 0:
        return _not_applicable(
            test_id, name, threshold, f"No grades formed on the {c.split} split to test."
        )
    failed = sum(g.p_value < t.calibration_alpha for g in c.grades)
    share = failed / n
    status: Band
    if failed == 0:
        status = "pass"
    elif share <= t.grade_fail_share_warn:
        status = "warn"
    else:
        status = "fail"
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=share,
        threshold=threshold,
        message=(
            f"{failed} of {n} grades on the {c.split} split reject the binomial test "
            f"(share {_fmt(share)}), which is {status}."
        ),
        evidence={
            "failed": failed,
            "n_grades": n,
            "share": share,
            "artifact_alpha": c.alpha,
            "threshold_alpha": t.calibration_alpha,
            "split": c.split,
        },
    )


def _cal_hl(c: CalibrationTests, t: Thresholds) -> ValidationTest:
    test_id, name = "cal.hl", "Hosmer-Lemeshow test"
    threshold = f"p >= {t.hl_p_min:.2f} pass, else warn"
    p = c.hl_p_value
    evidence = {
        "hl_statistic": c.hl_statistic,
        "hl_p_value": p,
        "n_grades": c.n_grades,
        "split": c.split,
    }
    if math.isnan(p):
        return ValidationTest(
            test_id=test_id,
            name=name,
            status="warn",
            value=None,
            threshold=threshold,
            message=(
                f"Only {c.n_grades} grades formed on the {c.split} split, too few grades "
                "for a Hosmer-Lemeshow p-value, so this is a warn."
            ),
            evidence=evidence,
        )
    status: Band = "pass" if p >= t.hl_p_min else "warn"
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=p,
        threshold=threshold,
        message=f"Hosmer-Lemeshow p-value is {_fmt(p)}, which is {status}.",
        evidence=evidence,
    )


def _cal_eo(c: CalibrationTests, t: Thresholds) -> ValidationTest:
    test_id, name = "cal.eo", "Expected to observed ratio"
    threshold = f"{t.eo_low:.2f} <= E/O <= {t.eo_high:.2f} pass, else warn"
    eo = c.eo_ratio
    evidence = {
        "eo_ratio": eo,
        "expected_total": c.expected_total,
        "observed_total": c.observed_total,
        "split": c.split,
    }
    if math.isinf(eo):
        message = f"No observed events on the {c.split} split make E/O infinite, which is a warn."
        status: Band = "warn"
    else:
        status = "pass" if t.eo_low <= eo <= t.eo_high else "warn"
        message = f"Expected to observed ratio is {_fmt(eo)}, which is {status}."
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=eo,
        threshold=threshold,
        message=message,
        evidence=evidence,
    )


def _psi_threshold(t: Thresholds) -> str:
    return f"< {t.psi_warn:.2f} pass, < {t.psi_fail:.2f} warn"


def _stab_psi_score(s: StabilityTests, t: Thresholds) -> ValidationTest:
    status = psi_band(s.score_psi, t)
    return ValidationTest(
        test_id="stab.psi.score",
        name="Score PSI",
        status=status,
        value=s.score_psi,
        threshold=_psi_threshold(t),
        message=(
            f"Score PSI of {s.compare_split} against {s.base_split} is "
            f"{_fmt(s.score_psi)}, which is {status}."
        ),
        evidence={
            "score_psi": s.score_psi,
            "base_split": s.base_split,
            "compare_split": s.compare_split,
            "n_bins": s.n_bins,
        },
    )


def _stab_csi_max(s: StabilityTests, t: Thresholds) -> ValidationTest:
    test_id, name = "stab.csi.max", "Maximum feature CSI"
    if not s.feature_csi:
        return _not_applicable(
            test_id, name, _psi_threshold(t), "No feature CSI entries exist, so this is skipped."
        )
    worst = max(s.feature_csi, key=lambda e: e.psi)
    status = psi_band(worst.psi, t)
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=worst.psi,
        threshold=_psi_threshold(t),
        message=f"Worst feature CSI is {_fmt(worst.psi)} on {worst.feature}, which is {status}.",
        evidence={
            "worst_feature": worst.feature,
            "max_csi": worst.psi,
            "n_features": len(s.feature_csi),
            "compare_split": s.compare_split,
        },
    )


def _sens_signs(s: SensitivityResults) -> ValidationTest:
    bad = [c.feature for c in s.sign_checks if not c.ok]
    status: Band = "pass" if not bad else "fail"
    return ValidationTest(
        test_id="sens.signs",
        name="Coefficient signs",
        status=status,
        value=float(len(bad)),
        threshold="0 wrong signs pass, else fail",
        message=f"{len(bad)} of {len(s.sign_checks)} coefficients have the wrong sign, "
        f"which is {status}.",
        evidence={"failed": len(bad), "n_checks": len(s.sign_checks), "features": bad},
    )


def _sens_monotonic(s: SensitivityResults) -> ValidationTest:
    bad = [m.feature for m in s.monotonicity if not m.monotone]
    status: Band = "pass" if not bad else "warn"
    return ValidationTest(
        test_id="sens.monotonic",
        name="WoE monotonicity",
        status=status,
        value=float(len(bad)),
        threshold="0 non-monotone pass, else warn",
        message=f"{len(bad)} of {len(s.monotonicity)} features are not monotone, "
        f"which is {status}.",
        evidence={
            "failed": len(bad),
            "n_checks": len(s.monotonicity),
            "features": bad,
        },
    )


def _bench_gap(b: BenchmarkResults, t: Thresholds) -> ValidationTest:
    test_id, name = "bench.gap", "Champion versus challenger AUC gap"
    threshold = f"challenger - champion AUC <= {t.benchmark_gap:.2f} pass, else warn"
    split = next((s for s in b.by_split if s.split == b.decided_on), None)
    if split is None:
        return _not_applicable(
            test_id, name, threshold, f"No benchmark row exists for the {b.decided_on} split."
        )
    delta = split.auc_delta
    status: Band = "pass" if delta <= t.benchmark_gap else "warn"
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=delta,
        threshold=threshold,
        message=(
            f"Challenger {b.challenger} minus champion {b.champion} AUC on {b.decided_on} "
            f"is {_fmt(delta)}, which is {status}."
        ),
        evidence={
            "decided_on": b.decided_on,
            "auc_delta": delta,
            "champion_auc": split.champion_auc,
            "challenger_auc": split.challenger_auc,
            "auc_delta_ci_low": split.auc_delta_ci_low,
            "auc_delta_ci_high": split.auc_delta_ci_high,
            "champion_within_gap": b.champion_within_gap,
        },
    )


def _feat_iv_range(sel: FeatureSelection, tables: BinningTables, t: Thresholds) -> ValidationTest:
    test_id, name = "feat.iv_range", "Maximum selected-feature IV"
    threshold = f"<= {t.iv_max:.2f} pass, else fail"
    if not sel.selected:
        return _not_applicable(test_id, name, threshold, "No features were selected to check.")
    ivs = {f: tables.get(f).iv for f in sel.selected}
    worst = max(ivs, key=lambda f: ivs[f])
    value = ivs[worst]
    status: Band = "fail" if value > t.iv_max else "pass"
    return ValidationTest(
        test_id=test_id,
        name=name,
        status=status,
        value=value,
        threshold=threshold,
        message=f"Highest selected-feature IV is {_fmt(value)} on {worst}, which is {status}.",
        evidence={"max_iv": value, "feature": worst, "n_selected": len(sel.selected)},
    )


def _overall(statuses: list[Status]) -> Band:
    worst: Band = "pass"
    for s in statuses:
        if s != "not_applicable" and _SEVERITY[s] > _SEVERITY[worst]:
            worst = s
    return worst


def evaluate_thresholds(
    *,
    model_name: str,
    thresholds: Thresholds,
    discrimination: DiscriminationMetrics,
    calibration: CalibrationTests,
    stability: StabilityTests,
    sensitivity: SensitivityResults,
    benchmark: BenchmarkResults,
    selection: FeatureSelection,
    tables: BinningTables,
    oot_available: bool,
) -> ValidationReport:
    """Run the thirteen pinned validation tests and roll them up into a report."""
    t = thresholds
    tests = [
        _disc_auc_oot(discrimination, t, oot_available=oot_available),
        _disc_auc_val(discrimination, t),
        _disc_ks_oot(discrimination, t, oot_available=oot_available),
        _disc_gap_train_oot(discrimination, t, oot_available=oot_available),
        _cal_binomial(calibration, t),
        _cal_hl(calibration, t),
        _cal_eo(calibration, t),
        _stab_psi_score(stability, t),
        _stab_csi_max(stability, t),
        _sens_signs(sensitivity),
        _sens_monotonic(sensitivity),
        _bench_gap(benchmark, t),
        _feat_iv_range(selection, tables, t),
    ]
    return ValidationReport(
        model_name=model_name,
        thresholds=t,
        tests=tests,
        overall=_overall([x.status for x in tests]),
    )
