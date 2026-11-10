"""Provider adapters: the only modules that import LangChain integrations."""

from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from modelforge_gateway.profiles import Route
from modelforge_gateway.providers import anthropic, lmstudio, ollama
from modelforge_gateway.providers.fake import ScriptedChatModel
from modelforge_gateway.schemas import GatewayError, Provider
from modelforge_gateway.settings import GatewaySettings

ChatModelFactory = Callable[[Route, GatewaySettings, int], BaseChatModel]


def build_chat_model(
    route: Route, settings: GatewaySettings, max_output_tokens: int
) -> BaseChatModel:
    """Construct the chat model for ``route``; nothing is sent until it is invoked."""
    if route.provider == "anthropic":
        return anthropic.build(route, settings, max_output_tokens)
    if route.provider == "ollama":
        return ollama.build(route, settings, max_output_tokens)
    if route.provider == "lmstudio":
        return lmstudio.build(route, settings, max_output_tokens)
    if route.provider == "fake":
        return ScriptedChatModel()
    raise GatewayError("config", f"unknown provider {route.provider!r}")


def structured(
    chat_model: BaseChatModel, provider: Provider, schema: type[BaseModel]
) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
    """The raw-including structured runnable; every real provider uses JSON-schema mode.

    Anthropic's forced tool calling is unsupported with thinking enabled, so it too uses the
    integration's ``json_schema`` method. The scripted fake takes no method.
    """
    if provider in ("anthropic", "ollama", "lmstudio"):
        return chat_model.with_structured_output(schema, method="json_schema", include_raw=True)
    return chat_model.with_structured_output(schema, include_raw=True)
