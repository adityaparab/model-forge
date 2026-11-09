import asyncio

from mcp import Client
from modelforge_core.schemas import ColumnDictionary, ProfileArtifact, PSIReport, SplitManifest
from modelforge_lineage.models import ArtifactRef, Producer
from modelforge_mcp.registry import build_server
from modelforge_mcp.schemas import DatasetRef
from modelforge_mcp.servers.data_profiler import (
    CheckLeakageInput,
    ComputePsiInput,
    LoadDatasetInput,
    LoadDatasetOutput,
    MakeSplitsInput,
    ProfileDatasetInput,
)


def test_load_dataset_stores_parquet_and_dictionary(call, store) -> None:
    request = LoadDatasetInput(dataset_ref=DatasetRef(adapter="synthetic_credit"))
    out = LoadDatasetOutput.model_validate(
        call("data-profiler", "load_dataset", request).structured_content
    )
    assert out.n_rows == 4000 and out.oot_capable and out.dataset.kind == "dataset"
    assert out.dataset.producer == Producer(
        server="data-profiler", tool="load_dataset", version="1.0.0"
    )
    dictionary = store.get_model(out.dictionary.id, ColumnDictionary)
    assert dictionary.feature_names == [f"f_{i}" for i in range(1, 7)]
    again = LoadDatasetOutput.model_validate(
        call("data-profiler", "load_dataset", request).structured_content
    )
    assert again.dataset.id == out.dataset.id


def test_profile_leakage_psi_splits(call, store, dataset_id) -> None:
    prof = ArtifactRef.model_validate(
        call(
            "data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id=dataset_id)
        ).structured_content
    )
    assert store.get_model(prof.id, ProfileArtifact).n_rows == 4000
    assert prof.summary["n_rows"] == 4000
    leak = ArtifactRef.model_validate(
        call(
            "data-profiler", "check_leakage", CheckLeakageInput(dataset_id=dataset_id)
        ).structured_content
    )
    assert leak.summary["flagged"] == []
    psi = ArtifactRef.model_validate(
        call(
            "data-profiler",
            "compute_psi",
            ComputePsiInput(
                dataset_id=dataset_id, base_vintages=[1, 2, 3, 4], compare_vintages=[5]
            ),
        ).structured_content
    )
    assert store.get_model(psi.id, PSIReport).compare_vintages == [5]
    assert psi.summary["max_psi"] < 0.1
    split = ArtifactRef.model_validate(
        call(
            "data-profiler",
            "make_splits",
            MakeSplitsInput(dataset_id=dataset_id, strategy="vintage", oot_vintages=[5], seed=42),
        ).structured_content
    )
    manifest = store.get_model(split.id, SplitManifest)
    assert manifest.oot_available and split.summary["rows"]["oot"] == len(manifest.ids("oot"))


def test_make_splits_missing_vintage_is_invalid_input(call, dataset_id) -> None:
    r = call(
        "data-profiler",
        "make_splits",
        MakeSplitsInput(dataset_id=dataset_id, strategy="vintage", oot_vintages=[99], seed=1),
    )
    assert r.is_error
    assert '"code": "invalid_input"' in r.content[0].text.replace('"code":"', '"code": "')


def test_tool_versions_are_published(settings) -> None:
    async def _list():
        async with Client(build_server("data-profiler", settings)) as c:
            return (await c.list_tools()).tools

    tools = asyncio.run(_list())
    assert {t.name for t in tools} == {
        "load_dataset",
        "profile_dataset",
        "check_leakage",
        "compute_psi",
        "make_splits",
    }
    assert all(t.meta == {"version": "1.0.0"} and t.output_schema is not None for t in tools)
