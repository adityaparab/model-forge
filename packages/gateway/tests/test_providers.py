"""Provider builders: construction only, never invoked, so no network."""

from pathlib import Path
from typing import Any

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from modelforge_gateway.gateway import check_credentials
from modelforge_gateway.profiles import Route, StructuredMethod, load_profile
from modelforge_gateway.providers import build_chat_model, structured
from modelforge_gateway.providers.fake import ScriptedChatModel
from modelforge_gateway.schemas import GatewayError, Provider
from modelforge_gateway.settings import GatewaySettings
from pydantic import BaseModel, SecretStr


def test_anthropic_builder_passes_key_url_effort_and_ignores_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy.invalid")
    model = build_chat_model(
        Route(provider="anthropic", model="claude-opus-5-5", effort="low"),
        GatewaySettings(anthropic_api_key="k", request_timeout_s=7.0),
        max_output_tokens=512,
    )
    assert isinstance(model, ChatAnthropic)
    assert model.anthropic_api_url == "https://api.anthropic.com"
    assert model.max_tokens == 512
    assert model.reasoning_effort == "low"
    assert model.model == "claude-opus-5-5"
    assert model.max_retries == 2
    assert model.default_request_timeout == 7.0
    assert model.anthropic_api_key.get_secret_value() == "k"
    assert ChatAnthropic.model_fields["reasoning_effort"].alias == "effort"
    with pytest.raises(GatewayError) as exc:
        build_chat_model(
            Route(provider="anthropic", model="m"), GatewaySettings(), max_output_tokens=1
        )
    assert exc.value.kind == "config"
    assert exc.value.message == "ANTHROPIC_API_KEY is not set"


def test_anthropic_builder_uses_the_modelforge_base_url() -> None:
    model = build_chat_model(
        Route(provider="anthropic", model="claude-opus-5-5"),
        GatewaySettings(anthropic_api_key="k", anthropic_base_url="http://gw:8"),
        8,
    )
    assert isinstance(model, ChatAnthropic)
    assert model.anthropic_api_url == "http://gw:8"


def test_local_builders_use_settings_urls_and_model_overrides() -> None:
    o = build_chat_model(
        Route(provider="ollama", model="gpt-oss:20b"),
        GatewaySettings(ollama_model="qwen3:8b", ollama_base_url="http://h:1"),
        max_output_tokens=9,
    )
    assert isinstance(o, ChatOllama)
    assert o.model == "qwen3:8b" and o.base_url == "http://h:1" and o.num_predict == 9
    assert o.num_ctx == 32768
    lm = build_chat_model(
        Route(provider="lmstudio", model="google/gemma-4-26b-a4b-qat"),
        GatewaySettings(),
        max_output_tokens=9,
    )
    assert isinstance(lm, ChatOpenAI)
    assert lm.openai_api_base == "http://localhost:1234/v1"
    assert lm.model_name == "google/gemma-4-26b-a4b-qat"
    assert lm.max_tokens == 9
    assert isinstance(lm.openai_api_key, SecretStr)
    assert lm.openai_api_key.get_secret_value() == "lm-studio"
    overridden = build_chat_model(
        Route(provider="lmstudio", model="google/gemma-4-26b-a4b-qat"),
        GatewaySettings(lmstudio_model="qwen/qwen3-8b"),
        9,
    )
    assert isinstance(overridden, ChatOpenAI) and overridden.model_name == "qwen/qwen3-8b"


def test_fake_builder_returns_the_scripted_model() -> None:
    model = build_chat_model(Route(provider="fake", model="scripted-v1"), GatewaySettings(), 1)
    assert isinstance(model, ScriptedChatModel)


def test_check_credentials(repo_profiles_dir: Path) -> None:
    default = load_profile(repo_profiles_dir, "default")
    with pytest.raises(GatewayError) as exc:
        check_credentials(default, GatewaySettings())
    assert exc.value.kind == "config"
    assert exc.value.message == (
        "profile 'default' routes to anthropic but ANTHROPIC_API_KEY is not set"
    )
    check_credentials(default, GatewaySettings(anthropic_api_key="k"))
    check_credentials(load_profile(repo_profiles_dir, "fake"), GatewaySettings())
    check_credentials(load_profile(repo_profiles_dir, "local-ollama"), GatewaySettings())


def test_ollama_builder_passes_the_configured_context_window() -> None:
    o = build_chat_model(
        Route(provider="ollama", model="gpt-oss:20b"), GatewaySettings(ollama_num_ctx=8192), 9
    )
    assert isinstance(o, ChatOllama) and o.num_ctx == 8192


class Answer(BaseModel):
    text: str


class RecordingModel(BaseChatModel):
    """Records the keyword arguments of every `with_structured_output` call."""

    seen: list[dict[str, Any]] = []

    @property
    def _llm_type(self) -> str:
        return "recording"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError

    def with_structured_output(
        self, schema: dict[str, Any] | type, **kwargs: Any
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        self.seen.append(kwargs)
        return RunnableLambda(lambda model_input: {})


@pytest.mark.parametrize(
    ("provider", "method", "expected"),
    [
        ("anthropic", None, {"method": "json_schema", "include_raw": True}),
        ("ollama", None, {"method": "json_schema", "include_raw": True}),
        ("lmstudio", None, {"method": "json_schema", "include_raw": True}),
        ("fake", None, {"include_raw": True}),
        ("anthropic", "function_calling", {"method": "function_calling", "include_raw": True}),
        ("fake", "function_calling", {"include_raw": True}),
    ],
)
def test_structured_output_method_per_provider(
    provider: Provider, method: StructuredMethod | None, expected: dict[str, Any]
) -> None:
    model = RecordingModel(seen=[])
    structured(model, provider, Answer, method=method)
    assert model.seen == [expected]
