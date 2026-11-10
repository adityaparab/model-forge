"""The ``anthropic`` provider: ``ChatAnthropic`` with an explicit key, URL and effort."""

from langchain_anthropic import ChatAnthropic
from pydantic import SecretStr

from modelforge_gateway.profiles import Route
from modelforge_gateway.schemas import GatewayError
from modelforge_gateway.settings import GatewaySettings

ANTHROPIC_DEFAULT_URL = "https://api.anthropic.com"


def build(route: Route, settings: GatewaySettings, max_output_tokens: int) -> ChatAnthropic:
    """The URL is always explicit: the SDK would otherwise read ``ANTHROPIC_BASE_URL``.

    Fields are passed by their aliases (``model_name`` for ``model``,
    ``max_tokens_to_sample`` for ``max_tokens``, ``effort`` for ``reasoning_effort``),
    the spelling the type checker sees on the integration's constructor.
    """
    if not settings.anthropic_api_key:
        raise GatewayError("config", "ANTHROPIC_API_KEY is not set")
    return ChatAnthropic(
        model_name=route.model,
        api_key=SecretStr(settings.anthropic_api_key),
        base_url=settings.anthropic_base_url or ANTHROPIC_DEFAULT_URL,
        max_tokens_to_sample=max_output_tokens,
        max_retries=2,
        timeout=settings.request_timeout_s,
        effort=route.effort,
        stop=None,
    )
