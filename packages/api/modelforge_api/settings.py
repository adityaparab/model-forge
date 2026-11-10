"""API settings: the orchestrator's `Settings` plus the API's own ``MODELFORGE_*`` variables."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from modelforge_orchestrator.config import Settings, _read_dotenv, load_settings

__all__ = ["ROLES", "ApiSettings", "load_api_settings", "parse_users"]

ROLES: frozenset[str] = frozenset({"requester", "approver", "admin"})
"""``requester`` submits runs; ``approver`` decides gates, pauses, resumes, aborts and
replays; ``admin`` changes the server-side settings."""

_ENTRY = re.compile(r"(?P<name>[A-Za-z0-9._@-]{1,64}):(?P<roles>[a-z]+(?:,[a-z]+)*)")


@dataclass(frozen=True)
class ApiSettings:
    core: Settings
    host: str = "0.0.0.0"
    port: int = 8000
    console_dir: Path = Path("console/dist")
    console_dev_url: str | None = None
    briefs_dir: Path = Path("examples/briefs")
    users: dict[str, frozenset[str]] | None = None
    """The role map; ``None`` means every identity has every role."""
    jaeger_url: str | None = "http://localhost:16686"
    phoenix_url: str | None = "http://localhost:6006"


def parse_users(text: str) -> dict[str, frozenset[str]]:
    """Parse ``"alice:requester bob:approver,requester"`` into a role map.

    Raises `ValueError` for an entry that is not ``name:role[,role]`` or names an unknown role.
    """
    users: dict[str, frozenset[str]] = {}
    for entry in text.split():
        match = _ENTRY.fullmatch(entry)
        if match is None:
            raise ValueError(
                f"MODELFORGE_USERS: malformed entry {entry!r}; expected name:role[,role]"
            )
        roles = frozenset(match["roles"].split(","))
        unknown = roles - ROLES
        if unknown:
            raise ValueError(
                f"MODELFORGE_USERS: unknown role {', '.join(sorted(unknown))} in {entry!r};"
                f" roles: {', '.join(sorted(ROLES))}"
            )
        users[match["name"]] = roles
    return users


def _url(source: Mapping[str, str], key: str, default: str | None) -> str | None:
    value = source.get(key)
    if value is None:
        return default
    return value or None


def load_api_settings(
    env: Mapping[str, str] | None = None, *, dotenv: Path | None = Path(".env")
) -> ApiSettings:
    """Build API settings from ``env`` (default ``os.environ``); `.env` fills only absent keys,
    as for the orchestrator settings. An empty URL variable means ``None``."""
    source: dict[str, str] = dict(os.environ if env is None else env)
    if dotenv is not None and dotenv.is_file():
        for key, value in _read_dotenv(dotenv).items():
            source.setdefault(key, value)
    users = source.get("MODELFORGE_USERS")
    return ApiSettings(
        core=load_settings(source, dotenv=None),
        host=source.get("MODELFORGE_API_HOST", "0.0.0.0"),
        port=int(source.get("MODELFORGE_API_PORT", "8000")),
        console_dir=Path(source.get("MODELFORGE_CONSOLE_DIR", "console/dist")),
        console_dev_url=_url(source, "MODELFORGE_CONSOLE_DEV_URL", None),
        briefs_dir=Path(source.get("MODELFORGE_BRIEFS_DIR", "examples/briefs")),
        users=parse_users(users) if users is not None else None,
        jaeger_url=_url(source, "MODELFORGE_JAEGER_URL", "http://localhost:16686"),
        phoenix_url=_url(source, "MODELFORGE_PHOENIX_URL", "http://localhost:6006"),
    )
