import os
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.runtime import Runtime
from modelforge_lineage import runs
from modelforge_lineage.approvals import record_approval
from modelforge_lineage.db import Database
from modelforge_lineage.facade import Lineage
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import GateDecision
from modelforge_lineage.queue import enqueue
from modelforge_lineage.store import MEDIA_YAML, ArtifactStore
from modelforge_lineage.testing import database, database_url, db
from modelforge_mcp.schemas import DatasetRef
from modelforge_orchestrator.briefs import BriefError, brief_hash, parse_brief
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.build import build
from modelforge_orchestrator.nodes.challenge import challenge
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.features import features
from modelforge_orchestrator.nodes.intake import intake
from modelforge_orchestrator.nodes.profile import profile
from modelforge_orchestrator.nodes.remediate import remediate
from modelforge_orchestrator.nodes.scope import scope
from modelforge_orchestrator.nodes.split import split
from modelforge_orchestrator.nodes.validate import validate
from modelforge_orchestrator.schemas import RunState, apply_update
from modelforge_orchestrator.tools import InProcessToolClient
from modelforge_orchestrator.worker import run_once

__all__ = [
    "REPO",
    "Cli",
    "checkpointer",
    "cli",
    "completed_cli_run",
    "completed_run_id",
    "database",
    "database_url",
    "db",
    "deps",
    "make_run",
    "prepared_state",
    "prepared_state_no_oot",
    "queued_run",
    "run_id_from",
    "runtime",
    "scoped_state",
    "settings",
    "signed_off_state",
    "state_no_oot",
    "store",
    "synthetic_state",
    "validated_state",
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


Cli = Callable[..., "subprocess.CompletedProcess[str]"]
"""``cli(*args)`` runs ``uv run modelforge *args`` against the test database and directories."""


@pytest.fixture
def cli(settings: Settings, checkpointer: PostgresSaver) -> Cli:
    """Run the real ``modelforge`` entry point as a subprocess.

    A subprocess rather than Typer's `CliRunner`: the MCP stdio transport needs a real
    ``stderr`` file descriptor when it spawns the servers.
    """
    del checkpointer  # the checkpoint tables must exist before the CLI drives a run
    env = {
        **os.environ,
        "MODELFORGE_DATABASE_URL": settings.database_url,
        "MODELFORGE_ARTIFACT_DIR": str(settings.artifact_dir),
        "MODELFORGE_DATA_DIR": str(settings.data_dir),
        "MODELFORGE_TEMPLATES_DIR": str(settings.templates_dir),
    }

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["uv", "run", "modelforge", *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    return run


def run_id_from(text: str) -> str:
    """The first run id in ``text``."""
    found = re.search(r"run_[0-9a-f]{12}", text)
    if found is None:
        raise AssertionError(f"no run id in {text!r}")
    return found.group(0)


@pytest.fixture
def completed_cli_run(cli: Cli) -> str:
    """A synthetic run driven through the three gates with the CLI; return its id."""
    result = cli("run", str(SYNTHETIC_BRIEF), "--requester", "alice")
    assert result.returncode == 0, result.stderr
    run_id = run_id_from(result.stdout)
    for args in (
        ("scope", "--decision", "approve", "--approver", "alice"),
        ("model", "--decision", "accept", "--approver", "bob"),
        ("signoff", "--decision", "approve", "--approver", "bob"),
    ):
        result = cli("approve", run_id, *args)
        assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"{run_id} completed"
    return run_id


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


def queued_run(
    db: Database, store: ArtifactStore, brief_path: Path, *, requester: str = "alice"
) -> str:
    """`make_run` followed by enqueueing the run as a ``start``; return the run id."""
    run_id = make_run(db, store, brief_path, requester=requester).run_id
    with db.transaction() as conn:
        enqueue(conn, run_id, "start")
    return run_id


@pytest.fixture
def completed_run_id(
    settings: Settings, database: Database, store: ArtifactStore, checkpointer: PostgresSaver
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


@pytest.fixture
def scoped_state(synthetic_state: RunState, runtime: Runtime[Deps]) -> RunState:
    """``synthetic_state`` after `intake` and `scope`."""
    state = synthetic_state
    for node in (intake, scope):
        state = apply_update(state, node(state, runtime))
    return state


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


@pytest.fixture
def validated_state(prepared_state: RunState, runtime: Runtime[Deps]) -> RunState:
    """``prepared_state`` after `features`, `build` and `validate`."""
    state = prepared_state
    for node in (features, build, validate):
        state = apply_update(state, node(state, runtime))
    return state


@pytest.fixture
def signed_off_state(validated_state: RunState, runtime: Runtime[Deps]) -> RunState:
    """``validated_state`` after `document`, `challenge` and `remediate`, signed off."""
    state = validated_state
    for node in (document, challenge, remediate):
        state = apply_update(state, node(state, runtime))
    signoff = GateDecision(
        gate="signoff",
        decision="approve_with_conditions",
        approver="bob",
        conditions=["monitor PSI quarterly"],
    )
    return apply_update(state, {"gates": {"signoff": signoff}, "status": "completed"})
