"""The governed completion pipeline, driven by a scripted stub chat model."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from modelforge_gateway.gateway import Gateway
from modelforge_gateway.pricing import project_cost_usd
from modelforge_gateway.profiles import Price, Profile, Route, load_profile
from modelforge_gateway.prompts import PromptRegistry
from modelforge_gateway.schemas import (
    CallMetadata,
    CompletionRequest,
    GatewayError,
    LlmResponse,
    Message,
    RedactionContext,
)
from modelforge_gateway.settings import GatewaySettings
from modelforge_lineage import events, llm_calls, runs, steps
from modelforge_lineage.db import Database
from modelforge_lineage.facade import Lineage
from modelforge_lineage.store import ArtifactStore
from modelforge_lineage.testing import create_test_run
from pydantic import BaseModel

pytestmark = pytest.mark.db

PROMPT = "---\npurpose: scope\nversion: 1\noutput_schema: Answer\ndescription: d\n---\nBe brief."
SYSTEM = "You answer briefly."
USAGE: dict[str, Any] = {
    "input_tokens": 1000,
    "output_tokens": 200,
    "total_tokens": 1200,
    "input_token_details": {"cache_read": 100, "cache_creation": 50},
}
FAKE = Route(provider="fake", model="scripted-v1", effort="low")
PRICED = Profile(
    name="t",
    routes={p: FAKE for p in ("scope", "document", "challenge", "remediate", "judge")},
    prices={"scripted-v1": Price(input=4.0, output=20.0)},
)


class Answer(BaseModel):
    text: str


@dataclass
class Outcome:
    parsed: BaseModel | None = field(default_factory=lambda: Answer(text="ok"))
    parsing_error: Exception | None = None
    stop_reason: str | None = "end_turn"
    usage: dict[str, Any] | None = field(default_factory=lambda: dict(USAGE))
    raises: Exception | None = None


class StubModel(BaseChatModel):
    """Pops the next scripted outcome per call (the last one repeats); records inputs."""

    outcomes: list[Any] = []
    calls: list[list[BaseMessage]] = []
    structured_kwargs: list[dict[str, Any]] = []

    @property
    def _llm_type(self) -> str:
        return "stub"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError

    def with_structured_output(
        self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        self.structured_kwargs.append({"include_raw": include_raw, **kwargs})

        def respond(model_input: LanguageModelInput) -> Any:
            self.calls.append(list(self._convert_input(model_input).to_messages()))
            outcome: Outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
            if outcome.raises is not None:
                raise outcome.raises
            parsed = None if outcome.parsing_error is not None else outcome.parsed
            raw = AIMessage(
                content=parsed.model_dump_json() if parsed is not None else "",
                usage_metadata=outcome.usage,  # pyright: ignore[reportArgumentType]
                response_metadata={"stop_reason": outcome.stop_reason},
            )
            return {"raw": raw, "parsed": parsed, "parsing_error": outcome.parsing_error}

        return RunnableLambda(respond)


def stub(*outcomes: Outcome) -> StubModel:
    return StubModel(outcomes=list(outcomes) or [Outcome()], calls=[], structured_kwargs=[])


GatewayFor = Callable[..., Gateway]


@pytest.fixture
def gateway_for(db: Database, tmp_path: Path) -> GatewayFor:
    prompts = tmp_path / "prompts"
    (prompts / "scope").mkdir(parents=True)
    (prompts / "scope" / "system.v1.md").write_text(PROMPT)

    def build(model: StubModel, profile: Profile = PRICED) -> Gateway:
        return Gateway(
            GatewaySettings(prompts_dir=prompts),
            profile,
            Lineage(db),
            ArtifactStore(tmp_path / "artifacts"),
            PromptRegistry(prompts, lineage=None),
            chat_model_factory=lambda route, s, n: model,
        )

    return build


@pytest.fixture
def metadata(db: Database) -> CallMetadata:
    with db.transaction() as conn:
        run = create_test_run(conn, "run_g")
        step = steps.start_step(conn, run.id, "scope", "h")
    return CallMetadata(run_id=run.id, step_id=step.id)


def request(metadata: CallMetadata, text: str = "Hello.", **kwargs: Any) -> CompletionRequest:
    return CompletionRequest(
        purpose="scope",
        prompt_name="scope/system",
        prompt_version=1,
        system=SYSTEM,
        messages=[Message(role="user", content=text)],
        output_schema=Answer,
        metadata=metadata,
        **kwargs,
    )


def event_types(db: Database, run_id: str) -> list[str]:
    with db.connection() as conn:
        return [e.type for e in events.list_events(conn, run_id)]


def failed_payload(db: Database, run_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        failed = [e for e in events.list_events(conn, run_id) if e.type == "llm.failed"]
    assert len(failed) == 1
    return failed[0].payload


def test_complete_records_row_artifact_cost_and_event(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database, tmp_path: Path
) -> None:
    gateway = gateway_for(stub())
    result = gateway.complete(request(metadata))
    assert result.parsed == Answer(text="ok")
    assert result.schema_valid and result.attempts == 1
    assert (result.provider, result.model) == ("fake", "scripted-v1")
    assert result.text == '{"text":"ok"}'
    assert result.usage.input_tokens == 1000 and result.usage.cache_read_tokens == 100
    assert result.usage.cache_write_tokens == 50
    assert result.stop_reason == "end_turn"
    assert result.prompt_hash and len(result.request_hash) == 64
    # 850 uncached input at 4, 100 cache reads and 50 cache writes free, 200 output at 20.
    assert result.cost_usd == pytest.approx((850 * 4.0 + 200 * 20.0) / 1e6)
    with db.connection() as conn:
        rows = llm_calls.list_llm_calls(conn, metadata.run_id)
        run = runs.get_run(conn, metadata.run_id)
        artifacts = steps.list_artifacts(conn, metadata.run_id)
        called = [e for e in events.list_events(conn, metadata.run_id) if e.type == "llm.called"]
    assert len(rows) == 1
    row = rows[0]
    assert row.schema_valid and row.step_id == metadata.step_id and row.purpose == "scope"
    assert row.raw_ref == result.raw_ref.id and row.input_tokens == 1000
    assert row.cost_usd == pytest.approx(result.cost_usd)
    assert run.cost_usd == pytest.approx(result.cost_usd) and run.cost_usd > 0
    assert [a.id for a in artifacts] == [result.raw_ref.id]
    assert artifacts[0].kind == "llm_response"
    body = ArtifactStore(tmp_path / "artifacts").get_model(result.raw_ref.id, LlmResponse)
    assert body.attempt == 1 and body.parsed == {"text": "ok"}
    assert body.request_hash == result.request_hash
    assert result.raw_ref.producer is not None
    assert result.raw_ref.producer.server == "gateway" and result.raw_ref.producer.tool == "scope"
    assert len(called) == 1 and called[0].step_id == metadata.step_id
    assert called[0].payload["attempt"] == 1
    assert called[0].payload["raw_ref"] == result.raw_ref.id
    assert "id" not in called[0].payload


def test_complete_retries_twice_then_schema_error(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    model = stub(Outcome(parsing_error=ValueError("missing field text")))
    with pytest.raises(GatewayError) as exc:
        gateway_for(model).complete(request(metadata))
    assert exc.value.kind == "schema"
    with db.connection() as conn:
        rows = llm_calls.list_llm_calls(conn, metadata.run_id)
    assert [r.schema_valid for r in rows] == [False, False, False]
    assert len(model.calls) == 3
    retry = model.calls[2][-1]
    assert isinstance(retry, HumanMessage)
    assert retry.content == (
        "Your previous answer failed validation: missing field text. "
        "Return only the corrected object."
    )
    assert len(model.calls[1]) == 3 and len(model.calls[2]) == 4
    assert event_types(db, metadata.run_id).count("llm.called") == 3
    payload = failed_payload(db, metadata.run_id)
    assert payload["kind"] == "schema" and payload["purpose"] == "scope"
    assert payload["provider"] == "fake" and payload["model"] == "scripted-v1"


def test_complete_validate_hook_retries_like_schema(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    verdicts = iter(["bad", None])
    model = stub()
    result = gateway_for(model).complete(request(metadata), validate=lambda parsed: next(verdicts))
    assert result.attempts == 2 and result.schema_valid
    single = (850 * 4.0 + 200 * 20.0) / 1e6
    assert result.cost_usd == pytest.approx(2 * single)
    assert result.usage.input_tokens == 2000
    retry = model.calls[1][-1]
    assert isinstance(retry, HumanMessage) and "failed validation: bad." in str(retry.content)
    with db.connection() as conn:
        rows = llm_calls.list_llm_calls(conn, metadata.run_id)
        assert runs.get_run(conn, metadata.run_id).cost_usd == pytest.approx(2 * single)
    assert [r.schema_valid for r in rows] == [False, True]
    assert result.raw_ref.id == rows[1].raw_ref


def test_validate_hook_exception_is_recorded_then_config_error(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database, tmp_path: Path
) -> None:
    def explode(parsed: BaseModel) -> str | None:
        raise RuntimeError("hook broke")

    model = stub()
    with pytest.raises(GatewayError) as exc:
        gateway_for(model).complete(request(metadata), validate=explode)
    assert exc.value.kind == "config"
    assert exc.value.message == "validate hook raised RuntimeError: hook broke"
    assert isinstance(exc.value.__cause__, RuntimeError)
    assert len(model.calls) == 1
    with db.connection() as conn:
        rows = llm_calls.list_llm_calls(conn, metadata.run_id)
        run = runs.get_run(conn, metadata.run_id)
        artifacts = steps.list_artifacts(conn, metadata.run_id)
    assert len(rows) == 1 and rows[0].schema_valid is False
    assert run.cost_usd == pytest.approx((850 * 4.0 + 200 * 20.0) / 1e6)
    assert rows[0].raw_ref is not None and [a.id for a in artifacts] == [rows[0].raw_ref]
    assert ArtifactStore(tmp_path / "artifacts").exists(rows[0].raw_ref)
    assert event_types(db, metadata.run_id).count("llm.called") == 1
    assert failed_payload(db, metadata.run_id)["kind"] == "config"


def test_complete_maps_refusal(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    model = stub(Outcome(parsed=None, stop_reason="refusal"))
    with pytest.raises(GatewayError) as exc:
        gateway_for(model).complete(request(metadata))
    assert exc.value.kind == "refusal"
    assert len(model.calls) == 1
    with db.connection() as conn:
        rows = llm_calls.list_llm_calls(conn, metadata.run_id)
    assert len(rows) == 1 and rows[0].stop_reason == "refusal" and not rows[0].schema_valid
    assert failed_payload(db, metadata.run_id)["kind"] == "refusal"


def test_complete_maps_a_refusal_exception(gateway_for: GatewayFor, metadata: CallMetadata) -> None:
    model = stub(Outcome(raises=RuntimeError("stop_reason was refusal")))
    with pytest.raises(GatewayError) as exc:
        gateway_for(model).complete(request(metadata))
    assert exc.value.kind == "refusal"


def test_complete_refuses_on_redaction_hit_and_sends_nothing(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    model = stub()
    req = request(
        metadata,
        "Tell me about Project Falcon.",
        redaction=RedactionContext(restricted_terms=["Project Falcon"]),
    )
    with pytest.raises(GatewayError) as exc:
        gateway_for(model).complete(req)
    assert exc.value.kind == "redaction"
    assert "restricted_term" in exc.value.message and "user" in exc.value.message
    assert "Falcon" not in str(exc.value)
    assert model.calls == []
    with db.connection() as conn:
        assert llm_calls.list_llm_calls(conn, metadata.run_id) == []
    payload = failed_payload(db, metadata.run_id)
    assert payload["kind"] == "redaction" and "Falcon" not in str(payload)


def test_complete_parks_on_cost_cap_before_sending(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    with db.transaction() as conn:
        runs.set_cost_cap(conn, metadata.run_id, 0.000001)
    model = stub()
    with pytest.raises(GatewayError) as exc:
        gateway_for(model).complete(request(metadata))
    assert exc.value.kind == "cost_cap"
    assert exc.value.message.startswith("spent 0.0000 USD plus projected ")
    assert exc.value.message.endswith("exceeds the cap of 0.00 USD")
    assert model.calls == []
    assert failed_payload(db, metadata.run_id)["kind"] == "cost_cap"


def test_cost_cap_is_checked_before_every_attempt(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    req = request(metadata)
    chars = len(req.system) + sum(len(m.content) for m in req.messages)
    projected = project_cost_usd(chars, req.max_output_tokens, PRICED.prices["scripted-v1"])
    first = (850 * 4.0 + 200 * 20.0) / 1e6
    # The first attempt fits under the cap; its recorded cost pushes the retry over it.
    with db.transaction() as conn:
        runs.set_cost_cap(conn, metadata.run_id, projected + first / 2)
    model = stub(Outcome(parsing_error=ValueError("missing field text")))
    with pytest.raises(GatewayError) as exc:
        gateway_for(model).complete(req)
    assert exc.value.kind == "cost_cap"
    assert len(model.calls) == 1
    with db.connection() as conn:
        rows = llm_calls.list_llm_calls(conn, metadata.run_id)
        run = runs.get_run(conn, metadata.run_id)
    assert [r.schema_valid for r in rows] == [False]
    assert rows[0].cost_usd == pytest.approx(first) and run.cost_usd == pytest.approx(first)
    assert failed_payload(db, metadata.run_id)["kind"] == "cost_cap"


def test_retry_message_is_scanned_before_it_is_sent(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    model = stub(Outcome(parsing_error=ValueError("field text got 123456789012")))
    with pytest.raises(GatewayError) as exc:
        gateway_for(model).complete(request(metadata))
    assert exc.value.kind == "redaction"
    assert "digit_run" in exc.value.message and "retry" in exc.value.message
    assert "123456789012" not in str(exc.value)
    assert len(model.calls) == 1
    with db.connection() as conn:
        rows = llm_calls.list_llm_calls(conn, metadata.run_id)
    assert [r.schema_valid for r in rows] == [False]
    payload = failed_payload(db, metadata.run_id)
    assert payload["kind"] == "redaction" and "123456789012" not in str(payload)


def test_route_structured_method_reaches_the_provider(
    gateway_for: GatewayFor, metadata: CallMetadata
) -> None:
    def profile(route: Route) -> Profile:
        purposes = ("scope", "document", "challenge", "remediate", "judge")
        return Profile(name="t", routes=dict.fromkeys(purposes, route))

    forced = Route(provider="anthropic", model="m", structured="function_calling")
    model = stub()
    gateway_for(model, profile(forced)).complete(request(metadata))
    default = stub()
    gateway_for(default, profile(Route(provider="anthropic", model="m"))).complete(
        request(metadata)
    )
    assert model.structured_kwargs == [{"include_raw": True, "method": "function_calling"}]
    assert default.structured_kwargs == [{"include_raw": True, "method": "json_schema"}]


def test_missing_usage_metadata_records_zero_usage(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    result = gateway_for(stub(Outcome(usage=None))).complete(request(metadata))
    assert result.usage.input_tokens == 0 and result.cost_usd == 0.0
    assert result.stop_reason == "usage_missing"
    with db.connection() as conn:
        row = llm_calls.list_llm_calls(conn, metadata.run_id)[0]
    assert row.input_tokens == 0 and row.output_tokens == 0
    assert row.stop_reason == "usage_missing"


def test_provider_exception_is_provider_error(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    with pytest.raises(GatewayError) as exc:
        gateway_for(stub(Outcome(raises=RuntimeError("boom")))).complete(request(metadata))
    assert exc.value.kind == "provider"
    assert exc.value.message == "RuntimeError: boom"
    with db.connection() as conn:
        assert llm_calls.list_llm_calls(conn, metadata.run_id) == []
    assert failed_payload(db, metadata.run_id)["message"] == "RuntimeError: boom"


def test_unknown_prompt_is_config_and_recorded(
    gateway_for: GatewayFor, metadata: CallMetadata, db: Database
) -> None:
    req = request(metadata).model_copy(update={"prompt_version": 9})
    with pytest.raises(GatewayError) as exc:
        gateway_for(stub()).complete(req)
    assert exc.value.kind == "config"
    assert failed_payload(db, metadata.run_id)["kind"] == "config"


def test_anthropic_route_marks_system_block_cacheable(
    gateway_for: GatewayFor, metadata: CallMetadata, repo_profiles_dir: Path
) -> None:
    model = stub()
    gateway = gateway_for(model, load_profile(repo_profiles_dir, "default"))
    result = gateway.complete(request(metadata))
    assert (result.provider, result.model) == ("anthropic", "claude-opus-5-5")
    system = model.calls[0][0]
    assert isinstance(system, SystemMessage)
    assert system.content == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    gateway.complete(request(metadata, cache_prefix=False))
    plain = model.calls[1][0]
    assert isinstance(plain, SystemMessage) and plain.content == SYSTEM


def test_fake_route_never_marks_the_system_block(
    gateway_for: GatewayFor, metadata: CallMetadata
) -> None:
    model = stub()
    gateway_for(model).complete(request(metadata))
    assert model.calls[0][0].content == SYSTEM
    assert isinstance(model.calls[0][1], HumanMessage)


def test_route_applies_local_model_and_effort_overrides(
    repo_profiles_dir: Path, db: Database, tmp_path: Path
) -> None:
    settings = GatewaySettings(
        profiles_dir=repo_profiles_dir, prompts_dir=tmp_path, ollama_model="qwen3:8b"
    )
    lineage = Lineage(db)
    gateway = Gateway.for_run(settings, "local-ollama", lineage, ArtifactStore(tmp_path))
    assert gateway.profile.name == "local-ollama" and gateway.settings is settings
    assert gateway.registry.lineage is lineage
    route = gateway.route("document", effort="high")
    assert route == Route(provider="ollama", model="qwen3:8b", effort="high")
    assert gateway.route("document").effort == "medium"
    default = Gateway.for_run(settings, "default", lineage, ArtifactStore(tmp_path))
    assert default.route("document").model == "claude-opus-5-5"
    assert default.route("scope", effort="high").effort == "high"
