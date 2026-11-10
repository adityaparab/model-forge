"""The worker: claims queued runs, drives the graph, and parks runs at gates or for attention.

One `execute` call runs a claimed run until the graph pauses at a gate, finishes, is paused at a
node boundary (`steps.PauseRequested`), or raises. The run's status, `gate.requested` events and
`runs.error` are written here; the nodes write the rest. The checkpointed state is never edited
here, so a resumed run continues where it failed.
"""

import importlib
import os
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from modelforge_gateway.gateway import Gateway
from modelforge_lineage import migrate, queue, runs
from modelforge_lineage.approvals import list_approvals
from modelforge_lineage.db import Database
from modelforge_lineage.events import append_event, list_events
from modelforge_lineage.facade import Lineage
from modelforge_lineage.models import GateName, QueuedRun, RunRecord, RunStatus
from modelforge_lineage.observability import configure_logging, get_logger
from modelforge_lineage.store import ArtifactStore
from modelforge_mcp.schemas import DatasetRef
from pydantic import BaseModel

from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.graph import build_graph
from modelforge_orchestrator.schemas import ErrorRecord, GatePrompt, RunState
from modelforge_orchestrator.steps import PauseRequested
from modelforge_orchestrator.tools import StdioToolClient, ToolClient

__all__ = [
    "STATE_MODULES",
    "WorkerError",
    "execute",
    "initial_state",
    "make_checkpointer",
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
        rerun_llm=run.rerun_llm,
    )


STATE_MODULES: tuple[tuple[str, ...], ...] = (
    ("modelforge_orchestrator", "schemas"),
    ("modelforge_lineage", "models"),
    ("modelforge_core", "schemas"),
    ("modelforge_mcp", "schemas"),
)
"""The modules whose Pydantic models the run state checkpoints."""


def _state_types() -> tuple[tuple[str, str], ...]:
    """``(module, class)`` for every Pydantic model defined in `STATE_MODULES`.

    The msgpack allowlist matches exact ``(module, class)`` keys, not module prefixes.
    """
    found: list[tuple[str, str]] = []
    for path in STATE_MODULES:
        module = importlib.import_module(".".join(path))
        for name, value in vars(module).items():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__
            ):
                found.append((module.__name__, name))
    return tuple(found)


def make_checkpointer(db: Database) -> PostgresSaver:
    """A saver over ``db``'s pool whose deserializer allows exactly the run state's models.

    An explicit allowlist: the default serializer is permissive and ignores `with_allowlist`.
    """
    serde = JsonPlusSerializer(allowed_msgpack_modules=_state_types())
    return PostgresSaver(db.pool, serde=serde)


def setup_checkpointer(db: Database) -> PostgresSaver:
    """`make_checkpointer` with its tables created (idempotent)."""
    saver = make_checkpointer(db)
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
    if kind == "resume" and run.status in ("needs_attention", "paused"):
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
    graph = build_graph(make_checkpointer(db))
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
            gateway = Gateway.for_run(settings.gateway, run.profile, Lineage(db), store)
            deps = Deps(
                tools=tools, lineage=Lineage(db), store=store, settings=settings, gateway=gateway
            )
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
    except PauseRequested as exc:
        # LangGraph 1.2.20 re-raises a node's exception as itself, not in an `ExceptionGroup`.
        with db.transaction() as conn:
            runs.set_status(conn, run_id, "paused")
            runs.clear_pause(conn, run_id)
            append_event(conn, run_id, "run.paused", {"node": exc.node})
        log.info("run.paused", node=exc.node)
    except Exception as exc:
        record = _error_record(db, run_id, exc)
        payload = record.model_dump(mode="json")
        with db.transaction() as conn:
            runs.set_error(conn, run_id, payload)
            runs.set_status(conn, run_id, "needs_attention")
        # No `graph.update_state` here: LangGraph attributes it to the last completed node, and
        # after a gate that routed by `Command` that drops the pending task, so `resume` ran
        # nothing. The error lives in `runs.error` and the `error.recorded` event.
        log.error("run.needs_attention", error=payload)
    finally:
        with db.transaction() as conn:
            # A pause requested too late (during an exempt node) must not reach a later execution.
            runs.clear_pause(conn, run_id)
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
        log = get_logger(component="worker")
        url = urlsplit(settings.database_url)
        if url.password is not None:
            userinfo, _, host = url.netloc.rpartition("@")
            user = userinfo.partition(":")[0]
            url = url._replace(netloc=f"{user}:***@{host}")
        log.info(
            "worker.ready",
            database_url=urlunsplit(url),
            artifact_dir=str(settings.artifact_dir),
            poll_interval_s=poll_interval_s,
            worker_id=_this_worker_id(),
        )
        while True:
            run = run_once(settings, db, store)
            if run is None:
                log.debug("worker.idle")
            if once:
                return
            if run is None:
                time.sleep(poll_interval_s)
    finally:
        db.close()
