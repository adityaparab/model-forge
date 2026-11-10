"""Response models of the API; the console's TypeScript types are generated from them."""

from typing import Literal

from modelforge_lineage.models import GateName, RunStatus
from pydantic import BaseModel

__all__ = [
    "ConfigResponse",
    "Directories",
    "GateInfo",
    "HealthResponse",
    "IdentityConfig",
    "TraceUrls",
    "TracingConfig",
    "Versions",
]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: str
    """``ok``, or the error the database query raised."""
    version: str


class IdentityConfig(BaseModel):
    mode: Literal["open", "roles"]
    """``open``: every identity has every role; ``roles``: the ``MODELFORGE_USERS`` map."""
    roles: dict[str, list[str]]


class TraceUrls(BaseModel):
    jaeger: str | None
    phoenix: str | None


class TracingConfig(BaseModel):
    endpoints: list[str]
    content: bool


class Directories(BaseModel):
    artifacts: str
    data: str
    templates: str
    knowledge: str
    prompts: str
    profiles: str
    briefs: str
    console: str


class Versions(BaseModel):
    core: str
    orchestrator: str
    api: str


class GateInfo(BaseModel):
    name: GateName
    status: RunStatus
    """The run status while the run waits at this gate."""
    decisions: list[str]


class ConfigResponse(BaseModel):
    """The redacted effective configuration: never a URL with a password or a key."""

    profile_default: str
    console: Literal["built", "missing", "dev"]
    identity: IdentityConfig
    trace_urls: TraceUrls
    tracing: TracingConfig
    directories: Directories
    versions: Versions
    nodes: list[str]
    gates: list[GateInfo]
