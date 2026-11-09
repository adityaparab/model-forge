"""The worker: claims queued runs, drives the graph, and parks runs at gates or for attention.

One `execute` call runs a claimed run until the graph pauses at a gate, finishes, or raises.
The run's status, `gate.requested` events and errors are written here; the nodes write the rest.
"""

import os
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from modelforge_lineage import migrate, queue, runs
from modelforge_lineage.approvals import list_approvals
from modelforge_lineage.db import Database
from modelforge_lineage.events import append_event, list_events
from modelforge_lineage.facade import Lineage
from modelforge_lineage.models import GateName, QueuedRun, RunRecord, RunStatus
from modelforge_lineage.observability import configure_logging, get_logger
from modelforge_lineage.store import ArtifactStore
from modelforge_mcp.schemas import DatasetRef

from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.graph import build_graph
from modelforge_orchestrator.schemas import ErrorRecord, GatePrompt, RunState
from modelforge_orchestrator.tools import StdioToolClient, ToolClient

__all__ = [
    "WorkerError",
    "execute",
    "initial_state",
    "run_loop",
    "run_once",
    "setup_checkpointer",
    "worker_id",
]

ToolClientFactory = Callable[[Settings], ToolClient]

_AWAITING: dict[GateName, RunStatus] = {
    "scope": "awaiting_scope",
    "model": "awaiting_model",
    "signoff": "awaiting_signoff",
}
_GATE_OF: dict[RunStatus, GateName] = {status: gate for gate, status in _AWAITING.items()}


class WorkerError(ValueError):
    """A claimed queue row whose kind does not fit the run's status."""


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


# `execute` and `run_once` take a `worker_id` argument that shadows the function.
_this_worker_id = worker_id


def initial_state(run: RunRecord) -> RunState:
    return RunState(
        run_id=run.id,
        requester=run.requester,
        profile=run.profile,
        seed=run.seed,
        brief_hash=run.brief_hash,
        brief_ref=run.brief_ref,
        dataset_ref=DatasetRef.parse(run.dataset_ref),
        replay_of=run.replay_of,
    )


def setup_checkpointer(db: Database) -> PostgresSaver:
    """A checkpointer over ``db``'s pool with its tables created (idempotent)."""
    saver = PostgresSaver(db.pool)
    saver.setup()
    return saver


def _graph_input(db: Database, run: RunRecord, kind: str) -> RunState | Command[Any] | None:
    """The graph input for a ``kind`` claim of ``run``; `WorkerError` when they do not fit."""
    if kind in ("start", "replay") and run.status == "queued":
        return initial_state(run)
    if kind == "resume" and run.status in _GATE_OF:
        gate = _GATE_OF[run.status]
        with db.connection() as conn:
            approvals = list_approvals(conn, run.id, gate=gate)
        if not approvals:
            raise WorkerError(
                f"run {run.id}: resume claimed at gate {gate} with no recorded approval"
            )
        return Command(resume=approvals[-1].payload.model_dump(mode="json"))
    if kind == "resume" and run.status == "needs_attention":
        return None
    raise WorkerError(f"run {run.id}: cannot execute a {kind!r} claim in status {run.status!r}")


def _error_record(db: Database, run_id: str, exc: Exception) -> ErrorRecord:
    """The error the failing step recorded during this execution, else an internal one."""
    with db.connection() as conn:
        events = list_events(conn, run_id)
    claimed = max((i for i, e in enumerate(events) if e.type == "run.claimed"), default=-1)
    recorded = [e for e in events[claimed + 1 :] if e.type == "error.recorded"]
    if recorded:
        return ErrorRecord.model_validate(recorded[-1].payload)
    return ErrorRecord(
        node="worker",
        attempt=0,
        kind="internal",
        error={"type": type(exc).__name__, "message": str(exc)},
        ts=datetime.now(UTC).isoformat(timespec="microseconds"),
    )


def execute(
    settings: Settings,
    db: Database,
    store: ArtifactStore,
    queued: QueuedRun,
    *,
    tool_client_factory: ToolClientFactory = StdioToolClient,
    worker_id: str | None = None,
) -> RunRecord:
    """Run a claimed run until it parks at a gate, finishes, or fails; release its queue row."""
    run_id = queued.run_id
    log = get_logger(component="worker", run_id=run_id)
    config: RunnableConfig = {"configurable": {"thread_id": run_id}}
    graph = build_graph(PostgresSaver(db.pool))
    try:
        with db.connection() as conn:
            run = runs.get_run(conn, run_id)
        try:
            graph_input = _graph_input(db, run, queued.kind)
        except WorkerError as exc:
            # A claim that does not fit the run leaves its status and checkpoint untouched.
            with db.transaction() as conn:
                append_event(
                    conn,
                    run_id,
                    "worker.rejected_claim",
                    {"kind": queued.kind, "status": run.status, "message": str(exc)},
                )
            log.warning("run.claim_rejected", kind=queued.kind, status=run.status, error=str(exc))
            return run
        claimant = worker_id if worker_id is not None else _this_worker_id()
        with db.transaction() as conn:
            runs.set_status(conn, run_id, "running")
            append_event(conn, run_id, "run.claimed", {"worker_id": claimant, "kind": queued.kind})
        log.info("run.claimed", kind=queued.kind, prior_status=run.status)
        with tool_client_factory(settings) as tools:
            deps = Deps(tools=tools, lineage=Lineage(db), store=store, settings=settings)
            result = graph.invoke(graph_input, config, context=deps)
        if "__interrupt__" in result:
            prompt = GatePrompt.model_validate(result["__interrupt__"][0].value)
            status = _AWAITING[prompt.gate]
            with db.transaction() as conn:
                append_event(
                    conn, run_id, "gate.requested", {"gate": prompt.gate, "summary": prompt.summary}
                )
                runs.set_status(conn, run_id, status)
            log.info("run.parked", status=status)
        else:
            with db.transaction() as conn:
                runs.set_status(conn, run_id, result["status"])
            log.info("run.finished", status=result["status"])
    except Exception as exc:
        record = _error_record(db, run_id, exc)
        payload = record.model_dump(mode="json")
        with db.transaction() as conn:
            runs.set_error(conn, run_id, payload)
            runs.set_status(conn, run_id, "needs_attention")
        graph.update_state(config, {"errors": [record]})
        log.error("run.needs_attention", error=payload)
    finally:
        with db.transaction() as conn:
            queue.release(conn, run_id)
    with db.connection() as conn:
        return runs.get_run(conn, run_id)


def run_once(
    settings: Settings,
    db: Database,
    store: ArtifactStore,
    *,
    run_id: str | None = None,
    tool_client_factory: ToolClientFactory = StdioToolClient,
    worker_id: str | None = None,
) -> RunRecord | None:
    """Claim one queued run (``run_id`` only, when given) and execute it; `None` when idle."""
    claimant = worker_id if worker_id is not None else _this_worker_id()
    with db.transaction() as conn:
        queued = queue.claim(conn, claimant, run_id=run_id)
    if queued is None:
        return None
    return execute(
        settings, db, store, queued, tool_client_factory=tool_client_factory, worker_id=claimant
    )


def run_loop(settings: Settings, *, once: bool = False, poll_interval_s: float = 2.0) -> None:
    """Migrate, set up the checkpointer, then execute queued runs until stopped."""
    configure_logging(settings.log_level)
    db = Database(settings.database_url)
    try:
        store = ArtifactStore(settings.artifact_dir)
        migrate.apply(db)
        setup_checkpointer(db)
        while True:
            run = run_once(settings, db, store)
            if once:
                return
            if run is None:
                time.sleep(poll_interval_s)
    finally:
        db.close()
