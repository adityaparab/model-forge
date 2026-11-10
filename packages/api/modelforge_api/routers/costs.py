"""``GET /costs``: spend across runs over a window of ``llm_calls``."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from modelforge_lineage.db import Database

from modelforge_api import queries
from modelforge_api.deps import get_db
from modelforge_api.errors import ApiError
from modelforge_api.schemas import CostsResponse

__all__ = ["DEFAULT_WINDOW", "router"]

router = APIRouter(prefix="/costs", tags=["costs"])

DEFAULT_WINDOW = timedelta(days=30)


def _aware(moment: datetime) -> datetime:
    """A timestamp without an offset is UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@router.get(
    "",
    response_model=CostsResponse,
    responses={400: {"description": "since is after until."}},
)
def costs(
    db: Annotated[Database, Depends(get_db)],
    since: Annotated[
        datetime | None, Query(description="ISO timestamp; default 30 days before until.")
    ] = None,
    until: Annotated[datetime | None, Query(description="ISO timestamp; default now.")] = None,
) -> CostsResponse:
    end = datetime.now(UTC) if until is None else _aware(until)
    start = end - DEFAULT_WINDOW if since is None else _aware(since)
    if start > end:
        raise ApiError(
            400, "validation", f"since ({start.isoformat()}) is after until ({end.isoformat()})"
        )
    with db.connection() as conn:
        return queries.costs_window(conn, start, end)
