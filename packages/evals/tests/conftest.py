"""Fixtures for the evals tests: a completed synthetic run under the ``fake`` profile.

These mirror the orchestrator's fixtures rather than importing its test module.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from modelforge_gateway.gateway import Gateway
from modelforge_gateway.settings import GatewaySettings
from modelforge_lineage import runs
from modelforge_lineage.approvals import record_approval
from modelforge_lineage.db import Database
from modelforge_lineage.facade import Lineage
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import GateDecision
from modelforge_lineage.queue import enqueue
from modelforge_lineage.store import MEDIA_YAML, ArtifactStore
from modelforge_lineage.testing import database, database_url, db
from modelforge_orchestrator.briefs import brief_hash, parse_brief
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.tools import InProcessToolClient
from modelforge_orchestrator.worker import run_once, setup_checkpointer

__all__ = [
    "REPO",
    "SYNTHETIC_BRIEF",
    "checkpointer",
    "completed_run_id",
    "database",
    "database_url",
    "db",
    "deps",
    "queued_run",
    "settings",
    "store",
]

REPO = Path(__file__).resolve().parents[3]
SYNTHETIC_BRIEF = REPO / "examples/briefs/synthetic.yaml"


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
def store(settings: Settings) -> ArtifactStore:
    """The same directory the in-process MCP servers write to."""
    return ArtifactStore(settings.artifact_dir)


@pytest.fixture
def deps(settings: Settings, database: Database) -> Iterator[Deps]:
    store = ArtifactStore(settings.artifact_dir)
    with InProcessToolClient(settings) as client:
        yield Deps(
            tools=client,
            lineage=Lineage(database),
            store=store,
            settings=settings,
            gateway=Gateway.for_run(settings.gateway, "fake", Lineage(database), store),
        )


@pytest.fixture(scope="session")
def checkpointer(database: Database) -> Iterator[object]:
    yield setup_checkpointer(database)


def queued_run(
    db: Database, store: ArtifactStore, brief_path: Path, *, requester: str = "alice"
) -> str:
    """Store the brief, create the run row under ``fake`` and enqueue a ``start``."""
    brief_bytes = brief_path.read_bytes()
    ref = store.put(brief_bytes, kind="brief", media_type=MEDIA_YAML)
    dataset_ref = parse_brief(brief_bytes).dataset
    run_id = new_id("run")
    with db.transaction() as conn:
        runs.create_run(
            conn,
            run_id=run_id,
            requester=requester,
            profile="fake",
            brief_hash=brief_hash(brief_bytes),
            brief_ref=ref.id,
            dataset_ref=dataset_ref.as_string(),
            seed=dataset_ref.seed,
        )
        enqueue(conn, run_id, "start")
    return run_id


@pytest.fixture
def completed_run_id(
    settings: Settings, database: Database, store: ArtifactStore, checkpointer: object
) -> str:
    """A fresh synthetic run driven through the three gates by the worker; return its id."""
    del checkpointer  # the checkpoint tables must exist before the worker runs
    run_id = queued_run(database, store, SYNTHETIC_BRIEF)
    run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    for decision in (
        GateDecision(gate="scope", decision="approve", approver="alice"),
        GateDecision(gate="model", decision="accept", approver="bob"),
        GateDecision(gate="signoff", decision="approve", approver="bob"),
    ):
        with database.transaction() as conn:
            record_approval(conn, run_id, decision, current_plan=None)
            enqueue(conn, run_id, "resume")
        run = run_once(
            settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
        )
    assert run is not None and run.status == "completed"
    return run_id
