from collections.abc import Sequence
from typing import Any

import pytest
from modelforge_lineage.db import Database
from modelforge_lineage.store import ArtifactStore
from modelforge_lineage.tracing import SPAN_KIND, TOOL_NAME, attr
from modelforge_mcp.servers.knowledge import GetIndexInput
from modelforge_orchestrator.briefs import parse_brief
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.tools import InProcessToolClient, ToolError
from modelforge_orchestrator.worker import run_once
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from .conftest import SYNTHETIC_BRIEF, queued_run

TRACE_ID = "cd" * 16


def _values(span: ReadableSpan) -> list[Any]:
    """Every attribute value on ``span`` and on its events."""
    values = list((span.attributes or {}).values())
    for event in span.events:
        values.extend((event.attributes or {}).values())
    return values


def _named(found: Sequence[ReadableSpan], prefix: str) -> list[ReadableSpan]:
    return [s for s in found if s.name.startswith(prefix)]


@pytest.mark.db
def test_run_to_scope_gate_produces_nested_spans(
    spans: InMemorySpanExporter,
    settings: Settings,
    db: Database,
    store: ArtifactStore,
    checkpointer: object,
) -> None:
    del checkpointer
    run_id = queued_run(db, store, SYNTHETIC_BRIEF)
    with db.transaction() as conn:
        conn.execute("update runs set trace_id = %s where id = %s", (TRACE_ID, run_id))

    run = run_once(settings, db, store, run_id=run_id, tool_client_factory=InProcessToolClient)

    assert run is not None and run.status == "awaiting_scope"
    found = spans.get_finished_spans()
    names = {s.name for s in found}
    assert {"run.execute", "node.intake", "node.scope"} <= names
    assert _named(found, "tool.knowledge.") and _named(found, "tool.doc-renderer.")
    assert all(s.context is not None and f"{s.context.trace_id:032x}" == TRACE_ID for s in found)

    (root,) = _named(found, "run.execute")
    assert root.attributes is not None and root.context is not None
    assert root.attributes[attr("run_id")] == run_id
    assert root.attributes[attr("queue_kind")] == "start"
    assert root.attributes[attr("prior_status")] == "queued"
    assert root.attributes[attr("final_status")] == "awaiting_scope"
    assert root.attributes[attr("profile")] == "fake"
    assert attr("worker_id") in root.attributes
    assert root.attributes[SPAN_KIND] == "AGENT"

    nodes = _named(found, "node.")
    node_ids = {s.context.span_id for s in nodes if s.context is not None}
    for node in nodes:
        assert node.parent is not None and node.parent.span_id == root.context.span_id
        assert node.attributes is not None
        assert node.attributes[SPAN_KIND] == "CHAIN"
        assert node.attributes[attr("status")] == "succeeded"
        assert node.attributes[attr("run_id")] == run_id
        assert node.attributes[attr("attempt")] == 1
        assert attr("input_state_hash") in node.attributes
        assert attr("output_state_hash") in node.attributes
    (scope_span,) = _named(found, "node.scope")
    assert scope_span.attributes is not None
    scope_step = scope_span.attributes[attr("step_id")]

    for tool in _named(found, "tool."):
        assert tool.parent is not None and tool.parent.span_id in node_ids
        assert tool.attributes is not None
        assert tool.attributes[SPAN_KIND] == "TOOL"
        server, name = tool.name.removeprefix("tool.").split(".", 1)
        assert tool.attributes[TOOL_NAME] == f"{server}/{name}"
        assert tool.attributes[attr("server")] == server
        assert tool.attributes[attr("tool")] == name
        assert tool.attributes[attr("run_id")] == run_id
        assert tool.attributes[attr("attempt")] == 1
        for key in ("step_id", "version", "duration_ms", "input_hash", "output_hash"):
            assert attr(key) in tool.attributes, key
    (index_span,) = _named(found, "tool.knowledge.get_index")
    assert index_span.attributes is not None
    assert index_span.attributes[attr("step_id")] == scope_step
    assert list(index_span.attributes[attr("artifact_ids")])  # type: ignore[arg-type]

    llm = _named(found, "llm.scope")
    assert llm
    assert all(s.attributes and s.attributes[attr("step_id")] == scope_step for s in llm)

    model_name = parse_brief(SYNTHETIC_BRIEF.read_bytes()).model_name
    for span in found:
        for value in _values(span):
            assert model_name not in str(value), (span.name, value)


@pytest.mark.db
def test_tool_span_on_failure_has_error_code(
    spans: InMemorySpanExporter, settings: Settings
) -> None:
    with InProcessToolClient(settings, servers=("knowledge",)) as client:
        with pytest.raises(ToolError) as caught:
            client.call("knowledge", "no_such_tool", GetIndexInput())
    assert caught.value.code == "internal"

    (span,) = spans.get_finished_spans()
    assert span.name == "tool.knowledge.no_such_tool"
    assert span.attributes is not None
    assert span.attributes[attr("error_code")] == "internal"
    assert span.attributes[SPAN_KIND] == "TOOL"
    assert attr("step_id") not in span.attributes
    assert span.status.status_code is StatusCode.ERROR
    (event,) = span.events
    assert event.name == "exception"
    assert dict(event.attributes or {}) == {"exception.type": "ToolError"}
