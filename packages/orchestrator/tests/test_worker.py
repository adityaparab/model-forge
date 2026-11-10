from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.runtime import Runtime
from modelforge_lineage import settings as server_settings
from modelforge_lineage.approvals import record_approval
from modelforge_lineage.db import Database
from modelforge_lineage.events import list_events
from modelforge_lineage.inventory import get_for_run
from modelforge_lineage.models import GateDecision
from modelforge_lineage.queue import claim, enqueue
from modelforge_lineage.runs import get_run, list_replays, request_pause, set_error
from modelforge_lineage.steps import list_artifacts, list_steps
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator import graph, worker
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.replay import ReplayError, create_replay
from modelforge_orchestrator.schemas import ReplayReport, RunState
from modelforge_orchestrator.tools import InProcessToolClient, ToolError
from modelforge_orchestrator.worker import run_loop, run_once
from structlog.testing import capture_logs

from .conftest import REPO, SYNTHETIC_BRIEF, queued_run

SYNTHETIC = REPO / "examples/briefs/synthetic.yaml"


@pytest.mark.db
def test_execute_parks_at_gates_and_completes(
    settings: Settings, db: Database, store: ArtifactStore, checkpointer: PostgresSaver
) -> None:
    database = db  # truncated: the empty-queue assertion below must not see other tests' runs
    run_id = queued_run(database, store, SYNTHETIC, requester="alice")
    run = run_once(
        settings,
        database,
        store,
        run_id=run_id,
        tool_client_factory=InProcessToolClient,
        worker_id="worker-test:1",
    )
    assert run is not None and run.status == "awaiting_scope"
    with database.connection() as conn:
        events = list_events(conn, run_id)
        assert [e.type for e in events][-2:] == ["gate.requested", "run.status_changed"]
        claimed = next(e for e in events if e.type == "run.claimed")
        assert claimed.payload["worker_id"] == "worker-test:1"
        assert claim(conn, "x") is None
    for decision in (
        GateDecision(gate="scope", decision="approve", approver="bob"),
        GateDecision(gate="model", decision="accept", approver="bob"),
        GateDecision(gate="signoff", decision="approve", approver="bob"),
    ):
        with database.transaction() as conn:
            record_approval(conn, run_id, decision, current_plan=None)
            enqueue(conn, run_id, "resume")
        run = run_once(
            settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
        )
    assert run is not None and run.status == "completed" and run.manifest_hash is not None
    with database.connection() as conn:
        entry = get_for_run(conn, run_id)
        assert entry is not None and entry.status == "approved"


@pytest.mark.db
def test_tool_failure_parks_run_and_resume_continues(
    settings: Settings,
    database: Database,
    store: ArtifactStore,
    checkpointer: PostgresSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = queued_run(database, store, SYNTHETIC)
    calls = {"n": 0}
    real_call = InProcessToolClient.call

    def flaky(self: InProcessToolClient, server: Any, tool: str, request: Any, **kw: Any) -> Any:
        if tool == "make_splits" and calls["n"] == 0:
            calls["n"] += 1
            raise ToolError(
                server=server, tool=tool, version="1.0.0", code="transport", message="server died"
            )
        return real_call(self, server, tool, request, **kw)

    monkeypatch.setattr(InProcessToolClient, "call", flaky)
    run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    with database.transaction() as conn:
        record_approval(
            conn,
            run_id,
            GateDecision(gate="scope", decision="approve", approver="alice"),
            current_plan=None,
        )
        enqueue(conn, run_id, "resume")
    run = run_once(
        settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
    )
    assert run is not None and run.status == "needs_attention" and run.error is not None
    assert run.error["kind"] == "tool" and run.error["error"]["code"] == "transport"
    with database.transaction() as conn:
        set_error(conn, run_id, None)
        enqueue(conn, run_id, "resume")
    run = run_once(
        settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
    )
    assert run is not None and run.status == "awaiting_model"
    with database.connection() as conn:
        split_steps = [s for s in list_steps(conn, run_id) if s.node == "split"]
    assert [s.status for s in split_steps] == ["failed", "succeeded"]
    assert [s.attempt for s in split_steps] == [1, 2]


@pytest.mark.db
def test_failure_in_first_node_after_a_gate_resumes(
    settings: Settings,
    database: Database,
    store: ArtifactStore,
    checkpointer: PostgresSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`document` follows `gate_model`, which routes by `Command`; a failure there must resume."""
    run_id = queued_run(database, store, SYNTHETIC)
    calls = {"n": 0}
    real_call = InProcessToolClient.call

    def flaky(self: InProcessToolClient, server: Any, tool: str, request: Any, **kw: Any) -> Any:
        # The first call is `scope`'s; the second is `document`'s first tool call.
        if tool == "list_template_sections":
            calls["n"] += 1
        if tool == "list_template_sections" and calls["n"] == 2:
            raise ToolError(
                server=server, tool=tool, version="1.0.0", code="transport", message="server died"
            )
        return real_call(self, server, tool, request, **kw)

    monkeypatch.setattr(InProcessToolClient, "call", flaky)
    run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    for decision in (
        GateDecision(gate="scope", decision="approve", approver="bob"),
        GateDecision(gate="model", decision="accept", approver="bob"),
    ):
        with database.transaction() as conn:
            record_approval(conn, run_id, decision, current_plan=None)
            enqueue(conn, run_id, "resume")
        run = run_once(
            settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
        )
    assert run is not None and run.status == "needs_attention" and run.error is not None
    assert run.error["node"] == "document" and run.error["error"]["code"] == "transport"
    with database.transaction() as conn:
        set_error(conn, run_id, None)
        enqueue(conn, run_id, "resume")
    run = run_once(
        settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
    )
    assert run is not None and run.status == "awaiting_signoff"


@pytest.mark.db
def test_failure_in_challenge_after_document_resumes(
    settings: Settings,
    database: Database,
    store: ArtifactStore,
    checkpointer: PostgresSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`challenge` follows `document`, the first node after `gate_model`; a failure must resume."""
    run_id = queued_run(database, store, SYNTHETIC)
    seen = {"documented": False, "failed": False}
    real_call = InProcessToolClient.call

    def flaky(self: InProcessToolClient, server: Any, tool: str, request: Any, **kw: Any) -> Any:
        # `check_completeness` is `document`'s last tool call; the next one is `challenge`'s.
        if seen["documented"] and not seen["failed"]:
            seen["failed"] = True
            raise ToolError(
                server=server, tool=tool, version="1.0.0", code="transport", message="server died"
            )
        if tool == "check_completeness":
            seen["documented"] = True
        return real_call(self, server, tool, request, **kw)

    monkeypatch.setattr(InProcessToolClient, "call", flaky)
    run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    for decision in (
        GateDecision(gate="scope", decision="approve", approver="bob"),
        GateDecision(gate="model", decision="accept", approver="bob"),
    ):
        with database.transaction() as conn:
            record_approval(conn, run_id, decision, current_plan=None)
            enqueue(conn, run_id, "resume")
        run = run_once(
            settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
        )
    assert run is not None and run.status == "needs_attention" and run.error is not None
    assert run.error["node"] == "challenge" and run.error["error"]["code"] == "transport"
    with database.connection() as conn:
        challenge_steps = [s for s in list_steps(conn, run_id) if s.node == "challenge"]
    assert [s.status for s in challenge_steps] == ["failed"]
    with database.transaction() as conn:
        set_error(conn, run_id, None)
        enqueue(conn, run_id, "resume")
    run = run_once(
        settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
    )
    assert run is not None and run.status == "awaiting_signoff"
    with database.connection() as conn:
        nodes = [s.node for s in list_steps(conn, run_id) if s.status == "succeeded"]
    assert "remediate" in nodes and nodes.count("challenge") >= 2


@pytest.mark.db
def test_replay_reproduces_every_deterministic_ref(
    settings: Settings,
    database: Database,
    store: ArtifactStore,
    checkpointer: PostgresSaver,
    completed_run_id: str,
) -> None:
    with database.transaction() as conn:
        replay = create_replay(conn, completed_run_id)
    run = run_once(
        settings, database, store, run_id=replay.id, tool_client_factory=InProcessToolClient
    )
    assert run is not None and run.status == "completed" and run.replay_of == completed_run_id
    with database.connection() as conn:
        report_ref = next(a for a in list_artifacts(conn, run.id) if a.kind == "replay_report")
        assert all(e.type != "gate.requested" for e in list_events(conn, run.id))
        assert get_for_run(conn, run.id) is None
    report = store.get_model(report_ref.id, ReplayReport)
    assert report.ok and report.mismatched == [] and report.missing == []
    assert {"champion", "champion_scores", "validation", "document_md"} <= set(report.matched)


@pytest.mark.db
def test_resume_claim_for_sealed_run_is_rejected_untouched(
    settings: Settings,
    database: Database,
    store: ArtifactStore,
    checkpointer: PostgresSaver,
    completed_run_id: str,
) -> None:
    with database.transaction() as conn:
        # a flag planted directly: a rejected claim must leave the run's row untouched
        conn.execute("update runs set pause_requested = true where id = %s", (completed_run_id,))
        enqueue(conn, completed_run_id, "resume")
    run = run_once(
        settings, database, store, run_id=completed_run_id, tool_client_factory=InProcessToolClient
    )
    assert run is not None and run.status == "completed" and run.error is None
    assert run.pause_requested is True
    with database.connection() as conn:
        assert get_run(conn, completed_run_id).status == "completed"
        assert claim(conn, "x", run_id=completed_run_id) is None
        assert list_events(conn, completed_run_id)[-1].type == "worker.rejected_claim"


@pytest.mark.db
def test_create_replay_requires_sealed_run(database: Database, store: ArtifactStore) -> None:
    run_id = queued_run(database, store, SYNTHETIC)
    with database.transaction() as conn, pytest.raises(ReplayError):
        create_replay(conn, run_id)


@pytest.mark.db
def test_run_loop_logs_ready_with_redacted_url_and_idle(
    settings: Settings, db: Database, checkpointer: PostgresSaver, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db, checkpointer  # an empty queue, so the single pass is idle
    # `run_loop` configures JSON logging, which would replace the capturing configuration.
    monkeypatch.setattr(worker, "configure_logging", lambda level: None)
    assert "modelforge:modelforge@" in settings.database_url
    # An earlier `configure_logging` may leave an INFO filter in place; start from the defaults.
    saved = structlog.get_config()
    structlog.reset_defaults()
    try:
        with capture_logs() as logs:
            run_loop(settings, once=True)
    finally:
        structlog.configure(**saved)
    ready = [e for e in logs if e["event"] == "worker.ready"]
    assert len(ready) == 1
    assert "modelforge:***@" in ready[0]["database_url"]
    assert "modelforge:modelforge@" not in ready[0]["database_url"]
    assert ready[0]["artifact_dir"] == str(settings.artifact_dir)
    assert ready[0]["poll_interval_s"] == 2.0
    assert ready[0]["worker_id"]
    assert [e["log_level"] for e in logs if e["event"] == "worker.idle"] == ["debug"]


@pytest.mark.db
def test_worker_parks_paused_and_resume_continues(
    settings: Settings,
    database: Database,
    store: ArtifactStore,
    checkpointer: PostgresSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pause requested as `intake` ends parks the run before `scope`; resume runs `scope`."""
    real_intake = graph.NODES["intake"]
    requested: list[bool] = []

    def intake_then_pause(state: RunState, runtime: Runtime[Deps]) -> Any:
        update = real_intake(state, runtime)
        with runtime.context.lineage.transaction() as conn:
            requested.append(request_pause(conn, state.run_id))
        return update

    monkeypatch.setitem(graph.NODES, "intake", intake_then_pause)
    run_id = queued_run(database, store, SYNTHETIC)
    with database.transaction() as conn:
        assert request_pause(conn, run_id) is False  # queued: refused
    run = run_once(
        settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
    )
    assert requested == [True]
    assert run is not None and run.status == "paused"
    assert run.pause_requested is False and run.error is None
    with database.connection() as conn:
        events = list_events(conn, run_id)
        assert [(s.node, s.status) for s in list_steps(conn, run_id)] == [("intake", "succeeded")]
        assert claim(conn, "x", run_id=run_id) is None  # the queue row was released
    paused = [e for e in events if e.type == "run.paused"]
    assert [e.payload for e in paused] == [{"node": "scope"}]
    monkeypatch.setitem(graph.NODES, "intake", real_intake)
    with database.transaction() as conn:
        enqueue(conn, run_id, "resume")
    run = run_once(
        settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
    )
    assert run is not None and run.status == "awaiting_scope"
    with database.connection() as conn:
        assert [s.node for s in list_steps(conn, run_id)] == ["intake", "scope"]


@pytest.mark.db
def test_late_pause_request_is_cleared_on_finish(
    settings: Settings,
    database: Database,
    store: ArtifactStore,
    checkpointer: PostgresSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pause requested during the exempt `seal` node must not outlive the execution."""
    real_seal = graph.NODES["seal"]
    requested: list[bool] = []

    def seal_then_pause(state: RunState, runtime: Runtime[Deps]) -> Any:
        update = real_seal(state, runtime)
        with runtime.context.lineage.transaction() as conn:
            requested.append(request_pause(conn, state.run_id))
        return update

    monkeypatch.setitem(graph.NODES, "seal", seal_then_pause)
    run_id = queued_run(database, store, SYNTHETIC)
    run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    for decision in (
        GateDecision(gate="scope", decision="approve", approver="bob"),
        GateDecision(gate="model", decision="accept", approver="bob"),
        GateDecision(gate="signoff", decision="approve", approver="bob"),
    ):
        with database.transaction() as conn:
            record_approval(conn, run_id, decision, current_plan=None)
            enqueue(conn, run_id, "resume")
        run = run_once(
            settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
        )
    assert requested == [True]  # the run was running when seal asked
    assert run is not None and run.status == "completed"
    assert run.pause_requested is False


def _drive_to_completed(settings: Settings, database: Database, store: ArtifactStore) -> str:
    """A fresh synthetic run driven through the three gates by the worker; return its id."""
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
def auto_replay_on(db: Database) -> Iterator[None]:
    """``auto_replay`` seeded true; the row is removed afterwards so later tests are unaffected."""
    with db.transaction() as conn:
        assert server_settings.seed(conn, {"auto_replay": True}) == ["auto_replay"]
    yield
    with db.transaction() as conn:
        conn.execute("delete from settings where key = 'auto_replay'")


@pytest.mark.db
def test_completed_run_enqueues_auto_replay_when_on(
    settings: Settings,
    db: Database,
    store: ArtifactStore,
    checkpointer: PostgresSaver,
    auto_replay_on: None,
) -> None:
    run_id = _drive_to_completed(settings, db, store)
    with db.connection() as conn:
        replays = list_replays(conn, run_id)
        assert len(replays) == 1
        replay = replays[0]
        assert replay.status == "queued" and replay.rerun_llm is False
        assert replay.requester == "replay"
        created = next(e for e in list_events(conn, replay.id) if e.type == "run.created")
        assert created.payload["auto"] is True and created.payload["replay_of"] == run_id
        queued = conn.execute("select run_id, kind from run_queue").fetchall()
        assert queued == [{"run_id": replay.id, "kind": "replay"}]
        assert get_run(conn, run_id).status == "completed"
    finished = run_once(settings, db, store, tool_client_factory=InProcessToolClient)
    assert finished is not None and finished.id == replay.id and finished.status == "completed"
    with db.connection() as conn:
        assert list_replays(conn, replay.id) == []
        assert len(list_replays(conn, run_id)) == 1
        assert conn.execute("select count(*) as n from run_queue").fetchone() == {"n": 0}


@pytest.mark.db
def test_no_auto_replay_when_off(
    settings: Settings, db: Database, store: ArtifactStore, checkpointer: PostgresSaver
) -> None:
    run_id = _drive_to_completed(settings, db, store)
    with db.connection() as conn:
        assert list_replays(conn, run_id) == []
        assert conn.execute("select count(*) as n from run_queue").fetchone() == {"n": 0}
