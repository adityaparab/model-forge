import re

from modelforge_lineage.tracing import (
    configure_tracing,
    new_trace_id,
    provider,
    run_parent_context,
    set_attributes,
    shutdown_tracing,
    tracer,
)
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_new_trace_id_is_32_hex() -> None:
    trace_id = new_trace_id()
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
    assert trace_id != "0" * 32


def test_configure_without_endpoints_installs_nothing() -> None:
    assert configure_tracing("x") is False
    assert provider() is None
    assert not tracer().start_span("s").is_recording()


def test_spans_fixture_records_a_span(spans: InMemorySpanExporter) -> None:
    with tracer().start_as_current_span("s") as span:
        set_attributes(span, {"a": 1, "b": None, "c": {"k": "v"}, "d": ["x", "y"]})
    (finished,) = spans.get_finished_spans()
    assert dict(finished.attributes or {}) == {"a": 1, "c": '{"k": "v"}', "d": ("x", "y")}
    assert finished.resource.attributes["service.name"] == "test"
    assert finished.resource.attributes["openinference.project.name"] == "modelforge"


def test_run_parent_context_joins_the_trace(spans: InMemorySpanExporter) -> None:
    trace_id = "ab" * 16
    with tracer().start_as_current_span("run.execute", context=run_parent_context(trace_id)) as run:
        with tracer().start_as_current_span("node"):
            pass
    node, run_span = spans.get_finished_spans()
    assert node.context is not None and run_span.context is not None
    assert trace.format_trace_id(node.context.trace_id) == trace_id
    assert trace.format_trace_id(run_span.context.trace_id) == trace_id
    assert node.parent is not None and node.parent.span_id == run.get_span_context().span_id
    assert run_span.parent is not None and run_span.parent.is_remote


def test_configure_with_endpoint_builds_http_exporter() -> None:
    try:
        assert configure_tracing("x", ["http://localhost:4318/v1/traces"]) is True
        installed = provider()
        assert installed is not None
        assert len(installed._active_span_processor._span_processors) == 1  # pyright: ignore[reportPrivateUsage]
    finally:
        shutdown_tracing()
    assert provider() is None


def test_second_configure_replaces_the_provider() -> None:
    try:
        configure_tracing("a", exporter=InMemorySpanExporter())
        first = provider()
        configure_tracing("b", exporter=InMemorySpanExporter())
        assert provider() is not first
    finally:
        shutdown_tracing()
