"""data-profiler server: load canonical datasets, profile them, screen leakage, PSI and splits."""

from typing import Literal

from mcp.server.mcpserver import MCPServer
from modelforge_core.datasets import registry as datasets
from modelforge_core.leakage import check_leakage as core_check_leakage
from modelforge_core.profiling import profile as core_profile
from modelforge_core.psi import compute_psi as core_compute_psi
from modelforge_core.schemas import Split, StrictModel
from modelforge_core.splits import make_splits as core_make_splits
from modelforge_lineage.models import ArtifactRef

from modelforge_mcp.common import (
    ServerName,
    ServerSettings,
    ToolContext,
    ToolSpec,
    load_frame,
    make_context,
    make_server,
    producer,
    register_tool,
)
from modelforge_mcp.schemas import DatasetRef

SERVER_NAME: ServerName = "data-profiler"
SERVER_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"
INSTRUCTIONS = (
    "Loads canonical credit datasets into the artifact store and analyses them: profile, "
    "leakage screen, PSI between vintages, and deterministic train/val/OOT splits. "
    "Every tool takes and returns artifact ids; no data travels inline."
)


class LoadDatasetInput(StrictModel):
    dataset_ref: DatasetRef


class LoadDatasetOutput(StrictModel):
    dataset: ArtifactRef
    dictionary: ArtifactRef
    oot_capable: bool
    n_rows: int


class ProfileDatasetInput(StrictModel):
    dataset_id: str
    dataset_name: str | None = None


class CheckLeakageInput(StrictModel):
    dataset_id: str
    dataset_name: str | None = None
    auc_threshold: float = 0.9


class ComputePsiInput(StrictModel):
    dataset_id: str
    base_vintages: list[int]
    compare_vintages: list[int]
    features: list[str] | None = None
    n_bins: int = 10


class MakeSplitsInput(StrictModel):
    dataset_id: str
    strategy: Literal["vintage", "stratified_random"]
    oot_vintages: list[int] = []
    val_share: float = 0.2
    seed: int


def load_dataset(ctx: ToolContext, req: LoadDatasetInput) -> LoadDatasetOutput:
    ref = req.dataset_ref
    loaded = datasets.load_dataset(ref.adapter, ctx.settings.data_dir, seed=ref.seed)
    made_by = producer(ctx, "load_dataset", TOOL_VERSION)
    n_rows = len(loaded.frame)
    dataset = ctx.store.put_frame(
        loaded.frame,
        kind="dataset",
        producer=made_by,
        summary={
            "adapter": ref.adapter,
            "seed": ref.seed,
            "n_rows": n_rows,
            "oot_capable": loaded.oot_capable,
        },
    )
    dictionary = ctx.store.put_model(loaded.dictionary, kind="column_dictionary", producer=made_by)
    return LoadDatasetOutput(
        dataset=dataset, dictionary=dictionary, oot_capable=loaded.oot_capable, n_rows=n_rows
    )


def profile_dataset(ctx: ToolContext, req: ProfileDatasetInput) -> ArtifactRef:
    frame = load_frame(ctx, req.dataset_id)
    report = core_profile(frame, dataset=req.dataset_name or req.dataset_id)
    return ctx.store.put_model(
        report,
        kind="profile",
        producer=producer(ctx, "profile_dataset", TOOL_VERSION),
        summary={
            "n_rows": report.n_rows,
            "n_features": report.n_features,
            "target_rate": report.target_rate,
        },
    )


def check_leakage(ctx: ToolContext, req: CheckLeakageInput) -> ArtifactRef:
    frame = load_frame(ctx, req.dataset_id)
    report = core_check_leakage(
        frame, dataset=req.dataset_name or req.dataset_id, auc_threshold=req.auc_threshold
    )
    return ctx.store.put_model(
        report,
        kind="leakage",
        producer=producer(ctx, "check_leakage", TOOL_VERSION),
        summary={
            "flagged": [flag.feature for flag in report.flags],
            "duplicate_rows": report.duplicate_rows,
        },
    )


def compute_psi(ctx: ToolContext, req: ComputePsiInput) -> ArtifactRef:
    frame = load_frame(ctx, req.dataset_id)
    report = core_compute_psi(
        frame,
        base_vintages=req.base_vintages,
        compare_vintages=req.compare_vintages,
        features=req.features,
        n_bins=req.n_bins,
    )
    worst = max(report.entries, key=lambda e: e.psi, default=None)
    return ctx.store.put_model(
        report,
        kind="psi",
        producer=producer(ctx, "compute_psi", TOOL_VERSION),
        summary={
            "max_psi": worst.psi if worst is not None else None,
            "worst_feature": worst.feature if worst is not None else None,
        },
    )


def make_splits(ctx: ToolContext, req: MakeSplitsInput) -> ArtifactRef:
    frame = load_frame(ctx, req.dataset_id)
    manifest = core_make_splits(
        frame,
        strategy=req.strategy,
        seed=req.seed,
        oot_vintages=req.oot_vintages,
        val_share=req.val_share,
    )
    splits: tuple[Split, ...] = ("train", "val", "oot")
    rows = {split: len(manifest.ids(split)) for split in splits if split in manifest.row_ids}
    return ctx.store.put_model(
        manifest,
        kind="split_manifest",
        producer=producer(ctx, "make_splits", TOOL_VERSION),
        summary={"rows": rows, "oot_available": manifest.oot_available},
    )


_TOOLS = [
    (
        "load_dataset",
        "Load a canonical dataset by adapter name and seed; store it as Parquet with its "
        "column dictionary.",
        load_dataset,
        LoadDatasetInput,
        LoadDatasetOutput,
    ),
    (
        "profile_dataset",
        "Profile a stored dataset: row counts, target rates by vintage, per-feature statistics.",
        profile_dataset,
        ProfileDatasetInput,
        ArtifactRef,
    ),
    (
        "check_leakage",
        "Screen every feature of a stored dataset for leakage signatures and count duplicate rows.",
        check_leakage,
        CheckLeakageInput,
        ArtifactRef,
    ),
    (
        "compute_psi",
        "Per-feature population stability index of compare vintages against base vintages.",
        compute_psi,
        ComputePsiInput,
        ArtifactRef,
    ),
    (
        "make_splits",
        "Build a deterministic train/val/OOT split manifest for a stored dataset.",
        make_splits,
        MakeSplitsInput,
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
