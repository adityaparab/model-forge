"""``GET /settings`` and ``PATCH /settings``: the server-side settings with their audit trail."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from modelforge_gateway.profiles import load_profile
from modelforge_gateway.schemas import GatewayError
from modelforge_lineage import settings as server_settings
from modelforge_lineage.db import Database

from modelforge_api.deps import get_db, get_settings
from modelforge_api.errors import ApiError
from modelforge_api.identity import Identity, require
from modelforge_api.routers.system import config
from modelforge_api.schemas import (
    ProfileView,
    RouteView,
    SettingsEditable,
    SettingsPatch,
    SettingsResponse,
)
from modelforge_api.settings import ApiSettings

__all__ = ["AUDIT_LIMIT", "list_profiles", "router"]

router = APIRouter(prefix="/settings", tags=["settings"])

AUDIT_LIMIT = 20

Db = Annotated[Database, Depends(get_db)]
Settings = Annotated[ApiSettings, Depends(get_settings)]
Admin = Annotated[Identity, Depends(require("admin"))]


def list_profiles(profiles_dir: Path) -> list[ProfileView]:
    """Every ``*.yaml`` of ``profiles_dir`` by name; one that does not load carries its error."""
    views: list[ProfileView] = []
    for path in sorted(profiles_dir.glob("*.yaml")):
        try:
            profile = load_profile(profiles_dir, path.stem)
        except GatewayError as exc:
            views.append(ProfileView(name=path.stem, routes={}, prices={}, error=str(exc)))
            continue
        routes = {
            purpose: RouteView(
                provider=route.provider,
                model=route.model,
                effort=route.effort,
                structured=route.structured,
            )
            for purpose, route in sorted(profile.routes.items())
        }
        views.append(
            ProfileView(name=path.stem, routes=routes, prices=dict(profile.prices), error=None)
        )
    return views


def _document(settings: ApiSettings, db: Database) -> SettingsResponse:
    core = settings.core
    with db.connection() as conn:
        stored = server_settings.get_all(conn)
        audit = server_settings.list_audit(conn, limit=AUDIT_LIMIT)
    editable = SettingsEditable(
        default_profile=stored.get("default_profile", core.profile),
        default_cost_cap_usd=stored.get("default_cost_cap_usd", core.gateway.cost_cap_usd),
        auto_replay=stored.get("auto_replay", False),
    )
    return SettingsResponse(
        editable=editable,
        audit=audit,
        config=config(settings, db),
        profiles=list_profiles(core.gateway.profiles_dir),
    )


@router.get("", response_model=SettingsResponse)
def read_settings(settings: Settings, db: Db) -> SettingsResponse:
    return _document(settings, db)


@router.patch(
    "",
    response_model=SettingsResponse,
    responses={400: {"description": "An unknown profile or a cap that is not positive."}},
)
def patch_settings(
    patch: SettingsPatch, identity: Admin, settings: Settings, db: Db
) -> SettingsResponse:
    """Change any subset of the editable settings; each changed key writes an audit row."""
    changes = patch.model_dump(exclude_none=True)
    profile = changes.get("default_profile")
    if profile is not None:
        try:
            load_profile(settings.core.gateway.profiles_dir, profile)
        except GatewayError as exc:
            raise ApiError(400, "settings", str(exc), key="default_profile") from None
    cap = changes.get("default_cost_cap_usd")
    if cap is not None and not cap > 0:
        raise ApiError(
            400,
            "settings",
            f"default_cost_cap_usd must be positive, not {cap}",
            key="default_cost_cap_usd",
        )
    with db.transaction() as conn:
        for key in server_settings.EDITABLE_KEYS:
            if key in changes:
                server_settings.set_value(conn, key, changes[key], updated_by=identity.name)
    return _document(settings, db)
