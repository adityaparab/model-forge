"""Server-sent event framing and the polling stream behind the event routes (spec 3.5).

The stream polls the events table: each read runs in a worker thread through
`asyncio.to_thread`, so no thread or connection is held between polls.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable

from modelforge_lineage.models import EventRecord
from starlette.requests import Request

__all__ = [
    "KEEPALIVE",
    "KEEPALIVE_S",
    "PAGE_SIZE",
    "POLL_S",
    "Reader",
    "event_stream",
    "format_event",
    "start_after",
]

KEEPALIVE = b": keepalive\n\n"
Reader = Callable[[int], list[EventRecord]]
"""``read(after)``: up to `PAGE_SIZE` events with an id above ``after``, in id order."""
POLL_S = 0.5
"""Seconds between reads once the stream has caught up."""
KEEPALIVE_S = 15.0
"""Seconds of silence before a keepalive comment."""
PAGE_SIZE = 500
"""Events per read; a full page is followed by another read without waiting."""


def format_event(event: EventRecord) -> bytes:
    """One SSE message: ``id: <id>\\nevent: <type>\\ndata: <EventRecord JSON>\\n\\n``."""
    data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.type}\ndata: {data}\n\n".encode()


def start_after(last_event_id: str | None, after: int | None, *, default: int) -> int:
    """The event id to stream after: ``after`` wins, then a ``Last-Event-ID`` that parses
    as a non-negative integer, else ``default``."""
    if after is not None:
        return after
    if last_event_id is not None:
        try:
            parsed = int(last_event_id)
        except ValueError:
            return default
        if parsed >= 0:
            return parsed
    return default


async def event_stream(
    request: Request,
    read: Reader,
    start: int,
    *,
    poll_s: float = POLL_S,
    keepalive_s: float = KEEPALIVE_S,
) -> AsyncIterator[bytes]:
    """Yield every event ``read(after)`` returns above ``start``, then poll for new ones
    until the client disconnects; a keepalive comment after ``keepalive_s`` of silence."""
    last = start
    written = time.monotonic()
    while not await request.is_disconnected():
        page = await asyncio.to_thread(read, last)
        for event in page:
            yield format_event(event)
            last = event.id
        if page:
            written = time.monotonic()
        if len(page) >= PAGE_SIZE:
            continue
        if time.monotonic() - written >= keepalive_s:
            yield KEEPALIVE
            written = time.monotonic()
        await asyncio.sleep(poll_s)
