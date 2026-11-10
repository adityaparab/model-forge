"""The one OpenTelemetry tracer provider for ModelForge, exported over OTLP/HTTP.

The provider lives in this module and is never registered as the global one
(``trace.set_tracer_provider`` can run only once per process), so tests can install
and tear down their own. With no endpoints and no exporter nothing is installed and
``tracer()`` hands out the API's no-op tracer. Spans carry identifiers, names, counts
and costs only: never a prompt, a message, an excerpt, a brief, a passage, an artifact
value or a dataset value.
"""

import json
import secrets
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext, TraceFlags, Tracer

from modelforge_lineage import __version__

__all__ = [
    "INPUT_VALUE",
    "LLM_INPUT_MESSAGES",
    "LLM_INVOCATION_PARAMETERS",
    "LLM_MODEL_NAME",
    "LLM_OUTPUT_MESSAGES",
    "LLM_PROVIDER",
    "LLM_TOKEN_COMPLETION",
    "LLM_TOKEN_PROMPT",
    "LLM_TOKEN_TOTAL",
    "OUTPUT_VALUE",
    "PROJECT_NAME",
    "SPAN_KIND",
    "TOOL_NAME",
    "attr",
    "configure_tracing",
    "new_trace_id",
    "provider",
    "run_parent_context",
    "set_attributes",
    "shutdown_tracing",
    "tracer",
]

# OpenInference semantic-convention attribute names.
SPAN_KIND = "openinference.span.kind"
PROJECT_NAME = "openinference.project.name"
LLM_MODEL_NAME = "llm.model_name"
LLM_PROVIDER = "llm.provider"
LLM_TOKEN_PROMPT = "llm.token_count.prompt"
LLM_TOKEN_COMPLETION = "llm.token_count.completion"
LLM_TOKEN_TOTAL = "llm.token_count.total"
LLM_INVOCATION_PARAMETERS = "llm.invocation_parameters"
LLM_INPUT_MESSAGES = "llm.input_messages"
LLM_OUTPUT_MESSAGES = "llm.output_messages"
INPUT_VALUE = "input.value"
OUTPUT_VALUE = "output.value"
TOOL_NAME = "tool.name"

_TRACER_NAME = "modelforge"
_provider: TracerProvider | None = None


def new_trace_id() -> str:
    """A fresh W3C trace id: 32 lowercase hex characters, never all zeros."""
    return f"{uuid.uuid4().int:032x}"


def configure_tracing(
    service_name: str,
    endpoints: Sequence[str] = (),
    *,
    exporter: SpanExporter | None = None,
) -> bool:
    """Install the module provider; return False and install nothing without endpoints or exporter.

    Each endpoint gets a batching OTLP/HTTP exporter; ``exporter`` (for tests) is
    exported synchronously. A second call shuts the first provider down and replaces it.
    """
    if not endpoints and exporter is None:
        return False
    shutdown_tracing()
    resource = Resource.create(
        {"service.name": service_name, "service.version": __version__, PROJECT_NAME: "modelforge"}
    )
    installed = TracerProvider(resource=resource)
    for url in endpoints:
        installed.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=url)))
    if exporter is not None:
        installed.add_span_processor(SimpleSpanProcessor(exporter))
    global _provider
    _provider = installed
    return True


def shutdown_tracing() -> None:
    """Flush and shut down the module provider, if any, and forget it."""
    global _provider
    if _provider is None:
        return
    _provider.force_flush()
    _provider.shutdown()
    _provider = None


def provider() -> TracerProvider | None:
    return _provider


def tracer() -> Tracer:
    """The module provider's tracer, or the API's no-op tracer when none is installed."""
    if _provider is None:
        return trace.NoOpTracer()
    return _provider.get_tracer(_TRACER_NAME, __version__)


def run_parent_context(trace_id: str) -> Context:
    """A context whose remote, sampled parent span puts new spans in trace ``trace_id``."""
    span_context = SpanContext(
        trace_id=int(trace_id, 16),
        span_id=secrets.randbits(64) or 1,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context))


def set_attributes(span: Span, values: Mapping[str, Any]) -> None:
    """Set ``values`` on ``span``: ``None`` is skipped, dicts are JSON-encoded, the rest pass."""
    for name, value in values.items():
        if value is None:
            continue
        if isinstance(value, Mapping):
            value = json.dumps(value)
        span.set_attribute(name, value)


def attr(name: str) -> str:
    """The ModelForge-namespaced attribute name for ``name``."""
    return "modelforge." + name
