"""``GET /briefs``: the example briefs a new run can start from."""

from typing import Annotated

from fastapi import APIRouter, Depends

from modelforge_api.deps import get_settings
from modelforge_api.schemas import BriefItem
from modelforge_api.settings import ApiSettings

__all__ = ["router"]

router = APIRouter(prefix="/briefs", tags=["briefs"])


@router.get("", response_model=list[BriefItem])
def list_briefs(settings: Annotated[ApiSettings, Depends(get_settings)]) -> list[BriefItem]:
    """Every ``*.yaml`` of the briefs directory by name; empty when the directory is missing."""
    return [
        BriefItem(name=path.stem, path=str(path), text=path.read_text())
        for path in sorted(settings.briefs_dir.glob("*.yaml"))
    ]
