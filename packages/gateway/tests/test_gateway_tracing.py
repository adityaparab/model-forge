"""One ``llm.<purpose>`` span per gateway attempt, with content only when enabled."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from modelforge_gateway.gateway import Gateway
from modelforge_gateway.prompts import PromptRegistry
from modelforge_gateway.schemas import CallMetadata, GatewayError
from modelforge_gateway.settings import GatewaySettings
from modelforge_lineage import runs, steps
from modelforge_lineage.canonical import canonical_json
from modelforge_lineage.db import Database
from modelforge_lineage.facade import Lineage
from modelforge_lineage.store import ArtifactStore
from modelforge_lineage.testing import create_test_run
from modelforge_lineage.tracing import (
    INPUT_VALUE,
    LLM_INVOCATION_PARAMETERS,
    LLM_MODEL_NAME,
    LLM_PROVIDER,
    LLM_TOKEN_COMPLETION,
    LLM_TOKEN_PROMPT,
    LLM_TOKEN_TOTAL,
    OUTPUT_VALUE,
    SPAN_KIND,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import BaseModel
from structlog.testing import capture_logs

from .test_gateway import PRICED, PROMPT, SYSTEM, Outcome, StubModel, request, stub

pytestmark = pytest.mark.db

Build = Callable[..., Gateway]


@pytest.fixture
def build(db: Database, tmp_path: Path) -> Build:
    prompts = tmp_path / "prompts"
    (prompts / "scope").mkdir(parents=True)
    (prompts / "scope" / "system.v1.md").write_text(PROMPT)

    def make(model: StubModel, *, trace_content: bool = False) -> Gateway:
        return Gateway(
            GatewaySettings(prompts_dir=prompts, trace_content=trace_content),
            PRICED,
            Lineage(db),
            ArtifactStore(tmp_path / "artifacts"),
            PromptRegistry(prompts, lineage=None),
            chat_model_factory=lambda route, s, n: model,
        )

    return make


@pytest.fixture
def meta(db: Database) -> CallMetadata:
    with db.transaction() as conn:
        run = create_test_run(conn, "run_t")
        step = steps.start_step(conn, run.id, "scope", "h")
    return CallMetadata(run_id=run.id, step_id=step.id, section_id="sec_1")


def test_llm_span_per_attempt_without_content(
    spans: InMemorySpanExporter, build: Build, meta: CallMetadata
) -> None:
    model = stub(Outcome(parsing_error=ValueError("missing field text")), Outcome())
    with capture_logs() as logs:
        gateway = build(model)
    result = gateway.complete(request(meta))
    assert not [e for e in logs if e["event"] == "gateway.trace_content_enabled"]
    finished = spans.get_finished_spans()
    assert [s.name for s in finished] == ["llm.scope", "llm.scope"]
    first, second = (s.attributes or {} for s in finished)
    assert (first["modelforge.attempt"], second["modelforge.attempt"]) == (1, 2)
    assert first["modelforge.schema_valid"] is False and second["modelforge.schema_valid"] is True
    for attrs in (first, second):
        assert not any(k.startswith(("llm.input_messages", "llm.output_messages")) for k in attrs)
        assert INPUT_VALUE not in attrs and OUTPUT_VALUE not in attrs
        assert "modelforge.error_kind" not in attrs
    assert second[SPAN_KIND] == "LLM"
    assert (second[LLM_PROVIDER], second[LLM_MODEL_NAME]) == ("fake", "scripted-v1")
    assert (second[LLM_TOKEN_PROMPT], second[LLM_TOKEN_COMPLETION]) == (1000, 200)
    assert second[LLM_TOKEN_TOTAL] == 1200
    assert json.loads(str(second[LLM_INVOCATION_PARAMETERS])) == {
        "effort": "low",
        "max_output_tokens": 4096,
        "structured": None,
    }
    assert second["modelforge.run_id"] == meta.run_id
    assert second["modelforge.step_id"] == meta.step_id
    assert second["modelforge.section_id"] == "sec_1"
    assert second["modelforge.purpose"] == "scope"
    assert (second["modelforge.provider"], second["modelforge.model"]) == ("fake", "scripted-v1")
    assert second["modelforge.effort"] == "low"
    assert second["modelforge.prompt_name"] == "scope/system"
    assert second["modelforge.prompt_version"] == 1
    assert second["modelforge.prompt_hash"] == result.prompt_hash
    assert second["modelforge.request_hash"] == result.request_hash
    assert (second["modelforge.input_tokens"], second["modelforge.output_tokens"]) == (1000, 200)
    assert second["modelforge.cache_read_tokens"] == 100
    assert second["modelforge.cache_write_tokens"] == 50
    assert second["modelforge.cost_usd"] == pytest.approx((850 * 4.0 + 200 * 20.0) / 1e6)
    assert isinstance(second["modelforge.latency_ms"], int)
    assert second["modelforge.stop_reason"] == "end_turn"
    assert all(s.status.status_code is not StatusCode.ERROR for s in finished)


def test_llm_span_carries_content_when_enabled(
    spans: InMemorySpanExporter, build: Build, meta: CallMetadata
) -> None:
    with capture_logs() as logs:
        gateway = build(stub(), trace_content=True)
    warned = [e for e in logs if e["event"] == "gateway.trace_content_enabled"]
    assert len(warned) == 1 and warned[0]["log_level"] == "warning"
    req = request(meta)
    result = gateway.complete(req)
    (span,) = spans.get_finished_spans()
    attrs = span.attributes or {}
    assert attrs["llm.input_messages.0.message.role"] == "system"
    assert attrs["llm.input_messages.0.message.content"] == req.system == SYSTEM
    assert attrs["llm.input_messages.1.message.role"] == "user"
    assert attrs["llm.input_messages.1.message.content"] == "Hello."
    assert "llm.input_messages.2.message.role" not in attrs
    assert attrs["llm.output_messages.0.message.content"] == result.text
    assert attrs[OUTPUT_VALUE] == result.text
    assert attrs[INPUT_VALUE] == canonical_json(
        {"system": SYSTEM, "messages": [{"role": "user", "content": "Hello."}]}
    )


def test_llm_span_records_gateway_error(
    spans: InMemorySpanExporter, build: Build, meta: CallMetadata, db: Database
) -> None:
    with db.transaction() as conn:
        runs.set_cost_cap(conn, meta.run_id, 0.000001)
    with pytest.raises(GatewayError):
        build(stub()).complete(request(meta))
    (span,) = spans.get_finished_spans()
    attrs = span.attributes or {}
    assert span.name == "llm.scope"
    assert attrs["modelforge.error_kind"] == "cost_cap"
    assert attrs["modelforge.attempt"] == 1
    assert "modelforge.input_tokens" not in attrs
    assert span.status.status_code is StatusCode.ERROR
    assert [e.name for e in span.events] == ["exception"]


SENTINEL = "SECRET-PROMPT-ECHO-7731"


def _everything(span: ReadableSpan) -> str:
    values = [str(v) for v in (span.attributes or {}).values()]
    for event in span.events:
        values += [event.name, *(str(v) for v in (event.attributes or {}).values())]
    return " ".join(values)


def test_error_event_carries_no_content_when_disabled(
    spans: InMemorySpanExporter, build: Build, meta: CallMetadata
) -> None:
    model = stub(Outcome(raises=RuntimeError(f"400 bad request: {SENTINEL}")))
    with pytest.raises(GatewayError) as exc:
        build(model).complete(request(meta))
    assert exc.value.kind == "provider" and SENTINEL in str(exc.value)
    (span,) = spans.get_finished_spans()
    assert SENTINEL not in _everything(span)
    assert span.status.status_code is StatusCode.ERROR
    (event,) = span.events
    assert event.name == "exception"
    assert dict(event.attributes or {}) == {
        "exception.type": "GatewayError",
        "modelforge.error_kind": "provider",
    }


def test_validate_hook_error_carries_no_content_when_disabled(
    spans: InMemorySpanExporter, build: Build, meta: CallMetadata
) -> None:
    def explode(parsed: BaseModel) -> str | None:
        raise ValueError(SENTINEL)

    with pytest.raises(GatewayError):
        build(stub()).complete(request(meta), validate=explode)
    (span,) = spans.get_finished_spans()
    assert SENTINEL not in _everything(span)
    assert span.attributes and span.attributes["modelforge.error_kind"] == "config"
    (event,) = span.events
    assert dict(event.attributes or {}) == {
        "exception.type": "GatewayError",
        "modelforge.error_kind": "config",
    }


def test_error_event_carries_the_message_when_content_enabled(
    spans: InMemorySpanExporter, build: Build, meta: CallMetadata
) -> None:
    model = stub(Outcome(raises=RuntimeError(f"400 bad request: {SENTINEL}")))
    with capture_logs(), pytest.raises(GatewayError):
        build(model, trace_content=True).complete(request(meta))
    (span,) = spans.get_finished_spans()
    (event,) = span.events
    assert event.name == "exception"
    assert SENTINEL in str((event.attributes or {})["exception.message"])
    assert span.status.status_code is StatusCode.ERROR


def test_non_gateway_error_marks_the_span_and_propagates(
    spans: InMemorySpanExporter,
    build: Build,
    meta: CallMetadata,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = build(stub())

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(gateway, "_record", broken)
    with pytest.raises(RuntimeError, match=SENTINEL):
        gateway.complete(request(meta))
    (span,) = spans.get_finished_spans()
    assert SENTINEL not in _everything(span)
    assert span.status.status_code is StatusCode.ERROR
    assert "modelforge.error_kind" not in (span.attributes or {})
    (event,) = span.events
    assert dict(event.attributes or {}) == {"exception.type": "RuntimeError"}
