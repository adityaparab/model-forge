"""validation server: discrimination, calibration, stability, sensitivity, benchmark, thresholds."""

from mcp.server.mcpserver import MCPServer
from modelforge_core.benchmark import benchmark
from modelforge_core.calibration_tests import calibration_tests as core_calibration_tests
from modelforge_core.metrics import discrimination_metrics as core_discrimination_metrics
from modelforge_core.predict import parse_model
from modelforge_core.schemas import (
    BenchmarkResults,
    BinningTables,
    CalibrationTests,
    DiscriminationMetrics,
    FeatureSelection,
    Scorecard,
    SensitivityResults,
    StabilityTests,
    StrictModel,
    Thresholds,
)
from modelforge_core.schemas import Split as SplitName
from modelforge_core.sensitivity import sensitivity_analysis as core_sensitivity_analysis
from modelforge_core.stability import stability_tests as core_stability_tests
from modelforge_core.thresholds import evaluate_thresholds as core_evaluate_thresholds
from modelforge_lineage.models import ArtifactRef

from modelforge_mcp.common import (
    ServerName,
    ServerSettings,
    ToolContext,
    ToolSpec,
    fail,
    json_float,
    load_frame,
    load_model,
    make_context,
    make_server,
    producer,
    register_tool,
)

SERVER_NAME: ServerName = "validation"
SERVER_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"
INSTRUCTIONS = (
    "Validates stored credit PD models: discrimination metrics, calibration tests, score and "
    "feature stability, scorecard sensitivity, champion-challenger benchmark, and the threshold "
    "evaluation that yields the validation report. Every tool takes and returns artifact ids; "
    "no data travels inline."
)


class DiscriminationInput(StrictModel):
    scores_id: str
    model_name: str
    bootstrap_samples: int = 200
    seed: int


class CalibrationTestsInput(StrictModel):
    scores_id: str
    split: SplitName
    alpha: float = 0.05
    n_grades: int = 10


class StabilityInput(StrictModel):
    scores_id: str
    dataset_id: str
    binning_id: str
    features: list[str]
    base_split: SplitName = "train"
    compare_split: SplitName
    n_bins: int = 10
    thresholds: Thresholds = Thresholds()


class SensitivityInput(StrictModel):
    model_id: str
    binning_id: str
    dataset_id: str
    shifts: list[int] = [-1, 1]


class BenchmarkInput(StrictModel):
    champion_scores_id: str
    challenger_scores_id: str
    champion: str
    challenger: str
    gap_threshold: float
    bootstrap_samples: int = 200
    seed: int


class EvaluateThresholdsInput(StrictModel):
    model_name: str
    thresholds: Thresholds
    discrimination_id: str
    calibration_id: str
    stability_id: str
    sensitivity_id: str | None
    benchmark_id: str | None
    selection_id: str
    binning_id: str
    oot_available: bool


def discrimination_metrics(ctx: ToolContext, req: DiscriminationInput) -> ArtifactRef:
    scores = load_frame(ctx, req.scores_id)
    metrics = core_discrimination_metrics(
        scores,
        model_name=req.model_name,
        bootstrap_samples=req.bootstrap_samples,
        seed=req.seed,
    )
    return ctx.store.put_model(
        metrics,
        kind="discrimination",
        producer=producer(ctx, "discrimination_metrics", TOOL_VERSION),
        summary={"auc": {m.split: json_float(m.auc) for m in metrics.by_split}},
    )


def calibration_tests(ctx: ToolContext, req: CalibrationTestsInput) -> ArtifactRef:
    scores = load_frame(ctx, req.scores_id)
    tests = core_calibration_tests(scores, split=req.split, alpha=req.alpha, n_grades=req.n_grades)
    return ctx.store.put_model(
        tests,
        kind="calibration_tests",
        producer=producer(ctx, "calibration_tests", TOOL_VERSION),
        summary={
            "n_grades": int(tests.n_grades),
            "grades_failed": sum(1 for g in tests.grades if not g.passed),
            "hl_p_value": json_float(tests.hl_p_value),
            "eo_ratio": json_float(tests.eo_ratio),
        },
    )


def stability_tests(ctx: ToolContext, req: StabilityInput) -> ArtifactRef:
    scores = load_frame(ctx, req.scores_id)
    frame = load_frame(ctx, req.dataset_id)
    tables = load_model(ctx, req.binning_id, BinningTables)
    stability = core_stability_tests(
        scores,
        frame,
        tables,
        base_split=req.base_split,
        compare_split=req.compare_split,
        n_bins=req.n_bins,
        thresholds=req.thresholds,
        features=req.features,
    )
    return ctx.store.put_model(
        stability,
        kind="stability",
        producer=producer(ctx, "stability_tests", TOOL_VERSION),
        summary={
            "score_psi": json_float(stability.score_psi),
            "max_csi": (
                json_float(max(e.psi for e in stability.feature_csi))
                if stability.feature_csi
                else None
            ),
        },
    )


def sensitivity_analysis(ctx: ToolContext, req: SensitivityInput) -> ArtifactRef:
    model = parse_model(ctx.store.get(req.model_id).decode())
    if not isinstance(model, Scorecard):
        fail("invalid_input", f"artifact {req.model_id!r} is not a scorecard")
    tables = load_model(ctx, req.binning_id, BinningTables)
    frame = load_frame(ctx, req.dataset_id)
    results = core_sensitivity_analysis(model, tables, frame, shifts=req.shifts)
    return ctx.store.put_model(
        results,
        kind="sensitivity",
        producer=producer(ctx, "sensitivity_analysis", TOOL_VERSION),
        summary={"all_signs_ok": results.all_signs_ok, "all_monotone": results.all_monotone},
    )


def benchmark_models(ctx: ToolContext, req: BenchmarkInput) -> ArtifactRef:
    champion_scores = load_frame(ctx, req.champion_scores_id)
    challenger_scores = load_frame(ctx, req.challenger_scores_id)
    results = benchmark(
        champion_scores,
        challenger_scores,
        champion=req.champion,
        challenger=req.challenger,
        gap_threshold=req.gap_threshold,
        bootstrap_samples=req.bootstrap_samples,
        seed=req.seed,
    )
    decided = next(s for s in results.by_split if s.split == results.decided_on)
    return ctx.store.put_model(
        results,
        kind="benchmark",
        producer=producer(ctx, "benchmark_models", TOOL_VERSION),
        summary={
            "decided_on": results.decided_on,
            "auc_delta": json_float(decided.auc_delta),
            "champion_within_gap": results.champion_within_gap,
        },
    )


def evaluate_thresholds(ctx: ToolContext, req: EvaluateThresholdsInput) -> ArtifactRef:
    report = core_evaluate_thresholds(
        model_name=req.model_name,
        thresholds=req.thresholds,
        discrimination=load_model(ctx, req.discrimination_id, DiscriminationMetrics),
        calibration=load_model(ctx, req.calibration_id, CalibrationTests),
        stability=load_model(ctx, req.stability_id, StabilityTests),
        sensitivity=(
            load_model(ctx, req.sensitivity_id, SensitivityResults)
            if req.sensitivity_id is not None
            else None
        ),
        benchmark=(
            load_model(ctx, req.benchmark_id, BenchmarkResults)
            if req.benchmark_id is not None
            else None
        ),
        selection=load_model(ctx, req.selection_id, FeatureSelection),
        tables=load_model(ctx, req.binning_id, BinningTables),
        oot_available=req.oot_available,
    )
    return ctx.store.put_model(
        report,
        kind="validation_report",
        producer=producer(ctx, "evaluate_thresholds", TOOL_VERSION),
        summary={
            "overall": report.overall,
            "statuses": {t.test_id: t.status for t in report.tests},
        },
    )


_TOOLS = [
    (
        "discrimination_metrics",
        "Compute AUC, Gini and KS with bootstrap confidence intervals per split of stored scores.",
        discrimination_metrics,
        DiscriminationInput,
        ArtifactRef,
    ),
    (
        "calibration_tests",
        "Run per-grade binomial, Hosmer-Lemeshow and expected-over-observed calibration tests "
        "on one split of stored scores.",
        calibration_tests,
        CalibrationTestsInput,
        ArtifactRef,
    ),
    (
        "stability_tests",
        "Compute score PSI and per-feature CSI between two splits of stored scores and dataset.",
        stability_tests,
        StabilityInput,
        ArtifactRef,
    ),
    (
        "sensitivity_analysis",
        "Check coefficient signs, WoE monotonicity and PD response to bin shifts for a stored "
        "scorecard.",
        sensitivity_analysis,
        SensitivityInput,
        ArtifactRef,
    ),
    (
        "benchmark_models",
        "Compare champion and challenger scores by AUC and KS with a paired bootstrap on the "
        "AUC delta.",
        benchmark_models,
        BenchmarkInput,
        ArtifactRef,
    ),
    (
        "evaluate_thresholds",
        "Evaluate stored validation results against thresholds and produce the validation report.",
        evaluate_thresholds,
        EvaluateThresholdsInput,
        ArtifactRef,
    ),
]

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        server=SERVER_NAME,
        tool=name,
        version=TOOL_VERSION,
        input_model=input_model,
        output_model=output_model,
    )
    for name, _, _, input_model, output_model in _TOOLS
]


def build(settings: ServerSettings) -> MCPServer:
    server = make_server(SERVER_NAME, SERVER_VERSION, INSTRUCTIONS)
    ctx = make_context(SERVER_NAME, settings)
    for name, description, fn, input_model, output_model in _TOOLS:
        register_tool(
            server,
            ctx,
            name=name,
            version=TOOL_VERSION,
            description=description,
            fn=fn,
            input_model=input_model,
            output_model=output_model,
        )
    return server
