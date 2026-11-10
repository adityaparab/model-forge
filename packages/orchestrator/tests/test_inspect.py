import json
import re
from typing import Any

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.inspect import gate_prompt, run_refs, run_snapshot, run_state
from modelforge_orchestrator.replay import create_replay
from modelforge_orchestrator.submit import create_run, create_run_from_brief
from modelforge_orchestrator.tools import InProcessToolClient
from modelforge_orchestrator.worker import run_once
from pydantic import TypeAdapter

from .conftest import SYNTHETIC_BRIEF, Cli, queued_run

_TRACE_ID = re.compile(r"[0-9a-f]{32}")


@pytest.mark.db
def test_run_state_none_before_first_step_and_refs_after(
    settings: Settings, database: Database, store: ArtifactStore, checkpointer: PostgresSaver
) -> None:
    run_id = queued_run(database, store, SYNTHETIC_BRIEF)
    assert run_state(database, run_id) is None
    assert run_refs(database, run_id) == {}
    assert gate_prompt(database, run_id) is None
    run = run_once(
        settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
    )
    assert run is not None and run.status == "awaiting_scope"
    state = run_state(database, run_id)
    assert state is not None and state.run_id == run_id
    assert "scope_notes" in state.refs  # the scope gate parks before any dataset ref exists
    assert run_refs(database, run_id) == state.refs
    prompt = gate_prompt(database, run_id)
    assert prompt is not None and prompt["gate"] == "scope"


@pytest.mark.db
def test_run_snapshot_matches_cli_status_json(
    cli: Cli, completed_cli_run: str, database: Database
) -> None:
    result = cli("status", completed_cli_run, "--json")
    assert result.returncode == 0, result.stderr
    snapshot = run_snapshot(database, completed_cli_run)
    assert set(snapshot) == {"run", "steps", "refs", "gate_prompt", "llm_calls", "findings"}
    dumped: dict[str, Any] = TypeAdapter(dict[str, Any]).dump_python(snapshot, mode="json")
    assert json.loads(result.stdout) == dumped


@pytest.mark.db
def test_run_snapshot_unknown_run_raises(database: Database) -> None:
    with pytest.raises(runs.RunNotFound):
        run_snapshot(database, "run_000000000000")


@pytest.mark.db
def test_create_run_from_bytes_sets_trace_id(
    database: Database, store: ArtifactStore, settings: Settings, completed_run_id: str
) -> None:
    options: dict[str, Any] = {
        "requester": "alice",
        "profile": "fake",
        "cost_cap_usd": 5.0,
        "gateway": settings.gateway,
    }
    from_bytes = create_run(database, store, SYNTHETIC_BRIEF.read_bytes(), **options)
    from_path = create_run_from_brief(database, store, SYNTHETIC_BRIEF, **options)
    with database.transaction() as conn:
        replays = [create_replay(conn, completed_run_id) for _ in range(2)]
    trace_ids = [from_bytes.trace_id, from_path.trace_id, *(r.trace_id for r in replays)]
    assert all(t is not None and _TRACE_ID.fullmatch(t) for t in trace_ids)
    assert len(set(trace_ids)) == 4
    assert from_bytes.brief_hash == from_path.brief_hash
