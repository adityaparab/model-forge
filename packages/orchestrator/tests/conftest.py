import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.runtime import Runtime
from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.facade import Lineage
from modelforge_lineage.ids import new_id
from modelforge_lineage.store import MEDIA_YAML, ArtifactStore
from modelforge_lineage.testing import database, database_url, db
from modelforge_mcp.schemas import DatasetRef
from modelforge_orchestrator.briefs import BriefError, brief_hash, parse_brief
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.intake import intake
from modelforge_orchestrator.nodes.profile import profile
from modelforge_orchestrator.nodes.scope import scope
from modelforge_orchestrator.nodes.split import split
from modelforge_orchestrator.schemas import RunState, apply_update
from modelforge_orchestrator.tools import InProcessToolClient

__all__ = [
    "REPO",
    "checkpointer",
    "database",
    "database_url",
    "db",
    "deps",
    "make_run",
    "prepared_state",
    "prepared_state_no_oot",
    "runtime",
    "settings",
    "state_no_oot",
    "store",
    "synthetic_state",
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
        profile="default",
        mcp_command=None,
        tool_timeout_s=600.0,
        log_level="INFO",
    )


@pytest.fixture(scope="session")
def checkpointer(database: Database) -> Iterator[PostgresSaver]:
    saver = PostgresSaver(database.pool)
    saver.setup()
    yield saver


def make_run(
    db: Database,
    store: ArtifactStore,
    brief_path: Path | None = None,
    *,
    brief_bytes: bytes | None = None,
    requester: str = "alice",
    replay_of: str | None = None,
) -> RunState:
    """Store the brief as a `brief` artifact, create the run row, and return the initial state.

    A brief that does not parse still gets a run (synthetic dataset, seed 42), so tests can
    exercise `intake` rejecting it.
    """
    if brief_bytes is None:
        if brief_path is None:
            raise ValueError("make_run needs brief_path or brief_bytes")
        brief_bytes = brief_path.read_bytes()
    ref = store.put(brief_bytes, kind="brief", media_type=MEDIA_YAML)
    try:
        dataset_ref = parse_brief(brief_bytes).dataset
    except BriefError:
        dataset_ref = DatasetRef(adapter="synthetic_credit")
    seed = dataset_ref.seed
    run_id = new_id("run")
    with db.transaction() as conn:
        runs.create_run(
            conn,
            run_id=run_id,
            requester=requester,
            profile="default",
            brief_hash=brief_hash(brief_bytes),
            brief_ref=ref.id,
            dataset_ref=dataset_ref.as_string(),
            seed=seed,
            replay_of=replay_of,
        )
    return RunState(
        run_id=run_id,
        requester=requester,
        profile="default",
        seed=seed,
        brief_hash=brief_hash(brief_bytes),
        brief_ref=ref.id,
        dataset_ref=dataset_ref,
        replay_of=replay_of,
        status="running",
    )


@pytest.fixture
def store(settings: Settings) -> ArtifactStore:
    """The same directory the in-process MCP servers write to."""
    return ArtifactStore(settings.artifact_dir)


@pytest.fixture
def deps(settings: Settings, database: Database) -> Iterator[Deps]:
    with InProcessToolClient(settings) as client:
        yield Deps(
            tools=client,
            lineage=Lineage(database),
            store=ArtifactStore(settings.artifact_dir),
            settings=settings,
        )


@pytest.fixture
def runtime(deps: Deps) -> Runtime[Deps]:
    """Enough of a LangGraph `Runtime` for calling nodes directly."""
    return cast("Runtime[Deps]", SimpleNamespace(context=deps))


@pytest.fixture
def synthetic_state(db: Database, store: ArtifactStore) -> RunState:
    return make_run(db, store, SYNTHETIC_BRIEF)


@pytest.fixture
def state_no_oot(db: Database, store: ArtifactStore, tmp_path: Path) -> RunState:
    text = SYNTHETIC_BRIEF.read_text()
    assert "oot_vintages: [5]" in text
    path = tmp_path / "synthetic-no-oot.yaml"
    path.write_text(text.replace("oot_vintages: [5]", "oot_vintages: []"))
    return make_run(db, store, path)


def _prepare(state: RunState, runtime: Runtime[Deps]) -> RunState:
    """``state`` after `intake`, `scope`, `profile` and `split`, each update applied in turn."""
    for node in (intake, scope, profile, split):
        state = apply_update(state, node(state, runtime))
    return state


@pytest.fixture
def prepared_state(synthetic_state: RunState, runtime: Runtime[Deps]) -> RunState:
    return _prepare(synthetic_state, runtime)


@pytest.fixture
def prepared_state_no_oot(state_no_oot: RunState, runtime: Runtime[Deps]) -> RunState:
    return _prepare(state_no_oot, runtime)
