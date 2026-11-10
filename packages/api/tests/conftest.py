import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from modelforge_api.app import create_app
from modelforge_api.settings import ApiSettings
from modelforge_gateway.settings import GatewaySettings
from modelforge_lineage.db import Database
from modelforge_lineage.models import RunRecord
from modelforge_lineage.store import ArtifactStore
from modelforge_lineage.testing import database, database_url, db, spans
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.tools import InProcessToolClient
from modelforge_orchestrator.worker import run_once, setup_checkpointer

__all__ = [
    "POLISH_BRIEF",
    "REPO",
    "SYNTHETIC_BRIEF",
    "Drive",
    "api_settings",
    "app",
    "checkpointer",
    "client",
    "database",
    "database_url",
    "db",
    "drive",
    "settings",
    "spans",
]

REPO = Path(__file__).resolve().parents[3]
SYNTHETIC_BRIEF = REPO / "examples/briefs/synthetic.yaml"
POLISH_BRIEF = REPO / "examples/briefs/polish-pd.yaml"


@pytest.fixture
def settings(tmp_path: Path, database: Database) -> Settings:
    return Settings(
        database_url=database.url,
        artifact_dir=tmp_path / "artifacts",
        data_dir=Path(os.environ.get("MODELFORGE_DATA_DIR", REPO / "data")),
        templates_dir=REPO / "templates",
        profile="fake",
        mcp_command=None,
        tool_timeout_s=600.0,
        log_level="INFO",
        knowledge_dir=REPO / "knowledge-base",
        gateway=GatewaySettings(prompts_dir=REPO / "prompts", profiles_dir=REPO / "profiles"),
    )


@pytest.fixture
def api_settings(settings: Settings, tmp_path: Path) -> ApiSettings:
    """API settings over ``settings`` with an empty console directory (console ``missing``)."""
    console = tmp_path / "console"
    console.mkdir()
    return ApiSettings(core=settings, console_dir=console, briefs_dir=REPO / "examples/briefs")


@pytest.fixture
def app(api_settings: ApiSettings) -> FastAPI:
    return create_app(api_settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A client over ``app`` with its lifespan running (database, migrations, seeds)."""
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def checkpointer(database: Database) -> Iterator[PostgresSaver]:
    yield setup_checkpointer(database)


Drive = Callable[[str], RunRecord | None]
"""``drive(run_id)`` executes the run's queued work in-process with the worker."""


@pytest.fixture
def drive(settings: Settings, database: Database, checkpointer: PostgresSaver) -> Drive:
    del checkpointer  # the checkpoint tables must exist before the worker runs
    store = ArtifactStore(settings.artifact_dir)

    def run(run_id: str) -> RunRecord | None:
        return run_once(
            settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
        )

    return run
