"""`Step`: the lineage recorder every graph node runs inside.

Each recording operation (entering the step, each tool call's bookkeeping, each stored artifact
or event, and finishing the step) commits in its own transaction, so a failure later in a node
never rolls back what was already recorded. Tool calls themselves run outside any transaction.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from contextvars import Token
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

from modelforge_core.schemas import StrictModel
from modelforge_gateway.schemas import (
    CallMetadata,
    CompletionRequest,
    CompletionResult,
    GatewayError,
)
from modelforge_lineage import events, runs, steps
from modelforge_lineage.canonical import canonical_json, sha256_hex
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import ArtifactRef, StepRecord, ToolCallRecord
from modelforge_lineage.tracing import SPAN_KIND, attr, set_attributes, tracer
from modelforge_mcp.common import ServerName
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from psycopg import Connection
from psycopg.rows import DictRow
from pydantic import BaseModel, ValidationError

from modelforge_orchestrator.briefs import BriefError
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.schemas import ErrorRecord, RunState, apply_update
from modelforge_orchestrator.tools import CURRENT_STEP, ToolCall, ToolError
from modelforge_orchestrator.tools import output_hash as tool_output_hash

__all__ = ["PAUSE_EXEMPT", "PauseRequested", "Step", "record_failure", "state_hash"]

PAUSE_EXEMPT: frozenset[str] = frozenset({"gate_scope", "gate_model", "gate_signoff", "seal"})
"""Nodes that run even with a pause requested: a gate resumes an interrupt and must not stop
between the answer and its step, and `seal` is seconds long."""


class PauseRequested(Exception):  # noqa: N818 - a control signal, not an error
    """A pause was requested for ``run_id``; raised before ``node`` records anything."""

    def __init__(self, run_id: str, node: str) -> None:
        super().__init__(f"run {run_id}: pause requested before {node}")
        self.run_id = run_id
        self.node = node


def state_hash(state: RunState) -> str:
    return sha256_hex(canonical_json(state.model_dump(mode="json")))


def record_failure(span: Span, exc: BaseException, *, trace_content: bool) -> None:
    """Mark ``span`` failed by ``exc``: ERROR status and an ``exception`` event.

    Exception messages and stack traces can carry prompt, brief or data text, so without
    ``trace_content`` the event names only the type (and the gateway kind or tool code).
    """
    kind: str | None = None
    if isinstance(exc, GatewayError):
        kind = exc.kind
    elif isinstance(exc, ToolError) and exc.code:
        kind = exc.code
    if kind is not None:
        span.set_attribute(attr("error_kind"), kind)
    if trace_content:
        span.record_exception(exc)
    else:
        event = {"exception.type": type(exc).__name__}
        if kind is not None:
            event[attr("error_kind")] = kind
        span.add_event("exception", event)
    span.set_status(Status(StatusCode.ERROR))


def _error_record(node: str, attempt: int, exc: BaseException) -> ErrorRecord:
    if isinstance(exc, ToolError):
        kind = "tool"
        error: dict[str, Any] = dict(exc.to_dict())
    elif isinstance(exc, GatewayError):
        kind = "llm"
        error = dict(exc.to_dict())
    else:
        kind = "validation" if isinstance(exc, ValidationError | BriefError) else "internal"
        error = {"type": type(exc).__name__, "message": str(exc)}
    return ErrorRecord(
        node=node,
        attempt=attempt,
        kind=kind,
        error=error,
        ts=datetime.now(UTC).isoformat(timespec="microseconds"),
    )


class Step:
    """Records one execution of a node: its step row, tool calls, artifacts and events."""

    record: StepRecord

    def __init__(self, deps: Deps, state: RunState, node: str) -> None:
        self.deps = deps
        self.state = state
        self.node = node
        self.output_state_hash: str | None = None
        self.last_error: ErrorRecord | None = None
        self.spent_usd: float = 0.0
        self._span: Span = trace.INVALID_SPAN
        self._span_scope: AbstractContextManager[Span] | None = None
        self._step_token: Token[tuple[str, str] | None] | None = None

    @property
    def run_id(self) -> str:
        return self.state.run_id

    def _event(self, conn: Connection[DictRow], type: str, payload: dict[str, Any]) -> None:
        events.append_event(conn, self.run_id, type, payload, step_id=self.record.id)

    def _artifact(self, conn: Connection[DictRow], ref: ArtifactRef) -> None:
        steps.insert_artifact(
            conn,
            run_id=self.run_id,
            ref=ref,
            uri=self.deps.store.uri(ref.id),
            producer_step_id=self.record.id,
        )
        self._event(conn, "artifact.stored", {"id": ref.id, "kind": ref.kind})

    def __enter__(self) -> Self:
        if self.node not in PAUSE_EXEMPT:
            with self.deps.lineage.connection() as conn:
                if runs.pause_requested(conn, self.run_id):
                    # Nothing recorded yet: the checkpoint stays at the previous node's end.
                    raise PauseRequested(self.run_id, self.node)
        input_hash = state_hash(self.state)
        self._open_span(input_hash)
        try:
            with self.deps.lineage.transaction() as conn:
                self.record = steps.start_step(conn, self.run_id, self.node, input_hash)
                self._event(
                    conn,
                    "step.started",
                    {
                        "node": self.node,
                        "attempt": self.record.attempt,
                        "input_state_hash": input_hash,
                    },
                )
        except BaseException as exc:
            self._close_span(exc, status="failed")
            raise
        set_attributes(
            self._span, {attr("step_id"): self.record.id, attr("attempt"): self.record.attempt}
        )
        self._step_token = CURRENT_STEP.set((self.run_id, self.record.id))
        return self

    def _open_span(self, input_hash: str) -> None:
        """Start the ``node.<name>`` span and make it current.

        Its parent is the current span when there is one (LangGraph runs nodes in the caller's
        context), else the run span's context carried in `Deps.trace_context`.
        """
        current = trace.get_current_span().get_span_context().is_valid
        context = None if current else self.deps.trace_context
        self._span = tracer().start_span(
            f"node.{self.node}",
            context=context,
            record_exception=False,
            set_status_on_exception=False,
        )
        set_attributes(
            self._span,
            {
                SPAN_KIND: "CHAIN",
                attr("run_id"): self.run_id,
                attr("node"): self.node,
                attr("input_state_hash"): input_hash,
            },
        )
        self._span_scope = trace.use_span(
            self._span, end_on_exit=True, record_exception=False, set_status_on_exception=False
        )
        self._span_scope.__enter__()

    def _close_span(self, exc: BaseException | None, *, status: str) -> None:
        """Record the outcome on the node span, end it, and restore the previous context."""
        set_attributes(
            self._span,
            {attr("status"): status, attr("output_state_hash"): self.output_state_hash},
        )
        if exc is not None:
            record_failure(self._span, exc, trace_content=self.deps.settings.gateway.trace_content)
        if self._step_token is not None:
            CURRENT_STEP.reset(self._step_token)
            self._step_token = None
        if self._span_scope is not None:
            self._span_scope.__exit__(None, None, None)
            self._span_scope = None

    def tool(
        self,
        server: ServerName,
        tool: str,
        request: StrictModel,
        *,
        timeout_s: float | None = None,
    ) -> ToolCall[Any]:
        try:
            call = self.deps.tools.call(server, tool, request, timeout_s=timeout_s)
        except ToolError as exc:
            with self.deps.lineage.transaction() as conn:
                self._event(
                    conn,
                    "tool.failed",
                    {"server": server, "tool": tool, "code": exc.code, "message": exc.message},
                )
            raise
        output_hash = tool_output_hash(call.output, call.refs)
        artifact_ids = [ref.id for ref in call.refs]
        record = ToolCallRecord(
            id=new_id("tc"),
            step_id=self.record.id,
            server=call.server,
            tool=call.tool,
            version=call.version,
            input_hash=call.input_hash,
            output_hash=output_hash,
            duration_ms=call.duration_ms,
            artifact_ids=artifact_ids,
        )
        with self.deps.lineage.transaction() as conn:
            steps.insert_tool_call(conn, record)
            self._event(
                conn,
                "tool.called",
                {
                    "server": call.server,
                    "tool": call.tool,
                    "version": call.version,
                    "input_hash": call.input_hash,
                    "output_hash": output_hash,
                    "duration_ms": call.duration_ms,
                    "artifact_ids": artifact_ids,
                },
            )
            for ref in call.refs:
                self._artifact(conn, ref)
        return call

    def call_metadata(self, section_id: str | None = None) -> CallMetadata:
        """The lineage keys a gateway call made inside this step carries."""
        return CallMetadata(run_id=self.run_id, step_id=self.record.id, section_id=section_id)

    def llm(
        self,
        request: CompletionRequest,
        *,
        validate: Callable[[BaseModel], str | None] | None = None,
    ) -> CompletionResult:
        """Complete ``request`` through the gateway, which records it; add its cost to the step."""
        result = self.deps.gateway.complete(request, validate=validate)
        self.spent_usd += result.cost_usd
        return result

    def _record_stored(self, ref: ArtifactRef) -> ArtifactRef:
        with self.deps.lineage.transaction() as conn:
            self._artifact(conn, ref)
        return ref

    def store(
        self, model: BaseModel, *, kind: str, summary: dict[str, Any] | None = None
    ) -> ArtifactRef:
        return self._record_stored(self.deps.store.put_model(model, kind=kind, summary=summary))

    def store_bytes(
        self,
        data: bytes,
        *,
        kind: str,
        media_type: str,
        summary: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        ref = self.deps.store.put(data, kind=kind, media_type=media_type, summary=summary)
        return self._record_stored(ref)

    def event(self, type: str, payload: dict[str, Any]) -> None:
        with self.deps.lineage.transaction() as conn:
            self._event(conn, type, payload)

    def complete(self, update: dict[str, Any]) -> dict[str, Any]:
        """Record the hash of the state after ``update``; return ``update`` unchanged."""
        self.output_state_hash = state_hash(apply_update(self.state, update))
        return update

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self._finish(exc)
        finally:
            self._close_span(exc, status="succeeded" if exc is None else "failed")

    def _finish(self, exc: BaseException | None) -> None:
        with self.deps.lineage.transaction() as conn:
            if exc is None:
                steps.finish_step(
                    conn,
                    self.record.id,
                    status="succeeded",
                    output_state_hash=self.output_state_hash,
                )
                self._event(
                    conn,
                    "step.finished",
                    {"status": "succeeded", "output_state_hash": self.output_state_hash},
                )
                return
            self.last_error = _error_record(self.node, self.record.attempt, exc)
            self._event(conn, "error.recorded", self.last_error.model_dump(mode="json"))
            steps.finish_step(conn, self.record.id, status="failed", output_state_hash=None)
            self._event(conn, "step.finished", {"status": "failed", "output_state_hash": None})
