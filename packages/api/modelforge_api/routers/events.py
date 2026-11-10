"""``GET /runs/{id}/events/stream`` and ``GET /events/stream``: server-sent events (spec 3.5)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from modelforge_lineage.db import Database
from modelforge_lineage.models import EventRecord

from modelforge_api import queries, sse
from modelforge_api.deps import get_db
from modelforge_api.routers.runs import Run

__all__ = ["router"]

router = APIRouter(tags=["events"])

Db = Annotated[Database, Depends(get_db)]
LastEventId = Annotated[str | None, Header(alias="Last-Event-ID")]
After = Annotated[int | None, Query(ge=0, description="An event id; stream the events above it.")]

_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _response(request: Request, read: sse.Reader, start: int) -> StreamingResponse:
    return StreamingResponse(
        sse.event_stream(request, read, start),
        media_type="text/event-stream",
        headers=_HEADERS,
    )


@router.get("/runs/{run_id}/events/stream", response_class=StreamingResponse)
def run_stream(
    request: Request,
    run: Run,
    db: Db,
    after: After = None,
    last_event_id: LastEventId = None,
) -> StreamingResponse:
    """The run's events, from the beginning unless ``after`` or ``Last-Event-ID`` says."""
    run_id = run.id

    def read(after_id: int) -> list[EventRecord]:
        with db.connection() as conn:
            return queries.list_events_after(conn, run_id, after_id, sse.PAGE_SIZE)

    return _response(request, read, sse.start_after(last_event_id, after, default=0))


@router.get("/events/stream", response_class=StreamingResponse)
def global_stream(
    request: Request,
    db: Db,
    after: After = None,
    last_event_id: LastEventId = None,
) -> StreamingResponse:
    """Every run's events, from the current head unless ``after`` or ``Last-Event-ID`` says."""

    def read(after_id: int) -> list[EventRecord]:
        with db.connection() as conn:
            return queries.list_events_after_global(conn, after_id, sse.PAGE_SIZE)

    with db.connection() as conn:
        head = queries.events_head(conn)
    return _response(request, read, sse.start_after(last_event_id, after, default=head))
