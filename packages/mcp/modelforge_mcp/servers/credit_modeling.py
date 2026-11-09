"""credit-modeling server: WoE binning, feature selection, scorecard, GBM challenger, scoring."""

from typing import Literal

from mcp.server.mcpserver import MCPServer
from modelforge_core.binning import fit_binning, transform_woe
from modelforge_core.calibration import calibrate_split
from modelforge_core.gbm import fit_gbm
from modelforge_core.logistic import fit_woe_logistic, fit_woe_logistic_stepwise
from modelforge_core.predict import parse_model, predict_model
from modelforge_core.schemas import (
    BinningTables,
    CalibrationMap,
    DroppedFeature,
    FeatureSelection,
    SplitManifest,
    StrictModel,
)
from modelforge_core.schemas import Split as SplitName
from modelforge_core.scoring import score_frame
from modelforge_core.selection import select_features as core_select_features
from modelforge_core.splits import apply_split
from modelforge_lineage.models import ArtifactRef

from modelforge_mcp.common import (
    ServerName,
    ServerSettings,
    ToolContext,
    ToolSpec,
    load_frame,
    load_model,
    make_context,
    make_server,
    producer,
    register_tool,
)

SERVER_NAME: ServerName = "credit-modeling"
SERVER_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"
INSTRUCTIONS = (
    "Builds credit PD models from stored datasets and split manifests: WoE binning, "
    "IV/correlation feature selection, a WoE-logistic scorecard, a LightGBM challenger, "
    "calibration and scoring. Every tool takes and returns artifact ids; no data travels inline."
)


class BinFeaturesInput(StrictModel):
    dataset_id: str
    split_id: str
    features: list[str]
    fit_split: SplitName = "train"
    method: Literal["quantile", "tree"] = "quantile"
    max_bins: int = 10
    min_bin_share: float = 0.05
    monotonic: bool = True


class SelectFeaturesInput(StrictModel):
    dataset_id: str
    split_id: str
    binning_id: str
    fit_split: SplitName = "train"
    iv_min: float
    iv_max: float
    corr_max: float
    max_features: int | None = None


class FitLogisticInput(StrictModel):
    dataset_id: str
    split_id: str
    binning_id: str
    selection_id: str
    model_name: str
    fit_split: SplitName = "train"
    stepwise: bool = True
    penalty: Literal["l2", "none"] = "l2"
    c: float = 1.0


class FitLogisticOutput(StrictModel):
    scorecard: ArtifactRef
    selection: ArtifactRef


class FitGbmInput(StrictModel):
    dataset_id: str
    split_id: str
    features: list[str]
    model_name: str
    seed: int
    params: dict[str, float | int | str | bool] | None = None
    fit_split: SplitName = "train"
    val_split: SplitName | None = "val"
    early_stopping_rounds: int = 30


class CalibrateInput(StrictModel):
    model_id: str
    dataset_id: str
    split_id: str
    fit_split: SplitName = "val"
    method: Literal["platt", "isotonic", "none"] = "platt"


class ScoreInput(StrictModel):
    model_id: str
    dataset_id: str
    split_id: str
    calibration_id: str | None = None


def bin_features_woe(ctx: ToolContext, req: BinFeaturesInput) -> ArtifactRef:
    frame = load_frame(ctx, req.dataset_id)
    manifest = load_model(ctx, req.split_id, SplitManifest)
    tables = fit_binning(
        apply_split(frame, manifest, req.fit_split),
        req.features,
        method=req.method,
        max_bins=req.max_bins,
        min_bin_share=req.min_bin_share,
        monotonic=req.monotonic,
    )
    return ctx.store.put_model(
        tables,
        kind="binning_tables",
        producer=producer(ctx, "bin_features_woe", TOOL_VERSION),
        summary={
            "n_features": len(tables.features),
            "iv": {f.feature: float(f.iv) for f in tables.features},
        },
    )


def select_features(ctx: ToolContext, req: SelectFeaturesInput) -> ArtifactRef:
    frame = load_frame(ctx, req.dataset_id)
    manifest = load_model(ctx, req.split_id, SplitManifest)
    tables = load_model(ctx, req.binning_id, BinningTables)
    woe_frame = transform_woe(apply_split(frame, manifest, req.fit_split), tables)
    selection = core_select_features(
        tables,
        woe_frame,
        iv_min=req.iv_min,
        iv_max=req.iv_max,
        corr_max=req.corr_max,
        max_features=req.max_features,
    )
    return ctx.store.put_model(
        selection,
        kind="feature_selection",
        producer=producer(ctx, "select_features", TOOL_VERSION),
        summary={"selected": list(selection.selected), "n_dropped": len(selection.dropped)},
    )


def fit_logistic_pd(ctx: ToolContext, req: FitLogisticInput) -> FitLogisticOutput:
    frame = load_frame(ctx, req.dataset_id)
    manifest = load_model(ctx, req.split_id, SplitManifest)
    tables = load_model(ctx, req.binning_id, BinningTables)
    selection = load_model(ctx, req.selection_id, FeatureSelection)
    train = apply_split(frame, manifest, req.fit_split)
    drops: list[DroppedFeature] = []
    if req.stepwise:
        scorecard, drops = fit_woe_logistic_stepwise(
            train,
            tables,
            selection.selected,
            model_name=req.model_name,
            penalty=req.penalty,
            c=req.c,
        )
        selection = selection.model_copy(
            update={
                "selected": [f.feature for f in scorecard.features],
                "dropped": [*selection.dropped, *drops],
            }
        )
    else:
        scorecard = fit_woe_logistic(
            train,
            tables,
            selection.selected,
            model_name=req.model_name,
            penalty=req.penalty,
            c=req.c,
        )
    made_by = producer(ctx, "fit_logistic_pd", TOOL_VERSION)
    scorecard_ref = ctx.store.put_model(
        scorecard,
        kind="scorecard",
        producer=made_by,
        summary={
            "features": [f.feature for f in scorecard.features],
            "intercept": float(scorecard.intercept),
            "fit_method": scorecard.fit_method,
            "n_wrong_sign_drops": len(drops),
        },
    )
    selection_ref = ctx.store.put_model(
        selection,
        kind="feature_selection",
        producer=made_by,
        summary={"selected": list(selection.selected), "n_dropped": len(selection.dropped)},
    )
    return FitLogisticOutput(scorecard=scorecard_ref, selection=selection_ref)


def fit_gbm_challenger(ctx: ToolContext, req: FitGbmInput) -> ArtifactRef:
    frame = load_frame(ctx, req.dataset_id)
    manifest = load_model(ctx, req.split_id, SplitManifest)
    val_frame = apply_split(frame, manifest, req.val_split) if req.val_split is not None else None
    model = fit_gbm(
        apply_split(frame, manifest, req.fit_split),
        req.features,
        model_name=req.model_name,
        seed=req.seed,
        params=req.params,
        val_frame=val_frame,
        early_stopping_rounds=req.early_stopping_rounds,
    )
    return ctx.store.put_model(
        model,
        kind="gbm_model",
        producer=producer(ctx, "fit_gbm_challenger", TOOL_VERSION),
        summary={"num_trees": int(model.num_trees), "features": list(model.features)},
    )


def calibrate(ctx: ToolContext, req: CalibrateInput) -> ArtifactRef:
    model = parse_model(ctx.store.get(req.model_id).decode())
    frame = load_frame(ctx, req.dataset_id)
    manifest = load_model(ctx, req.split_id, SplitManifest)
    cal = calibrate_split(
        frame,
        manifest,
        lambda f: predict_model(model, f),
        split=req.fit_split,
        method=req.method,
    )
    return ctx.store.put_model(
        cal,
        kind="calibration_map",
        producer=producer(ctx, "calibrate", TOOL_VERSION),
        summary={
            "method": cal.method,
            "platt_a": None if cal.platt_a is None else float(cal.platt_a),
            "platt_b": None if cal.platt_b is None else float(cal.platt_b),
        },
    )


def score(ctx: ToolContext, req: ScoreInput) -> ArtifactRef:
    model = parse_model(ctx.store.get(req.model_id).decode())
    frame = load_frame(ctx, req.dataset_id)
    manifest = load_model(ctx, req.split_id, SplitManifest)
    calibration = (
        load_model(ctx, req.calibration_id, CalibrationMap) if req.calibration_id else None
    )
    scores = score_frame(
        frame, manifest, lambda f: predict_model(model, f), calibration=calibration
    )
    labels: list[str] = scores["split"].tolist()
    splits: tuple[SplitName, ...] = ("train", "val", "oot")
    rows = {split: labels.count(split) for split in splits}
    return ctx.store.put_frame(
        scores,
        kind="scores",
        producer=producer(ctx, "score", TOOL_VERSION),
        summary={
            "rows": {split: n for split, n in rows.items() if n > 0},
            "model_name": model.model_name,
        },
    )


_TOOLS = [
    (
        "bin_features_woe",
        "Fit WoE binning tables for the given features on one split of a stored dataset.",
        bin_features_woe,
        BinFeaturesInput,
        ArtifactRef,
    ),
    (
        "select_features",
        "Select features by IV band and WoE correlation from stored binning tables.",
        select_features,
        SelectFeaturesInput,
        ArtifactRef,
    ),
    (
        "fit_logistic_pd",
        "Fit the WoE-logistic PD scorecard, optionally dropping wrong-sign features stepwise; "
        "returns the scorecard and the resulting feature selection.",
        fit_logistic_pd,
        FitLogisticInput,
        FitLogisticOutput,
    ),
    (
        "fit_gbm_challenger",
        "Fit a deterministic LightGBM challenger model, with early stopping on a validation split.",
        fit_gbm_challenger,
        FitGbmInput,
        ArtifactRef,
    ),
    (
        "calibrate",
        "Fit a PD calibration map (Platt, isotonic or none) for a stored model on one split.",
        calibrate,
        CalibrateInput,
        ArtifactRef,
    ),
    (
        "score",
        "Score every split of a stored dataset with a stored model, optionally calibrated; "
        "stores the scores as Parquet.",
        score,
        ScoreInput,
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
