"""The ``ollama`` provider: a local ``ChatOllama`` server."""

from langchain_ollama import ChatOllama

from modelforge_gateway.profiles import Route
from modelforge_gateway.settings import GatewaySettings


def build(route: Route, settings: GatewaySettings, max_output_tokens: int) -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model or route.model,
        base_url=settings.ollama_base_url,
        num_predict=max_output_tokens,
    )
