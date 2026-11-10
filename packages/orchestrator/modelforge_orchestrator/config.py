"""Orchestrator settings read from the environment, with a `.env` file honoured when present."""

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modelforge_gateway.settings import GatewaySettings, gateway_settings_from_env
from modelforge_mcp.common import ServerSettings

DEFAULT_DATABASE_URL = "postgresql://modelforge:modelforge@localhost:5432/modelforge"


@dataclass(frozen=True)
class Settings:
    database_url: str
    artifact_dir: Path
    data_dir: Path
    templates_dir: Path
    profile: str
    mcp_command: tuple[str, ...] | None
    tool_timeout_s: float
    log_level: str
    knowledge_dir: Path = Path("./knowledge-base")
    gateway: GatewaySettings = GatewaySettings()
    auto_replay: bool = False


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_settings(
    env: Mapping[str, str] | None = None, *, dotenv: Path | None = Path(".env")
) -> Settings:
    """Build settings from ``env`` (default ``os.environ``); `.env` fills only absent keys."""
    source: dict[str, str] = dict(os.environ if env is None else env)
    if dotenv is not None and dotenv.is_file():
        for key, value in _read_dotenv(dotenv).items():
            source.setdefault(key, value)
    command = source.get("MODELFORGE_MCP_COMMAND")
    return Settings(
        database_url=source.get("MODELFORGE_DATABASE_URL", DEFAULT_DATABASE_URL),
        artifact_dir=Path(source.get("MODELFORGE_ARTIFACT_DIR", "./artifacts")),
        data_dir=Path(source.get("MODELFORGE_DATA_DIR", "./data")),
        templates_dir=Path(source.get("MODELFORGE_TEMPLATES_DIR", "./templates")),
        profile=source.get("MODELFORGE_PROFILE", "default"),
        mcp_command=tuple(shlex.split(command)) if command is not None else None,
        tool_timeout_s=float(source.get("MODELFORGE_TOOL_TIMEOUT_S", "600")),
        log_level=source.get("MODELFORGE_LOG_LEVEL", "INFO"),
        knowledge_dir=Path(source.get("MODELFORGE_KNOWLEDGE_DIR", "./knowledge-base")),
        gateway=gateway_settings_from_env(source),
        auto_replay=source.get("MODELFORGE_AUTO_REPLAY", "").lower() in ("1", "true", "yes"),
    )


def server_settings(settings: Settings) -> ServerSettings:
    """The directories an in-process MCP server needs, taken from the orchestrator settings."""
    return ServerSettings(
        artifact_dir=settings.artifact_dir,
        data_dir=settings.data_dir,
        templates_dir=settings.templates_dir,
        knowledge_dir=settings.knowledge_dir,
    )


def setting_seeds(settings: Settings) -> dict[str, Any]:
    """The defaults written to the server-side ``settings`` table where a key is absent."""
    return {
        "default_profile": settings.profile,
        "default_cost_cap_usd": settings.gateway.cost_cap_usd,
        "auto_replay": settings.auto_replay,
    }
