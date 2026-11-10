"""The ``lmstudio`` provider: LM Studio's OpenAI-compatible local server."""

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from modelforge_gateway.profiles import Route
from modelforge_gateway.settings import GatewaySettings

LMSTUDIO_API_KEY = "lm-studio"  # LM Studio ignores the key; the client requires one


def build(route: Route, settings: GatewaySettings, max_output_tokens: int) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.lmstudio_model or route.model,
        base_url=settings.lmstudio_base_url,
        api_key=SecretStr(LMSTUDIO_API_KEY),
        max_completion_tokens=max_output_tokens,
        max_retries=2,
        timeout=settings.request_timeout_s,
    )
