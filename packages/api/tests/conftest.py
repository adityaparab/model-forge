import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from modelforge_api.app import create_app
from modelforge_api.settings import ApiSettings
from modelforge_gateway.settings import GatewaySettings
from modelforge_lineage import settings as server_settings
from modelforge_lineage.approvals import record_approval
from modelforge_lineage.db import Database
from modelforge_lineage.models import GateDecision, RunRecord
from modelforge_lineage.observability import configure_logging
from modelforge_lineage.queue import enqueue
from modelforge_lineage.store import ArtifactStore
from modelforge_lineage.testing import database, database_url, db, spans
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.submit import create_run
from modelforge_orchestrator.tools import InProcessToolClient
from modelforge_orchestrator.worker import run_once, setup_checkpointer

__all__ = [
    "POLISH_BRIEF",
    "REPO",
    "SYNTHETIC_BRIEF",
    "Drive",
    "Submit",
    "api_settings",
    "app",
    "auto_replay",
    "checkpointer",
    "client",
    "completed_run",
    "database",
    "database_url",
    "db",
    "drive",
    "make_settings",
    "settings",
    "spans",
    "submit",
    "submit_via_api",
]

REPO = Path(__file__).resolve().parents[3]
SYNTHETIC_BRIEF = REPO / "examples/briefs/synthetic.yaml"
POLISH_BRIEF = REPO / "examples/briefs/polish-pd.yaml"


def make_settings(tmp: Path, database_url: str) -> Settings:
    """Orchestrator settings over ``database_url`` with artifacts under ``tmp``, profile fake."""
    return Settings(
        database_url=database_url,
        artifact_dir=tmp / "artifacts",
        data_dir=Path(os.environ.get("MODELFORGE_DATA_DIR", REPO / "data")),
        templates_dir=REPO / "templates",
        profile="fake",
        mcp_command=None,
        tool_timeout_s=600.0,
        log_level="INFO",
        knowledge_dir=REPO / "knowledge-base",
        gateway=GatewaySettings(prompts_dir=REPO / "prompts", profiles_dir=REPO / "profiles"),
    )


@pytest.fixture(scope="session", autouse=True)
def _logging() -> None:
    """The api tests' logging, set once: no test depends on what an earlier package's left."""
    configure_logging("WARNING")


@pytest.fixture
def settings(tmp_path: Path, database: Database) -> Settings:
    return make_settings(tmp_path, database.url)


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


Submit = Callable[..., str]
"""``submit(brief=SYNTHETIC_BRIEF, requester="alice")`` creates a queued run; returns its id."""


@pytest.fixture
def submit(settings: Settings, db: Database) -> Submit:
    store = ArtifactStore(settings.artifact_dir)

    def create(brief: Path = SYNTHETIC_BRIEF, requester: str = "alice") -> str:
        record = create_run(
            db,
            store,
            brief.read_bytes(),
            requester=requester,
            profile="fake",
            cost_cap_usd=settings.gateway.cost_cap_usd,
            gateway=settings.gateway,
        )
        return record.id

    return create


def submit_via_api(
    client: TestClient, brief: Path = SYNTHETIC_BRIEF, requester: str = "alice", **fields: Any
) -> dict[str, Any]:
    """``POST /runs`` with the brief's text as JSON, acting as ``requester``; the `RunRecord`."""
    response = client.post(
        "/api/v1/runs",
        json={"brief": brief.read_text(), **fields},
        headers={"X-ModelForge-User": requester},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def auto_replay(db: Database) -> None:
    """Turn the ``auto_replay`` setting on; request it before `completed_run`."""
    with db.transaction() as conn:
        server_settings.set_value(conn, "auto_replay", True, updated_by="test")


@pytest.fixture
def completed_run(submit: Submit, db: Database, drive: Drive) -> str:
    """A fresh synthetic run driven through the three gates by the worker; return its id."""
    run_id = submit()
    drive(run_id)
    run: RunRecord | None = None
    for decision in (
        GateDecision(gate="scope", decision="approve", approver="alice"),
        GateDecision(gate="model", decision="accept", approver="bob"),
        GateDecision(gate="signoff", decision="approve", approver="bob"),
    ):
        with db.transaction() as conn:
            record_approval(conn, run_id, decision, current_plan=None)
            enqueue(conn, run_id, "resume")
        run = drive(run_id)
    assert run is not None and run.status == "completed"
    return run_id
