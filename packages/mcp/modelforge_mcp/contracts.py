"""Tool contracts: committed JSON schema snapshots and per-platform golden output hashes.

A schema snapshot pins each tool version's input and output JSON schema; a golden file pins
the content-addressed artifact ids a fixed tool chain produces on one platform. Changing a
tool's schema or algorithm without bumping its version makes the contract tests fail.
"""

import json
import platform
import sys
from pathlib import Path
from typing import Any

from modelforge_core.schemas import Thresholds
from modelforge_lineage.models import ArtifactRef

from modelforge_mcp.common import ServerName, ServerSettings, ToolSpec, make_context
from modelforge_mcp.registry import TOOLS
from modelforge_mcp.schemas import DatasetRef, DocumentSection, DocumentSections
from modelforge_mcp.servers import credit_modeling as cm
from modelforge_mcp.servers import data_profiler as dp
from modelforge_mcp.servers import doc_renderer as dr
from modelforge_mcp.servers import knowledge as kn
from modelforge_mcp.servers import validation as va

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"

GOLDEN_FEATURES = [f"f_{i}" for i in range(1, 7)]
GOLDEN_TEMPLATE = "sr11-7-dev-doc"


def platform_key() -> str:
    return f"{sys.platform}-{platform.machine()}"


def schema_snapshot(spec: ToolSpec) -> dict[str, Any]:
    return {
        "server": spec.server,
        "tool": spec.tool,
        "version": spec.version,
        "input_schema": spec.input_model.model_json_schema(),
        "output_schema": spec.output_model.model_json_schema(),
    }


def snapshot_path(spec: ToolSpec) -> Path:
    return CONTRACTS_DIR / spec.server / f"{spec.tool}@{spec.version}.json"


def write_snapshots(out_dir: Path = CONTRACTS_DIR) -> list[Path]:
    paths: list[Path] = []
    for spec in TOOLS.values():
        path = out_dir / snapshot_path(spec).relative_to(CONTRACTS_DIR)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(schema_snapshot(spec), indent=2, sort_keys=True) + "\n")
        paths.append(path)
    return paths


def run_golden_chain(settings: ServerSettings) -> dict[tuple[ServerName, str], list[ArtifactRef]]:
    """Call every artifact-producing tool once, in dependency order, on the synthetic dataset."""
    dpc = make_context("data-profiler", settings)
    cmc = make_context("credit-modeling", settings)
    vac = make_context("validation", settings)
    drc = make_context("doc-renderer", settings)
    knc = make_context("knowledge", settings)

    loaded = dp.load_dataset(
        dpc, dp.LoadDatasetInput(dataset_ref=DatasetRef(adapter="synthetic_credit", seed=42))
    )
    dataset = loaded.dataset.id
    profile = dp.profile_dataset(dpc, dp.ProfileDatasetInput(dataset_id=dataset))
    leakage = dp.check_leakage(dpc, dp.CheckLeakageInput(dataset_id=dataset))
    psi = dp.compute_psi(
        dpc,
        dp.ComputePsiInput(dataset_id=dataset, base_vintages=[1, 2, 3, 4], compare_vintages=[5]),
    )
    split = dp.make_splits(
        dpc,
        dp.MakeSplitsInput(dataset_id=dataset, strategy="vintage", oot_vintages=[5], seed=42),
    )

    binning = cm.bin_features_woe(
        cmc, cm.BinFeaturesInput(dataset_id=dataset, split_id=split.id, features=GOLDEN_FEATURES)
    )
    selection = cm.select_features(
        cmc,
        cm.SelectFeaturesInput(
            dataset_id=dataset,
            split_id=split.id,
            binning_id=binning.id,
            iv_min=0.02,
            iv_max=0.5,
            corr_max=0.7,
            max_features=20,
        ),
    )
    fit = cm.fit_logistic_pd(
        cmc,
        cm.FitLogisticInput(
            dataset_id=dataset,
            split_id=split.id,
            binning_id=binning.id,
            selection_id=selection.id,
            model_name="m",
            stepwise=True,
        ),
    )
    features: list[str] = fit.scorecard.summary["features"]
    gbm = cm.fit_gbm_challenger(
        cmc,
        cm.FitGbmInput(
            dataset_id=dataset, split_id=split.id, features=features, model_name="g", seed=42
        ),
    )
    calibration = cm.calibrate(
        cmc,
        cm.CalibrateInput(
            model_id=fit.scorecard.id,
            dataset_id=dataset,
            split_id=split.id,
            fit_split="val",
            method="platt",
        ),
    )
    champ_scores = cm.score(
        cmc,
        cm.ScoreInput(
            model_id=fit.scorecard.id,
            dataset_id=dataset,
            split_id=split.id,
            calibration_id=calibration.id,
        ),
    )
    chal_scores = cm.score(
        cmc, cm.ScoreInput(model_id=gbm.id, dataset_id=dataset, split_id=split.id)
    )

    disc = va.discrimination_metrics(
        vac, va.DiscriminationInput(scores_id=champ_scores.id, model_name="m", seed=42)
    )
    cal_tests = va.calibration_tests(
        vac, va.CalibrationTestsInput(scores_id=champ_scores.id, split="oot")
    )
    stability = va.stability_tests(
        vac,
        va.StabilityInput(
            scores_id=champ_scores.id,
            dataset_id=dataset,
            binning_id=binning.id,
            features=features,
            compare_split="oot",
            thresholds=Thresholds(),
        ),
    )
    sensitivity = va.sensitivity_analysis(
        vac,
        va.SensitivityInput(model_id=fit.scorecard.id, binning_id=binning.id, dataset_id=dataset),
    )
    bench = va.benchmark_models(
        vac,
        va.BenchmarkInput(
            champion_scores_id=champ_scores.id,
            challenger_scores_id=chal_scores.id,
            champion="m",
            challenger="g",
            gap_threshold=0.03,
            seed=42,
        ),
    )
    report = va.evaluate_thresholds(
        vac,
        va.EvaluateThresholdsInput(
            model_name="m",
            thresholds=Thresholds(),
            discrimination_id=disc.id,
            calibration_id=cal_tests.id,
            stability_id=stability.id,
            sensitivity_id=sensitivity.id,
            benchmark_id=bench.id,
            selection_id=fit.selection.id,
            binning_id=binning.id,
            oot_available=True,
        ),
    )

    sections = drc.store.put_model(
        DocumentSections(
            template_id=GOLDEN_TEMPLATE,
            sections=[
                DocumentSection(
                    section_id="executive_summary", body_md="Golden executive summary."
                ),
                DocumentSection(section_id="data", body_md="Golden data section."),
            ],
        ),
        kind="document_sections",
    )
    rendered = dr.render_document(
        drc,
        dr.RenderDocumentInput(
            template_id=GOLDEN_TEMPLATE,
            sections_id=sections.id,
            title="Golden document",
            context={"model": "m", "dataset": "synthetic_credit"},
        ),
    )
    completeness = dr.check_completeness(
        drc,
        dr.CheckCompletenessInput(document_id=rendered.markdown.id, template_id=GOLDEN_TEMPLATE),
    )

    index = kn.get_index(kn.load_index(knc), kn.GetIndexInput())

    return {
        ("data-profiler", "load_dataset"): [loaded.dataset, loaded.dictionary],
        ("data-profiler", "profile_dataset"): [profile],
        ("data-profiler", "check_leakage"): [leakage],
        ("data-profiler", "compute_psi"): [psi],
        ("data-profiler", "make_splits"): [split],
        ("credit-modeling", "bin_features_woe"): [binning],
        ("credit-modeling", "select_features"): [selection],
        ("credit-modeling", "fit_logistic_pd"): [fit.scorecard, fit.selection],
        ("credit-modeling", "fit_gbm_challenger"): [gbm],
        ("credit-modeling", "calibrate"): [calibration],
        ("credit-modeling", "score"): [champ_scores, chal_scores],
        ("validation", "discrimination_metrics"): [disc],
        ("validation", "calibration_tests"): [cal_tests],
        ("validation", "stability_tests"): [stability],
        ("validation", "sensitivity_analysis"): [sensitivity],
        ("validation", "benchmark_models"): [bench],
        ("validation", "evaluate_thresholds"): [report],
        ("doc-renderer", "render_document"): [rendered.markdown, rendered.html],
        ("doc-renderer", "check_completeness"): [completeness],
        ("knowledge", "get_index"): [index],
    }


def golden_path(spec: ToolSpec, platform: str) -> Path:
    return CONTRACTS_DIR / "goldens" / platform / spec.server / f"{spec.tool}@{spec.version}.sha256"


def write_goldens(settings: ServerSettings) -> list[Path]:
    key = platform_key()
    paths: list[Path] = []
    for (server, tool), refs in run_golden_chain(settings).items():
        path = golden_path(TOOLS[(server, tool)], key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(r.id for r in refs) + "\n")
        paths.append(path)
    return paths
