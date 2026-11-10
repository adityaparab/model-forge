"""Server-sent event framing and the polling stream behind the event routes (spec 3.5).

Every frame is an unnamed message (``id`` and ``data`` only, no ``event:`` line), so a
browser `EventSource` hands each one to ``onmessage``; the event type travels inside the
data as the `EventRecord`'s ``type``. A named frame would reach only a listener registered
for that name, so a client would silently drop any type it did not list.

The stream polls the events table: each read runs in a worker thread through
`asyncio.to_thread`, so no thread or connection is held between polls.

Event ids come from a sequence at insert, but commits are serialised only per run, so
across runs a lower id can commit after a higher one was already read. The run stream is
exact; the global stream re-reads a trailing window of `WINDOW` ids behind its cursor
and skips ids it has delivered. Limitation: an event that commits out of order more than
`WINDOW` ids behind the cursor, or at or below the id the stream started after, is
missed. A reconnect with ``Last-Event-ID`` re-reads the window, so it may repeat events;
the console's global hook only invalidates queries, so duplicates are harmless.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable

from modelforge_lineage.models import EventRecord
from modelforge_lineage.observability import get_logger
from starlette.requests import Request

__all__ = [
    "KEEPALIVE",
    "KEEPALIVE_S",
    "PAGE_SIZE",
    "POLL_S",
    "WINDOW",
    "Reader",
    "event_stream",
    "format_event",
    "start_after",
]

_log = get_logger(component="api")

KEEPALIVE = b": keepalive\n\n"
Reader = Callable[[int], list[EventRecord]]
"""``read(after)``: up to a page of events with an id above ``after``, in id order."""
POLL_S = 0.5
"""Seconds between reads once the stream has caught up."""
KEEPALIVE_S = 15.0
"""Seconds of silence before a keepalive comment."""
PAGE_SIZE = 500
"""Events per read; a full page is followed by another read without waiting."""
WINDOW = 200
"""Ids behind its cursor the global stream re-reads for out-of-order commits."""


def format_event(event: EventRecord) -> bytes:
    """One SSE message: ``id: <id>\\ndata: <EventRecord JSON>\\n\\n`` (no ``event:`` line)."""
    data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"id: {event.id}\ndata: {data}\n\n".encode()


def start_after(last_event_id: str | None, after: int | None, *, default: int) -> int:
    """The event id to stream after: ``after`` wins, then a ``Last-Event-ID`` of ASCII
    digits only, else ``default``."""
    if after is not None:
        return after
    if last_event_id is not None and last_event_id.isascii() and last_event_id.isdigit():
        return int(last_event_id)
    return default


async def event_stream(
    request: Request,
    read: Reader,
    start: int,
    *,
    poll_s: float = POLL_S,
    keepalive_s: float = KEEPALIVE_S,
    page_size: int = PAGE_SIZE,
    window: int = 0,
) -> AsyncIterator[bytes]:
    """Yield every event ``read(after)`` returns above ``start``, then poll for new ones
    until the client disconnects; a keepalive comment after ``keepalive_s`` of silence.

    ``read`` must return at most ``page_size`` events. With ``window`` above 0 each read
    starts ``window`` ids behind the cursor (never at or below ``start``) and ids already
    delivered are skipped. A failed read is logged once and ends the stream.
    """
    if window >= page_size:
        raise ValueError("window must be smaller than page_size")
    last = start
    delivered: set[int] = set()
    written = time.monotonic()
    while not await request.is_disconnected():
        floor = max(last - window, start)
        try:
            page = await asyncio.to_thread(read, floor)
        except Exception as exc:
            _log.error("sse.read_failed", type=type(exc).__name__, error=str(exc))
            return
        sent = False
        for event in page:
            if event.id in delivered:
                continue
            yield format_event(event)
            sent = True
            last = max(last, event.id)
            if window:
                delivered.add(event.id)
        if window:
            floor = max(last - window, start)
            delivered = {i for i in delivered if i > floor}
        if sent:
            written = time.monotonic()
        if len(page) >= page_size:
            continue
        if time.monotonic() - written >= keepalive_s:
            yield KEEPALIVE
            written = time.monotonic()
        await asyncio.sleep(poll_s)
