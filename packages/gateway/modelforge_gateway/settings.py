"""Gateway settings read from the environment."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GatewaySettings:
    prompts_dir: Path = Path("./prompts")
    profiles_dir: Path = Path("./profiles")
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str | None = None
    ollama_num_ctx: int = 32768
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_model: str | None = None
    cost_cap_usd: float = 10.0
    request_timeout_s: float = 300.0


def gateway_settings_from_env(source: Mapping[str, str]) -> GatewaySettings:
    """Build gateway settings from ``source``; an empty value counts as unset.

    The inherited ``ANTHROPIC_BASE_URL`` is deliberately never read: only
    ``MODELFORGE_ANTHROPIC_BASE_URL`` redirects Anthropic traffic.
    """

    def get(key: str) -> str | None:
        value = source.get(key)
        return value if value else None

    defaults = GatewaySettings()
    prompts_dir = get("MODELFORGE_PROMPTS_DIR")
    profiles_dir = get("MODELFORGE_PROFILES_DIR")
    cost_cap = get("MODELFORGE_COST_CAP_USD")
    timeout = get("MODELFORGE_LLM_TIMEOUT_S")
    num_ctx = get("MODELFORGE_OLLAMA_NUM_CTX")
    return GatewaySettings(
        prompts_dir=Path(prompts_dir) if prompts_dir else defaults.prompts_dir,
        profiles_dir=Path(profiles_dir) if profiles_dir else defaults.profiles_dir,
        anthropic_api_key=get("ANTHROPIC_API_KEY"),
        anthropic_base_url=get("MODELFORGE_ANTHROPIC_BASE_URL"),
        ollama_base_url=get("MODELFORGE_OLLAMA_BASE_URL") or defaults.ollama_base_url,
        ollama_model=get("MODELFORGE_OLLAMA_MODEL"),
        ollama_num_ctx=int(num_ctx) if num_ctx else defaults.ollama_num_ctx,
        lmstudio_base_url=get("MODELFORGE_LMSTUDIO_BASE_URL") or defaults.lmstudio_base_url,
        lmstudio_model=get("MODELFORGE_LMSTUDIO_MODEL"),
        cost_cap_usd=float(cost_cap) if cost_cap else defaults.cost_cap_usd,
        request_timeout_s=float(timeout) if timeout else defaults.request_timeout_s,
    )
