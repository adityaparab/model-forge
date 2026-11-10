"""The model gateway: one entry point for every LLM call.

``Gateway.complete`` routes the request, records the prompt, scans for sensitive text,
invokes the provider with bounded schema retries (checking the run's cost cap before every
attempt and scanning every retry message) and writes every attempt to lineage (spec 3.2).
"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from modelforge_lineage import events, llm_calls, runs, steps
from modelforge_lineage.canonical import canonical_json, sha256_hex
from modelforge_lineage.facade import Lineage
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import ArtifactRef, LlmCallRecord, Producer
from modelforge_lineage.observability import get_logger
from modelforge_lineage.store import ArtifactStore
from modelforge_lineage.tracing import (
    INPUT_VALUE,
    LLM_INPUT_MESSAGES,
    LLM_INVOCATION_PARAMETERS,
    LLM_MODEL_NAME,
    LLM_OUTPUT_MESSAGES,
    LLM_PROVIDER,
    LLM_TOKEN_COMPLETION,
    LLM_TOKEN_PROMPT,
    LLM_TOKEN_TOTAL,
    OUTPUT_VALUE,
    SPAN_KIND,
    attr,
    set_attributes,
    tracer,
)
from opentelemetry.trace import Span, Status, StatusCode
from pydantic import BaseModel

from modelforge_gateway.pricing import cost_usd, project_cost_usd
from modelforge_gateway.profiles import Profile, Route, load_profile
from modelforge_gateway.prompts import PromptRecord, PromptRegistry
from modelforge_gateway.providers import ChatModelFactory, build_chat_model, structured
from modelforge_gateway.redaction import scan, scan_messages
from modelforge_gateway.schemas import (
    CompletionRequest,
    CompletionResult,
    Effort,
    GatewayError,
    LlmResponse,
    Purpose,
    Usage,
)
from modelforge_gateway.settings import GatewaySettings

GATEWAY_VERSION = "1.0.0"
MAX_ATTEMPTS = 3  # the first attempt plus two schema retries
RETRY_MESSAGE = "Your previous answer failed validation: {error}. Return only the corrected object."

Validator = Callable[[BaseModel], str | None]


def check_credentials(profile: Profile, settings: GatewaySettings) -> None:
    """Fail fast when ``profile`` routes to a provider whose credential is missing."""
    if not settings.anthropic_api_key and any(
        route.provider == "anthropic" for route in profile.routes.values()
    ):
        raise GatewayError(
            "config",
            f"profile '{profile.name}' routes to anthropic but ANTHROPIC_API_KEY is not set",
        )


@dataclass(frozen=True)
class _Attempt:
    """What one invocation produced, after usage and stop reason are normalised."""

    number: int
    text: str | None
    parsed: BaseModel | None
    error: str | None
    usage: Usage
    stop_reason: str | None
    latency_ms: int
    hook_error: Exception | None = None  # the caller's validate hook raised


class Gateway:
    def __init__(
        self,
        settings: GatewaySettings,
        profile: Profile,
        lineage: Lineage,
        store: ArtifactStore,
        registry: PromptRegistry,
        *,
        chat_model_factory: ChatModelFactory = build_chat_model,
    ) -> None:
        self.settings = settings
        self.profile = profile
        self.registry = registry
        self._lineage = lineage
        self._store = store
        self._chat_model_factory = chat_model_factory
        if settings.trace_content:
            get_logger(component="gateway").warning("gateway.trace_content_enabled")

    @classmethod
    def for_run(
        cls,
        settings: GatewaySettings,
        profile_name: str,
        lineage: Lineage,
        store: ArtifactStore,
        *,
        chat_model_factory: ChatModelFactory = build_chat_model,
    ) -> "Gateway":
        profile = load_profile(settings.profiles_dir, profile_name)
        registry = PromptRegistry(settings.prompts_dir, lineage)
        return cls(
            settings, profile, lineage, store, registry, chat_model_factory=chat_model_factory
        )

    def route(self, purpose: Purpose, effort: Effort | None = None) -> Route:
        """The profile's route for ``purpose`` with the local model and effort overrides."""
        route = self.profile.routes.get(purpose)
        if route is None:
            raise GatewayError(
                "config", f"profile '{self.profile.name}' has no route for {purpose!r}"
            )
        update: dict[str, Any] = {}
        if route.provider == "ollama" and self.settings.ollama_model:
            update["model"] = self.settings.ollama_model
        if route.provider == "lmstudio" and self.settings.lmstudio_model:
            update["model"] = self.settings.lmstudio_model
        if effort is not None:
            update["effort"] = effort
        return route.model_copy(update=update)

    def complete(
        self, request: CompletionRequest, *, validate: Validator | None = None
    ) -> CompletionResult:
        route = self.route(request.purpose, request.effort)
        try:
            return self._complete(request, route, validate)
        except GatewayError as exc:
            exc.purpose = exc.purpose or request.purpose
            exc.provider = exc.provider or route.provider
            exc.model = exc.model or route.model
            with self._lineage.transaction() as conn:
                events.append_event(
                    conn,
                    request.metadata.run_id,
                    "llm.failed",
                    exc.to_dict(),
                    step_id=request.metadata.step_id,
                )
            raise

    def _complete(
        self, request: CompletionRequest, route: Route, validate: Validator | None
    ) -> CompletionResult:
        prompt = self.registry.get(request.prompt_name, request.prompt_version)
        messages = self._messages(request, route)
        hit = scan_messages(request.system, request.messages, request.redaction.restricted_terms)
        if hit is not None:
            raise GatewayError("redaction", f"{hit.pattern} in {hit.role} message")
        request_hash = sha256_hex(
            canonical_json(
                {
                    "system": request.system,
                    "messages": [m.model_dump() for m in request.messages],
                    "schema": request.output_schema.model_json_schema(),
                    "model": route.model,
                    "effort": route.effort,
                }
            )
        )
        chat_model = self._chat_model_factory(route, self.settings, request.max_output_tokens)
        runnable = structured(
            chat_model, route.provider, request.output_schema, method=route.structured
        )
        price = self.profile.prices.get(route.model)

        total_usage = Usage()
        total_cost = 0.0
        last_error: str | None = None
        retry_chars = 0
        for number in range(1, MAX_ATTEMPTS + 1):
            with _llm_span(request.purpose) as span:
                set_attributes(span, _identity(request, route, prompt, request_hash, number))
                if self.settings.trace_content:
                    set_attributes(span, _input_content(request))
                # `_record` has already added the previous attempts' cost to the run.
                self._check_cost_cap(request, route, retry_chars)
                attempt = self._invoke(runnable, messages, number, validate)
                attempt_cost = cost_usd(attempt.usage, price)
                ref = self._record(request, route, prompt, request_hash, attempt, attempt_cost)
                set_attributes(span, _outcome(attempt, attempt_cost))
                if self.settings.trace_content:
                    set_attributes(span, _output_content(attempt))
                total_usage = _add_usage(total_usage, attempt.usage)
                total_cost += attempt_cost
                if attempt.hook_error is not None:
                    raise GatewayError("config", attempt.error or "validate hook raised") from (
                        attempt.hook_error
                    )
                if attempt.stop_reason == "refusal":
                    raise GatewayError(
                        "refusal", f"the model refused the {request.purpose} request"
                    )
                if attempt.error is None and attempt.parsed is not None:
                    return CompletionResult(
                        text=attempt.text,
                        parsed=attempt.parsed,
                        provider=route.provider,
                        model=route.model,
                        prompt_name=prompt.name,
                        prompt_version=prompt.version,
                        prompt_hash=prompt.hash,
                        request_hash=request_hash,
                        usage=total_usage,
                        cost_usd=total_cost,
                        latency_ms=attempt.latency_ms,
                        stop_reason=attempt.stop_reason,
                        schema_valid=True,
                        attempts=number,
                        raw_ref=ref,
                    )
                last_error = attempt.error
                retry = RETRY_MESSAGE.format(error=last_error)
                pattern = scan(retry, request.redaction.restricted_terms)
                if pattern is not None:
                    raise GatewayError("redaction", f"{pattern} in retry message")
                retry_chars += len(retry)
                messages = [*messages, HumanMessage(retry)]
        raise GatewayError(
            "schema",
            f"no valid {request.output_schema.__name__} after {MAX_ATTEMPTS} attempts: "
            f"{last_error}",
        )

    def _messages(self, request: CompletionRequest, route: Route) -> list[BaseMessage]:
        if request.cache_prefix and route.provider == "anthropic":
            block = {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            system = SystemMessage(content=[block])
        else:
            system = SystemMessage(content=request.system)
        return [
            system,
            *(
                SystemMessage(content=m.content) if m.role == "system" else HumanMessage(m.content)
                for m in request.messages
            ),
        ]

    def _check_cost_cap(self, request: CompletionRequest, route: Route, retry_chars: int) -> None:
        """Raise ``cost_cap`` when the run's spend plus this attempt's worst case exceeds it."""
        with self._lineage.connection() as conn:
            run = runs.get_run(conn, request.metadata.run_id)
        text_chars = (
            len(request.system) + sum(len(m.content) for m in request.messages) + retry_chars
        )
        projected = project_cost_usd(
            text_chars, request.max_output_tokens, self.profile.prices.get(route.model)
        )
        if run.cost_usd + projected > run.cost_cap_usd:
            raise GatewayError(
                "cost_cap",
                f"spent {run.cost_usd:.4f} USD plus projected {projected:.4f} USD"
                f" exceeds the cap of {run.cost_cap_usd:.2f} USD",
            )

    def _invoke(
        self,
        runnable: Runnable[LanguageModelInput, dict[str, Any] | BaseModel],
        messages: list[BaseMessage],
        number: int,
        validate: Validator | None,
    ) -> _Attempt:
        started = time.perf_counter()
        try:
            out = runnable.invoke(messages)
        except GatewayError:
            raise
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if "refusal" in type(exc).__name__.lower() or "refusal" in str(exc).lower():
                raise GatewayError("refusal", detail) from exc
            raise GatewayError("provider", detail) from exc
        latency_ms = round((time.perf_counter() - started) * 1000)
        if not isinstance(out, dict) or not isinstance(out.get("raw"), AIMessage):
            raise GatewayError("provider", "the structured runnable returned no raw message")
        raw: AIMessage = out["raw"]
        stop_reason = raw.response_metadata.get("stop_reason")
        usage = _usage(raw)
        if usage is None and stop_reason != "refusal":
            stop_reason = "usage_missing"
        parsed = out.get("parsed")
        parsing_error = out.get("parsing_error")
        error = str(parsing_error) if parsing_error is not None else None
        if not isinstance(parsed, BaseModel):
            parsed = None
            error = error or "the model returned no structured object"
        hook_error: Exception | None = None
        if parsed is not None and error is None and validate is not None:
            try:
                error = validate(parsed)
            except Exception as exc:  # recorded as an invalid attempt, then raised
                hook_error = exc
                error = f"validate hook raised {type(exc).__name__}: {exc}"
        return _Attempt(
            number=number,
            text=str(raw.text) or None,
            parsed=parsed,
            error=error,
            usage=usage or Usage(),
            stop_reason=stop_reason,
            latency_ms=latency_ms,
            hook_error=hook_error,
        )

    def _record(
        self,
        request: CompletionRequest,
        route: Route,
        prompt: PromptRecord,
        request_hash: str,
        attempt: _Attempt,
        attempt_cost: float,
    ) -> ArtifactRef:
        """Write one attempt's raw artifact, ``llm_calls`` row, cost and event."""
        metadata = request.metadata
        body = LlmResponse(
            provider=route.provider,
            model=route.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_hash=prompt.hash,
            request_hash=request_hash,
            attempt=attempt.number,
            text=attempt.text,
            parsed=attempt.parsed.model_dump(mode="json") if attempt.parsed is not None else None,
            usage=attempt.usage,
            stop_reason=attempt.stop_reason,
        )
        ref = self._store.put_model(
            body,
            kind="llm_response",
            producer=Producer(server="gateway", tool=request.purpose, version=GATEWAY_VERSION),
        )
        record = LlmCallRecord(
            id=new_id("llm"),
            step_id=metadata.step_id,
            purpose=request.purpose,
            provider=route.provider,
            model=route.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_hash=prompt.hash,
            input_tokens=attempt.usage.input_tokens,
            output_tokens=attempt.usage.output_tokens,
            cache_read_tokens=attempt.usage.cache_read_tokens,
            cache_write_tokens=attempt.usage.cache_write_tokens,
            cost_usd=attempt_cost,
            latency_ms=attempt.latency_ms,
            stop_reason=attempt.stop_reason,
            schema_valid=attempt.error is None and attempt.stop_reason != "refusal",
            raw_ref=ref.id,
        )
        with self._lineage.transaction() as conn:
            steps.insert_artifact(
                conn,
                run_id=metadata.run_id,
                ref=ref,
                uri=self._store.uri(ref.id),
                producer_step_id=metadata.step_id,
            )
            llm_calls.insert_llm_call(conn, record)
            runs.add_cost(conn, metadata.run_id, attempt_cost)
            events.append_event(
                conn,
                metadata.run_id,
                "llm.called",
                {**record.model_dump(exclude={"id"}), "attempt": attempt.number},
                step_id=metadata.step_id,
            )
        return ref


@contextmanager
def _llm_span(purpose: str) -> Iterator[Span]:
    """One ``llm.<purpose>`` span; a ``GatewayError`` marks it failed and propagates."""
    with tracer().start_as_current_span(
        f"llm.{purpose}", record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except GatewayError as exc:
            span.set_attribute(attr("error_kind"), exc.kind)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            raise


def _identity(
    request: CompletionRequest, route: Route, prompt: PromptRecord, request_hash: str, number: int
) -> dict[str, Any]:
    """The attempt's identifying attributes: ids, names, hashes and invocation parameters."""
    metadata = request.metadata
    return {
        SPAN_KIND: "LLM",
        LLM_MODEL_NAME: route.model,
        LLM_PROVIDER: route.provider,
        LLM_INVOCATION_PARAMETERS: {
            "effort": route.effort,
            "max_output_tokens": request.max_output_tokens,
            "structured": route.structured,
        },
        attr("run_id"): metadata.run_id,
        attr("step_id"): metadata.step_id,
        attr("section_id"): metadata.section_id,
        attr("purpose"): request.purpose,
        attr("provider"): route.provider,
        attr("model"): route.model,
        attr("effort"): route.effort,
        attr("prompt_name"): prompt.name,
        attr("prompt_version"): prompt.version,
        attr("prompt_hash"): prompt.hash,
        attr("request_hash"): request_hash,
        attr("attempt"): number,
    }


def _outcome(attempt: _Attempt, attempt_cost: float) -> dict[str, Any]:
    """The attempt's usage, cost, latency, stop reason and validity (as its ``llm_calls`` row)."""
    usage = attempt.usage
    return {
        LLM_TOKEN_PROMPT: usage.input_tokens,
        LLM_TOKEN_COMPLETION: usage.output_tokens,
        LLM_TOKEN_TOTAL: usage.input_tokens + usage.output_tokens,
        attr("input_tokens"): usage.input_tokens,
        attr("output_tokens"): usage.output_tokens,
        attr("cache_read_tokens"): usage.cache_read_tokens,
        attr("cache_write_tokens"): usage.cache_write_tokens,
        attr("cost_usd"): attempt_cost,
        attr("latency_ms"): attempt.latency_ms,
        attr("stop_reason"): attempt.stop_reason,
        attr("schema_valid"): attempt.error is None and attempt.stop_reason != "refusal",
    }


def _input_content(request: CompletionRequest) -> dict[str, Any]:
    """The system block and request messages; only with ``MODELFORGE_TRACE_CONTENT=1``."""
    pairs = [("system", request.system), *((m.role, m.content) for m in request.messages)]
    values: dict[str, Any] = {}
    for i, (role, content) in enumerate(pairs):
        values[f"{LLM_INPUT_MESSAGES}.{i}.message.role"] = role
        values[f"{LLM_INPUT_MESSAGES}.{i}.message.content"] = content
    values[INPUT_VALUE] = canonical_json(
        {"system": request.system, "messages": [m.model_dump() for m in request.messages]}
    )
    return values


def _output_content(attempt: _Attempt) -> dict[str, Any]:
    """The attempt's text; only with ``MODELFORGE_TRACE_CONTENT=1``."""
    return {f"{LLM_OUTPUT_MESSAGES}.0.message.content": attempt.text, OUTPUT_VALUE: attempt.text}


def _usage(raw: AIMessage) -> Usage | None:
    metadata = raw.usage_metadata
    if metadata is None:
        return None
    details: dict[str, Any] = dict(metadata.get("input_token_details") or {})
    return Usage(
        input_tokens=metadata.get("input_tokens") or 0,
        output_tokens=metadata.get("output_tokens") or 0,
        cache_read_tokens=details.get("cache_read") or 0,
        cache_write_tokens=details.get("cache_creation") or 0,
    )


def _add_usage(a: Usage, b: Usage) -> Usage:
    return Usage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
        cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
    )
