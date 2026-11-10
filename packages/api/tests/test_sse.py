import asyncio
import json
import queue
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from modelforge_api import sse
from modelforge_lineage.db import Database
from modelforge_lineage.events import append_event
from modelforge_lineage.models import EventRecord

from .conftest import Submit

API = "/api/v1"


class _Lines:
    """Reads a streamed response's lines in a helper thread, so a wait can time out."""

    def __init__(self, lines: Iterator[str]) -> None:
        self._lines = lines
        self._queue: queue.Queue[str | None] = queue.Queue()
        self.seen: list[str] = []
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        try:
            for line in self._lines:
                self._queue.put(line)
        except Exception:  # the stream was closed under the reader
            pass
        finally:
            self._queue.put(None)

    def until(self, done: Callable[[list[str]], bool], timeout: float) -> list[str]:
        """Collect lines until ``done(seen)`` holds or ``timeout`` seconds pass; ``seen``."""
        deadline = time.monotonic() + timeout
        while not done(self.seen):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                line = self._queue.get(timeout=remaining)
            except queue.Empty:
                break
            if line is None:
                break
            self.seen.append(line)
        return self.seen


@contextmanager
def _stream(
    client: TestClient, path: str, headers: dict[str, str] | None = None
) -> Iterator[_Lines]:
    with client.stream("GET", f"{API}{path}", headers=headers or {}) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        lines = _Lines(response.iter_lines())
        yield lines
    lines.thread.join(timeout=2)
    assert not lines.thread.is_alive(), "closing the stream did not end the reader"


def _data(lines: list[str]) -> list[dict[str, object]]:
    return [json.loads(line.removeprefix("data: ")) for line in lines if line.startswith("data: ")]


def _head(client: TestClient, run_id: str) -> int:
    events = client.get(f"{API}/runs/{run_id}/events", params={"limit": 500}).json()
    return int(events["next_after"])


def test_format_event_and_start_after() -> None:
    event = EventRecord(
        id=3,
        run_id="r1",
        step_id=None,
        ts="2026-11-10T00:00:00+00:00",
        type="run.created",
        payload={"a": 1},
        prev_hash="p",
        hash="h",
    )
    assert sse.format_event(event) == (
        b"id: 3\nevent: run.created\ndata: "
        b'{"id":3,"run_id":"r1","step_id":null,"ts":"2026-11-10T00:00:00+00:00",'
        b'"type":"run.created","payload":{"a":1},"prev_hash":"p","hash":"h"}\n\n'
    )
    assert sse.KEEPALIVE == b": keepalive\n\n"
    assert sse.start_after(None, None, default=7) == 7
    assert sse.start_after("12", None, default=7) == 12
    assert sse.start_after("abc", None, default=7) == 7
    assert sse.start_after("-3", None, default=7) == 7
    assert sse.start_after("", None, default=7) == 7
    assert sse.start_after(None, 4, default=7) == 4
    assert sse.start_after("12", 4, default=7) == 4
    assert sse.start_after(None, 0, default=7) == 0


def test_event_stream_pages_without_waiting_then_keeps_alive() -> None:
    """A full page is read again at once; once caught up, silence yields a keepalive."""

    def event(i: int) -> EventRecord:
        return EventRecord(
            id=i, run_id="r", step_id=None, ts="t", type="e", payload={}, prev_hash="", hash=""
        )

    reads: list[int] = []

    def read(after: int) -> list[EventRecord]:
        reads.append(after)
        return [event(i) for i in range(after + 1, min(after + sse.PAGE_SIZE, 600) + 1)]

    class Client:
        async def is_disconnected(self) -> bool:
            return len(reads) >= 4

    async def collect() -> list[bytes]:
        stream = sse.event_stream(Client(), read, 0, poll_s=0.01, keepalive_s=0.0)  # type: ignore[arg-type]
        return [chunk async for chunk in stream]

    chunks = asyncio.run(collect())
    assert reads == [0, 500, 600, 600]
    assert len([c for c in chunks if c.startswith(b"id: ")]) == 600
    assert chunks[600:] == [sse.KEEPALIVE] * 3  # one per caught-up poll at keepalive_s=0


@pytest.mark.db
def test_run_stream_replays_history(client: TestClient, submit: Submit) -> None:
    run_id = submit()
    head = _head(client, run_id)
    path = f"/runs/{run_id}/events/stream"

    with _stream(client, path) as lines:
        seen = lines.until(lambda seen: len(_data(seen)) >= 1, timeout=3)
    assert seen[0].startswith("id: ") and seen[1] == "event: run.created"
    first = _data(seen)[0]
    assert first["run_id"] == run_id and first["type"] == "run.created"
    assert seen[0] == f"id: {first['id']}"

    with _stream(client, path, {"Last-Event-ID": str(head)}) as lines:
        seen = lines.until(lambda seen: bool(_data(seen)), timeout=1.5)
    assert _data(seen) == []
    assert all(line in ("", ": keepalive") for line in seen)

    with _stream(client, path, {"Last-Event-ID": "zzz"}) as lines:
        seen = lines.until(lambda seen: len(_data(seen)) >= 1, timeout=3)
    assert _data(seen)[0]["type"] == "run.created"

    with _stream(client, f"{path}?after={head}", {"Last-Event-ID": "0"}) as lines:
        seen = lines.until(lambda seen: bool(_data(seen)), timeout=1.0)
    assert _data(seen) == []  # the query parameter wins over the header


@pytest.mark.db
def test_run_stream_delivers_live_event(
    client: TestClient, submit: Submit, database: Database
) -> None:
    run_id = submit()
    head = _head(client, run_id)
    started = time.monotonic()
    with _stream(client, f"/runs/{run_id}/events/stream", {"Last-Event-ID": str(head)}) as lines:
        with database.transaction() as conn:
            appended = append_event(conn, run_id, "test.ping", {})
        seen = lines.until(lambda seen: bool(_data(seen)), timeout=3)
        assert "event: test.ping" in seen
        assert f"id: {appended.id}" in seen
        assert [d["id"] for d in _data(seen)] == [appended.id]
    assert time.monotonic() - started < 5


@pytest.mark.db
def test_global_stream_starts_at_head(client: TestClient, submit: Submit) -> None:
    submit()  # an older run whose events the new subscriber must not receive
    with _stream(client, "/events/stream") as lines:
        run_id = submit()
        seen = lines.until(
            lambda seen: any(d["type"] == "run.created" for d in _data(seen)), timeout=3
        )
    received = _data(seen)
    assert received and received[0]["type"] == "run.created"
    assert received[0]["run_id"] == run_id
    assert all(d["run_id"] == run_id for d in received)


@pytest.mark.db
def test_unknown_run_stream_404(client: TestClient) -> None:
    response = client.get(f"{API}/runs/nope/events/stream")
    assert response.status_code == 404
    assert response.json()["error"]["kind"] == "not_found"
