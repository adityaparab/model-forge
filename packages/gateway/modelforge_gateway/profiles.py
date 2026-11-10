"""Model profiles: named provider and model configurations."""

import re
from pathlib import Path
from typing import Literal, get_args

import yaml
from modelforge_core.schemas import StrictModel
from pydantic import ValidationError, model_validator

from modelforge_gateway.schemas import Effort, GatewayError, Provider, Purpose

PROFILE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PURPOSES: frozenset[str] = frozenset(get_args(Purpose))

StructuredMethod = Literal["json_schema", "function_calling"]


class Route(StrictModel):
    """``structured`` overrides the provider's structured-output method for this route."""

    provider: Provider
    model: str
    effort: Effort = "medium"
    structured: StructuredMethod | None = None


class Price(StrictModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


class Profile(StrictModel):
    name: str
    routes: dict[Purpose, Route]
    prices: dict[str, Price] = {}

    @model_validator(mode="after")
    def _routes_cover_every_purpose(self) -> "Profile":
        missing = PURPOSES - set(self.routes)
        if missing:
            raise ValueError(f"routes missing purposes: {sorted(missing)}")
        return self


def load_profile(profiles_dir: Path, name: str) -> Profile:
    """Load ``profiles_dir/<name>.yaml``; every failure is a ``config`` error."""
    if not PROFILE_NAME_PATTERN.match(name):
        raise GatewayError("config", f"invalid profile name {name!r}")
    path = profiles_dir / f"{name}.yaml"
    if not path.is_file():
        raise GatewayError("config", f"unknown profile {name!r}: {path} not found")
    try:
        data = yaml.safe_load(path.read_text())
        return Profile.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise GatewayError("config", f"invalid profile {name!r}: {exc}") from exc
