import asyncio
import json
import queue
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from modelforge_api import sse
from modelforge_lineage.db import Database
from modelforge_lineage.events import append_event
from modelforge_lineage.models import EventRecord
from structlog.testing import capture_logs

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


def _event(i: int, run_id: str = "r") -> EventRecord:
    return EventRecord(
        id=i, run_id=run_id, step_id=None, ts="t", type="e", payload={}, prev_hash="", hash=""
    )


class _FakeRequest:
    """Reports a disconnect once ``reads`` has ``polls`` entries."""

    def __init__(self, reads: list[int], polls: int) -> None:
        self._reads = reads
        self._polls = polls

    async def is_disconnected(self) -> bool:
        return len(self._reads) >= self._polls


def _collect(stream: AsyncIterator[bytes]) -> list[bytes]:
    async def collect() -> list[bytes]:
        return [chunk async for chunk in stream]

    return asyncio.run(collect())


def _ids(chunks: list[bytes]) -> list[int]:
    return [
        int(c.split(b"\n", 1)[0].removeprefix(b"id: ")) for c in chunks if c.startswith(b"id: ")
    ]


def test_event_stream_pages_without_waiting_then_keeps_alive() -> None:
    """A full page is read again at once; once caught up, silence yields a keepalive."""
    reads: list[int] = []

    def read(after: int) -> list[EventRecord]:
        reads.append(after)
        return [_event(i) for i in range(after + 1, min(after + 50, 120) + 1)]

    request = _FakeRequest(reads, polls=5)
    stream = sse.event_stream(request, read, 0, poll_s=0.01, keepalive_s=0.0, page_size=50)  # type: ignore[arg-type]
    chunks = _collect(stream)
    assert reads == [0, 50, 100, 120, 120]
    assert _ids(chunks) == list(range(1, 121))
    assert chunks[120:] == [sse.KEEPALIVE] * 3  # one per caught-up poll at keepalive_s=0


def test_event_stream_window_delivers_out_of_order_commits_once() -> None:
    """With a window, an id committing behind the cursor is still delivered, each id once."""
    committed = {5, 11}
    reads: list[int] = []

    def read(after: int) -> list[EventRecord]:
        reads.append(after)
        if len(reads) == 2:
            committed.add(10)  # committed after 11 was read
        if len(reads) == 3:
            committed.add(12)
        return [_event(i) for i in sorted(committed) if i > after][:50]

    request = _FakeRequest(reads, polls=4)
    stream = sse.event_stream(request, read, 3, poll_s=0.01, page_size=50, window=8)  # type: ignore[arg-type]
    assert _ids(_collect(stream)) == [5, 11, 10, 12]
    assert reads == [3, 3, 3, 4]  # max(last - window, start)


def test_event_stream_ends_on_read_failure() -> None:
    def read(after: int) -> list[EventRecord]:
        raise RuntimeError("database gone")

    with capture_logs() as logs:
        chunks = _collect(sse.event_stream(_FakeRequest([], polls=99), read, 0))  # type: ignore[arg-type]
    assert chunks == []
    assert [log["event"] for log in logs] == ["sse.read_failed"]
    assert logs[0]["type"] == "RuntimeError"


def test_start_after_header_digits_only() -> None:
    for header in ("+5", " 5", "5 ", "\u0665", "1_0", "-0"):
        assert sse.start_after(header, None, default=7) == 7, header
    assert sse.start_after("0", None, default=7) == 0


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

    created = received[0]["id"]
    with _stream(client, "/events/stream", {"Last-Event-ID": str(created)}) as lines:
        seen = lines.until(lambda seen: any(d["id"] == created for d in _data(seen)), timeout=3)
    resumed = [d["id"] for d in _data(seen)]
    assert created in resumed  # a resume re-reads the window behind the client's id
    assert len(resumed) == len(set(resumed))


@pytest.mark.db
def test_unknown_run_stream_404(client: TestClient) -> None:
    response = client.get(f"{API}/runs/nope/events/stream")
    assert response.status_code == 404
    assert response.json()["error"]["kind"] == "not_found"
