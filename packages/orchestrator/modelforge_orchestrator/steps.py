"""`Step`: the lineage recorder every graph node runs inside.

Each recording operation (entering the step, each tool call's bookkeeping, each stored artifact
or event, and finishing the step) commits in its own transaction, so a failure later in a node
never rolls back what was already recorded. Tool calls themselves run outside any transaction.
"""

from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

from modelforge_core.schemas import StrictModel
from modelforge_lineage import events, steps
from modelforge_lineage.canonical import canonical_json, sha256_hex
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import ArtifactRef, StepRecord, ToolCallRecord
from modelforge_mcp.common import ServerName
from psycopg import Connection
from psycopg.rows import DictRow
from pydantic import BaseModel, ValidationError

from modelforge_orchestrator.briefs import BriefError
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.schemas import ErrorRecord, RunState, apply_update
from modelforge_orchestrator.tools import ToolCall, ToolError

__all__ = ["Step", "state_hash"]


def state_hash(state: RunState) -> str:
    return sha256_hex(canonical_json(state.model_dump(mode="json")))


def _error_record(node: str, attempt: int, exc: BaseException) -> ErrorRecord:
    if isinstance(exc, ToolError):
        kind = "tool"
        error: dict[str, Any] = dict(exc.to_dict())
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
        input_hash = state_hash(self.state)
        with self.deps.lineage.transaction() as conn:
            self.record = steps.start_step(conn, self.run_id, self.node, input_hash)
            self._event(
                conn,
                "step.started",
                {"node": self.node, "attempt": self.record.attempt, "input_state_hash": input_hash},
            )
        return self

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
        if call.refs:
            output_hash = call.refs[0].id
        else:
            output_hash = sha256_hex(canonical_json(call.output.model_dump(mode="json")))
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
