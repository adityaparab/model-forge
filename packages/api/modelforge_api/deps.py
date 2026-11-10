"""FastAPI dependencies reading what the lifespan put on ``app.state``."""

from fastapi import Request
from modelforge_lineage.db import Database
from modelforge_lineage.store import ArtifactStore

from modelforge_api.settings import ApiSettings

__all__ = ["get_db", "get_settings", "get_store"]


def get_settings(request: Request) -> ApiSettings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_store(request: Request) -> ArtifactStore:
    return request.app.state.store
