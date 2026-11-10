"""``GET /health`` and ``GET /config``."""

from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import modelforge_core
import modelforge_orchestrator
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from modelforge_lineage import settings as server_settings
from modelforge_lineage.approvals import VALID_DECISIONS
from modelforge_lineage.db import Database
from modelforge_lineage.models import GateName, RunStatus
from modelforge_orchestrator.graph import NODES

import modelforge_api
from modelforge_api.console import console_mode
from modelforge_api.deps import get_db, get_settings
from modelforge_api.schemas import (
    ConfigResponse,
    Directories,
    GateInfo,
    HealthResponse,
    IdentityConfig,
    TraceUrls,
    TracingConfig,
    Versions,
)
from modelforge_api.settings import ApiSettings

__all__ = ["redact_url", "router"]

router = APIRouter(tags=["system"])

_AWAITING: dict[GateName, RunStatus] = {
    "scope": "awaiting_scope",
    "model": "awaiting_model",
    "signoff": "awaiting_signoff",
}


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "The database query failed."}},
)
def health(db: Annotated[Database, Depends(get_db)]) -> HealthResponse | JSONResponse:
    try:
        with db.connection() as conn:
            conn.execute("select 1")
    except Exception as exc:
        body = HealthResponse(
            status="degraded",
            database=f"{type(exc).__name__}: {exc}",
            version=modelforge_api.__version__,
        )
        return JSONResponse(status_code=503, content=body.model_dump())
    return HealthResponse(status="ok", database="ok", version=modelforge_api.__version__)


def redact_url(url: str) -> str:
    """``url`` without user information, query or fragment, where a password or key could be."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:  # an IPv6 literal
        host = f"[{host}]"
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _optional_url(url: str | None) -> str | None:
    return None if url is None else redact_url(url)


@router.get("/config", response_model=ConfigResponse)
def config(
    settings: Annotated[ApiSettings, Depends(get_settings)],
    db: Annotated[Database, Depends(get_db)],
) -> ConfigResponse:
    core = settings.core
    with db.connection() as conn:
        profile_default = server_settings.get(conn, "default_profile", core.profile)
    if settings.users is None:
        identity = IdentityConfig(mode="open", roles={})
    else:
        roles = {name: sorted(granted) for name, granted in sorted(settings.users.items())}
        identity = IdentityConfig(mode="roles", roles=roles)
    return ConfigResponse(
        profile_default=profile_default,
        console=console_mode(settings),
        identity=identity,
        trace_urls=TraceUrls(
            jaeger=_optional_url(settings.jaeger_url),
            phoenix=_optional_url(settings.phoenix_url),
        ),
        tracing=TracingConfig(
            endpoints=[redact_url(url) for url in core.gateway.otlp_endpoints],
            content=core.gateway.trace_content,
        ),
        directories=Directories(
            artifacts=str(core.artifact_dir),
            data=str(core.data_dir),
            templates=str(core.templates_dir),
            knowledge=str(core.knowledge_dir),
            prompts=str(core.gateway.prompts_dir),
            profiles=str(core.gateway.profiles_dir),
            briefs=str(settings.briefs_dir),
            console=str(settings.console_dir),
        ),
        versions=Versions(
            core=modelforge_core.__version__,
            orchestrator=modelforge_orchestrator.__version__,
            api=modelforge_api.__version__,
        ),
        nodes=list(NODES),
        gates=[
            GateInfo(name=gate, status=_AWAITING[gate], decisions=sorted(decisions))
            for gate, decisions in VALID_DECISIONS.items()
        ],
    )
