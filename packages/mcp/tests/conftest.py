import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from mcp import Client
from mcp.types import CallToolResult
from modelforge_core.schemas import StrictModel
from modelforge_lineage.store import ArtifactStore
from modelforge_mcp.common import ServerName, ServerSettings
from modelforge_mcp.registry import build_server
from modelforge_mcp.schemas import DatasetRef
from modelforge_mcp.servers.data_profiler import LoadDatasetInput, LoadDatasetOutput

REPO_ROOT = Path(__file__).resolve().parents[3]

CallFn = Callable[[ServerName, str, StrictModel], CallToolResult]


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> ServerSettings:
    root = tmp_path_factory.mktemp("mcp")
    return ServerSettings(
        artifact_dir=root / "artifacts",
        data_dir=root / "data",
        templates_dir=REPO_ROOT / "templates",
    )


@pytest.fixture(scope="module")
def store(settings: ServerSettings) -> ArtifactStore:
    return ArtifactStore(settings.artifact_dir)


@pytest.fixture(scope="module")
def call(settings: ServerSettings) -> CallFn:
    def _call(server_name: ServerName, tool: str, request: StrictModel) -> CallToolResult:
        async def _run() -> CallToolResult:
            async with Client(build_server(server_name, settings)) as c:
                return await c.call_tool(tool, {"request": request.model_dump(mode="json")})

        return asyncio.run(_run())

    return _call


@pytest.fixture(scope="module")
def dataset_id(call: CallFn) -> str:
    result = call(
        "data-profiler",
        "load_dataset",
        LoadDatasetInput(dataset_ref=DatasetRef(adapter="synthetic_credit", seed=42)),
    )
    assert not result.is_error, result.content
    return LoadDatasetOutput.model_validate(result.structured_content).dataset.id
