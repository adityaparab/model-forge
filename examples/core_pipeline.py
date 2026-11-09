"""End-to-end core pipeline on a real dataset.

The order here is the reference sequence the deterministic workflow nodes follow:
load -> profile -> leakage -> splits -> binning -> selection (capped by IV) ->
logistic champion (stepwise wrong-sign refit) -> GBM challenger -> Platt calibration ->
scoring -> discrimination -> calibration tests -> stability -> sensitivity -> benchmark ->
thresholds.

Run: uv run python examples/core_pipeline.py --dataset polish_bankruptcy --oot-vintages 5
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
from modelforge_core.benchmark import benchmark
from modelforge_core.binning import fit_binning, transform_woe
from modelforge_core.calibration import fit_calibration
from modelforge_core.calibration_tests import calibration_tests
from modelforge_core.datasets.base import LoadedDataset, feature_columns
from modelforge_core.datasets.registry import ADAPTERS, load_dataset
from modelforge_core.gbm import fit_gbm, predict_gbm
from modelforge_core.leakage import check_leakage
from modelforge_core.logistic import fit_woe_logistic_stepwise
from modelforge_core.metrics import discrimination_metrics
from modelforge_core.profiling import profile
from modelforge_core.schemas import (
    BinningTables,
    CalibrationMap,
    ColumnDictionary,
    FeatureSelection,
    GBMModel,
    Scorecard,
    SplitManifest,
    ValidationReport,
)
from modelforge_core.scorecard import predict_pd, to_json
from modelforge_core.scoring import score_frame
from modelforge_core.selection import select_features
from modelforge_core.sensitivity import sensitivity_analysis
from modelforge_core.splits import apply_split, make_splits
from modelforge_core.stability import stability_tests
from modelforge_core.standards import DEFAULT_THRESHOLDS
from modelforge_core.thresholds import evaluate_thresholds

VAL_SHARE = 0.2
IV_MIN, IV_MAX, CORR_MAX = 0.02, 0.5, 0.7


@dataclass(frozen=True)
class PipelineResult:
    report: ValidationReport
    scorecard: Scorecard
    gbm: GBMModel
    selection: FeatureSelection
    tables: BinningTables
    manifest: SplitManifest
    dictionary: ColumnDictionary


@dataclass(frozen=True)
class _Fitted:
    tables: BinningTables
    selection: FeatureSelection
    scorecard: Scorecard
    gbm: GBMModel
    calibration: CalibrationMap


def _load_and_screen(dataset: str, data_dir: Path) -> tuple[LoadedDataset, list[str]]:
    """Load, profile and leakage-screen; return the dataset and the candidate features."""
    ds = load_dataset(dataset, data_dir)
    profile(ds.frame, dataset=dataset)
    leakage = check_leakage(ds.frame, dataset=dataset)
    for flag in leakage.flags:
        print(f"leakage: {flag.feature} {flag.kind} {flag.detail}")
    flagged = {flag.feature for flag in leakage.flags}
    return ds, [c for c in feature_columns(ds.frame) if c not in flagged]


def _split(
    ds: LoadedDataset, oot_vintages: Sequence[int], seed: int
) -> tuple[SplitManifest, pd.DataFrame, pd.DataFrame]:
    if ds.oot_capable and oot_vintages:
        manifest = make_splits(
            ds.frame,
            strategy="vintage",
            seed=seed,
            oot_vintages=list(oot_vintages),
            val_share=VAL_SHARE,
        )
    else:
        manifest = make_splits(
            ds.frame, strategy="stratified_random", seed=seed, val_share=VAL_SHARE
        )
    train = apply_split(ds.frame, manifest, "train")
    return manifest, train, apply_split(ds.frame, manifest, "val")


def _fit(
    dataset: str,
    train: pd.DataFrame,
    val: pd.DataFrame,
    candidates: list[str],
    *,
    seed: int,
    max_features: int,
) -> _Fitted:
    tables = fit_binning(train, candidates)
    screened = select_features(
        tables,
        transform_woe(train, tables),
        iv_min=IV_MIN,
        iv_max=IV_MAX,
        corr_max=CORR_MAX,
        max_features=max_features,
    )
    scorecard, sign_drops = fit_woe_logistic_stepwise(
        train, tables, screened.selected, model_name=f"{dataset}_pd"
    )
    for drop in sign_drops:
        print(f"wrong sign: {drop.feature} {drop.detail}")
    features = [f.feature for f in scorecard.features]
    selection = screened.model_copy(
        update={"selected": features, "dropped": [*screened.dropped, *sign_drops]}
    )
    gbm = fit_gbm(train, features, model_name=f"{dataset}_gbm", seed=seed, val_frame=val)
    calibration = fit_calibration(
        predict_pd(scorecard, val),
        cast(np.ndarray, val["default_flag"].to_numpy()),
        method="platt",
        fitted_on="val",
    )
    return _Fitted(tables, selection, scorecard, gbm, calibration)


def _validate(
    frame: pd.DataFrame, manifest: SplitManifest, fitted: _Fitted, *, seed: int
) -> ValidationReport:
    sc, gbm = fitted.scorecard, fitted.gbm

    def champion_predict(f: pd.DataFrame) -> np.ndarray:
        return predict_pd(sc, f)

    def challenger_predict(f: pd.DataFrame) -> np.ndarray:
        return predict_gbm(gbm, f)

    champ_scores = score_frame(frame, manifest, champion_predict, calibration=fitted.calibration)
    chal_scores = score_frame(frame, manifest, challenger_predict)
    eval_split = "oot" if manifest.oot_available else "val"
    return evaluate_thresholds(
        model_name=sc.model_name,
        thresholds=DEFAULT_THRESHOLDS,
        discrimination=discrimination_metrics(champ_scores, model_name=sc.model_name, seed=seed),
        calibration=calibration_tests(champ_scores, split=eval_split),
        stability=stability_tests(
            champ_scores,
            frame,
            fitted.tables,
            compare_split=eval_split,
            features=[f.feature for f in sc.features],
        ),
        sensitivity=sensitivity_analysis(sc, fitted.tables, frame),
        benchmark=benchmark(
            champ_scores,
            chal_scores,
            champion=sc.model_name,
            challenger=gbm.model_name,
            gap_threshold=DEFAULT_THRESHOLDS.benchmark_gap,
            seed=seed,
        ),
        selection=fitted.selection,
        tables=fitted.tables,
        oot_available=manifest.oot_available,
    )


def _write_outputs(out: Path, result: PipelineResult) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "scorecard.json").write_text(to_json(result.scorecard))
    (out / "gbm_model.txt").write_text(result.gbm.model_text)
    (out / "validation_report.json").write_text(result.report.model_dump_json(indent=2))
    (out / "binning_tables.json").write_text(result.tables.model_dump_json(indent=2))
    (out / "column_dictionary.json").write_text(result.dictionary.model_dump_json(indent=2))


def run_pipeline(
    *,
    dataset: str,
    oot_vintages: Sequence[int],
    data_dir: Path,
    out_dir: Path,
    seed: int = 42,
    max_features: int = 20,
) -> PipelineResult:
    ds, candidates = _load_and_screen(dataset, data_dir)
    manifest, train, val = _split(ds, oot_vintages, seed)
    fitted = _fit(dataset, train, val, candidates, seed=seed, max_features=max_features)
    report = _validate(ds.frame, manifest, fitted, seed=seed)
    result = PipelineResult(
        report=report,
        scorecard=fitted.scorecard,
        gbm=fitted.gbm,
        selection=fitted.selection,
        tables=fitted.tables,
        manifest=manifest,
        dictionary=ds.dictionary,
    )
    _write_outputs(out_dir / dataset, result)
    return result


def _parse_vintages(text: str) -> list[int]:
    return [int(v) for v in text.split(",") if v.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ModelForge core pipeline end to end.")
    parser.add_argument("--dataset", choices=sorted(ADAPTERS), default="polish_bankruptcy")
    parser.add_argument(
        "--oot-vintages",
        type=_parse_vintages,
        default="5",
        help="comma-separated vintages held out as out-of-time; empty for none",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("out"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-features", type=int, default=20)
    args = parser.parse_args(argv)

    result = run_pipeline(
        dataset=args.dataset,
        oot_vintages=args.oot_vintages,
        data_dir=args.data_dir,
        out_dir=args.out,
        seed=args.seed,
        max_features=args.max_features,
    )
    for test in result.report.tests:
        print(f"{test.status + ':':>14} {test.test_id:<22} {test.value}")
    print(f"overall: {result.report.overall}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
