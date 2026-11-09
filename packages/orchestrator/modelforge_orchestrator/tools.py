"""The bridge from the synchronous orchestrator to the MCP servers: typed calls, typed errors.

A `_Bridge` owns one background thread running an asyncio loop. A single owning task enters
every MCP client context, waits for a stop signal, and exits those contexts itself (anyio cancel
scopes must be exited by the task that entered them). Graph nodes call tools synchronously
through `ToolClient.call`, which schedules each call onto that loop.
"""

import asyncio
import concurrent.futures
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol, Self, get_args

from mcp import Client, MCPError
from mcp.client.stdio import StdioServerParameters
from mcp.types import REQUEST_TIMEOUT, CallToolResult
from modelforge_core.schemas import StrictModel
from modelforge_lineage.canonical import canonical_json, sha256_hex
from modelforge_lineage.models import ArtifactRef
from modelforge_lineage.observability import FilteringBoundLogger, get_logger
from modelforge_mcp.common import ServerName, ToolErrorPayload
from modelforge_mcp.registry import TOOLS, build_server
from pydantic import ValidationError

from modelforge_orchestrator.config import Settings, server_settings

__all__ = [
    "ALL_SERVERS",
    "BaseToolClient",
    "InProcessToolClient",
    "StdioToolClient",
    "ToolCall",
    "ToolClient",
    "ToolError",
    "extract_refs",
    "parse_error",
    "server_environment",
]

ALL_SERVERS: tuple[ServerName, ...] = get_args(ServerName)
DEFAULT_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0)
RETRYABLE = frozenset({"transport", "timeout"})

ClientFactory = Callable[[], AbstractAsyncContextManager[Client]]


def _log() -> FilteringBoundLogger:
    """Bound per call: a module-level bound logger would keep the config in force at import."""
    return get_logger(component="tool_client")


class ToolError(Exception):
    """A failed tool call. `code` is a server `ToolErrorCode`, or `transport` / `timeout`."""

    def __init__(self, *, server: str, tool: str, version: str, code: str, message: str) -> None:
        self.server = server
        self.tool = tool
        self.version = version
        self.code = code
        self.message = message
        super().__init__(f"{server}/{tool}@{version} {code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {
            "server": self.server,
            "tool": self.tool,
            "version": self.version,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class ToolCall[T: StrictModel]:
    server: ServerName
    tool: str
    version: str
    input_hash: str
    output: T
    refs: list[ArtifactRef]
    duration_ms: int


class ToolClient(Protocol):
    def call(
        self, server: ServerName, tool: str, request: StrictModel, *, timeout_s: float | None = None
    ) -> ToolCall[Any]: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


def extract_refs(output: StrictModel) -> list[ArtifactRef]:
    """The output itself if it is a ref, else its top-level ref (or list-of-ref) fields in order."""
    if isinstance(output, ArtifactRef):
        return [output]
    refs: list[ArtifactRef] = []
    for name in type(output).model_fields:
        value = getattr(output, name)
        if isinstance(value, ArtifactRef):
            refs.append(value)
        elif isinstance(value, list):
            refs.extend(item for item in value if isinstance(item, ArtifactRef))
    return refs


def parse_error(text: str) -> tuple[str, str]:
    """`(code, message)` from an error result's text, which embeds a `ToolErrorPayload`."""
    idx = text.find("{")
    if idx >= 0:
        try:
            payload = ToolErrorPayload.model_validate_json(text[idx:])
        except ValueError:
            pass
        else:
            return payload.code, payload.message
    if "validation error" in text.lower():
        return "invalid_input", text
    return "internal", text


def _describe(exc: BaseException) -> str:
    """The first leaf of an exception group (anyio wraps transport failures), else `exc`."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return str(exc) or type(exc).__name__


async def _tool_versions(client: Client) -> dict[str, str]:
    versions: dict[str, str] = {}
    cursor: str | None = None
    while True:
        listing = await client.list_tools(cursor=cursor)
        for tool in listing.tools:
            versions[tool.name] = str((tool.meta or {}).get("version", ""))
        cursor = listing.next_cursor
        if not cursor:
            return versions


class _Bridge:
    """One thread, one event loop, one owning task holding every client context open."""

    def __init__(self, factories: Mapping[ServerName, ClientFactory]) -> None:
        self._factories = dict(factories)
        self.versions: dict[ServerName, dict[str, str]] = {}
        self.opening: str = "all"
        self._clients: dict[ServerName, Client] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    async def _own(self) -> None:
        self._stop = asyncio.Event()
        async with AsyncExitStack() as stack:
            for name, factory in self._factories.items():
                self.opening = name
                client = await stack.enter_async_context(factory())
                self.versions[name] = await _tool_versions(client)
                self._clients[name] = client
            self.opening = "all"
            self._ready.set()
            await self._stop.wait()

    def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        try:
            self._task = loop.create_task(self._own())
            loop.run_until_complete(self._task)
        except BaseException as exc:  # stored and re-raised in the calling thread
            self._error = exc
        finally:
            self._ready.set()

    def start(self, timeout_s: float = 60) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._thread = threading.Thread(target=self._run, args=(loop,), daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout_s):
            if self._task is not None:
                loop.call_soon_threadsafe(self._task.cancel)
            self._join()
            raise TimeoutError(f"MCP servers did not start within {timeout_s}s")
        if self._error is not None:
            self._join()
            raise self._error

    def call_tool(
        self, server: ServerName, tool: str, args: dict[str, Any], *, timeout_s: float
    ) -> CallToolResult:
        if self._loop is None or server not in self._clients:
            raise RuntimeError(f"bridge has no open client for {server!r}")
        future = asyncio.run_coroutine_threadsafe(
            self._clients[server].call_tool(tool, args, read_timeout_seconds=timeout_s), self._loop
        )
        try:
            return future.result(timeout=timeout_s + 5)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def stop(self) -> None:
        if self._loop is None:
            return
        if self._stop is not None and self._thread is not None and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._stop.set)
        self._join()
        if self._error is not None:
            _log().warning("tool.bridge_shutdown_error", error=repr(self._error))

    def _join(self) -> None:
        if self._thread is not None:
            self._thread.join()
        if self._loop is not None:
            self._loop.close()
        self._loop = None
        self._clients = {}


class BaseToolClient:
    """Synchronous typed tool calls over a `_Bridge`, retrying only transport and timeout errors."""

    def __init__(
        self,
        bridge_factories: Mapping[ServerName, ClientFactory],
        *,
        default_timeout_s: float,
        backoff_s: Sequence[float] = DEFAULT_BACKOFF_S,
    ) -> None:
        self._bridge = _Bridge(bridge_factories)
        self.default_timeout_s = default_timeout_s
        self.backoff_s = tuple(backoff_s)

    def __enter__(self) -> Self:
        try:
            self._bridge.start()
        except Exception as exc:
            raise ToolError(
                server=self._bridge.opening,
                tool="connect",
                version="",
                code="transport",
                message=_describe(exc),
            ) from exc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._bridge.stop()

    def call(
        self, server: ServerName, tool: str, request: StrictModel, *, timeout_s: float | None = None
    ) -> ToolCall[Any]:
        try:
            spec = TOOLS[(server, tool)]
        except KeyError:
            raise ToolError(
                server=server,
                tool=tool,
                version="",
                code="internal",
                message=f"unknown tool {server}/{tool}",
            ) from None
        version = self._bridge.versions.get(server, {}).get(tool, spec.version)
        timeout = self.default_timeout_s if timeout_s is None else timeout_s
        payload = request.model_dump(mode="json")
        input_hash = sha256_hex(canonical_json(payload))
        arguments = {"request": payload}

        def error(code: str, message: str) -> ToolError:
            return ToolError(server=server, tool=tool, version=version, code=code, message=message)

        attempts = len(self.backoff_s) + 1
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                result = self._bridge.call_tool(server, tool, arguments, timeout_s=timeout)
            except concurrent.futures.TimeoutError:
                failure = error("timeout", f"no response within {timeout}s")
            except MCPError as exc:
                code = "timeout" if exc.code == REQUEST_TIMEOUT else "transport"
                failure = error(code, f"MCPError: {exc.message}")
            except Exception as exc:
                failure = error("transport", f"{type(exc).__name__}: {exc}")
            else:
                if not isinstance(result, CallToolResult):
                    failure = error("transport", f"unexpected result {type(result).__name__}")
                elif result.is_error:
                    text = getattr(result.content[0], "text", "") if result.content else ""
                    raise error(*parse_error(text))
                else:
                    try:
                        output = spec.output_model.model_validate(result.structured_content)
                    except ValidationError as exc:
                        raise error(
                            "internal", f"output does not match the contract: {exc}"
                        ) from exc
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    _log().info(
                        "tool.call",
                        server=server,
                        tool=tool,
                        version=version,
                        duration_ms=duration_ms,
                        attempt=attempt,
                    )
                    return ToolCall(
                        server=server,
                        tool=tool,
                        version=version,
                        input_hash=input_hash,
                        output=output,
                        refs=extract_refs(output),
                        duration_ms=duration_ms,
                    )
            if failure.code not in RETRYABLE or attempt == attempts:
                raise failure
            _log().warning(
                "tool.retry",
                server=server,
                tool=tool,
                version=version,
                attempt=attempt,
                code=failure.code,
                message=failure.message,
            )
            time.sleep(self.backoff_s[attempt - 1])
        raise AssertionError("unreachable")


_PASSED_THROUGH = ("PATH", "HOME", "LANG", "VIRTUAL_ENV", "PYTHONPATH", "TMPDIR")


def server_environment(
    settings: Settings, parent: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment of a stdio server: an allowlist of ``parent``, never its secrets.

    Passes ``PATH``, ``HOME``, ``LANG``, ``LC_*``, ``VIRTUAL_ENV``, ``PYTHONPATH`` and ``TMPDIR``
    when present, the three directory settings, and ``MODELFORGE_LOG_LEVEL`` (``WARNING`` unless
    the parent sets it). ``MODELFORGE_DATABASE_URL`` and everything else stay behind.
    """
    source = os.environ if parent is None else parent
    env = {
        key: value
        for key, value in source.items()
        if key in _PASSED_THROUGH or key.startswith("LC_")
    }
    env.update(
        {
            "MODELFORGE_ARTIFACT_DIR": str(settings.artifact_dir),
            "MODELFORGE_DATA_DIR": str(settings.data_dir),
            "MODELFORGE_TEMPLATES_DIR": str(settings.templates_dir),
            "MODELFORGE_LOG_LEVEL": source.get("MODELFORGE_LOG_LEVEL", "WARNING"),
        }
    )
    return env


class StdioToolClient(BaseToolClient):
    """Each server is a `python -m modelforge_mcp <server>` subprocess (or `mcp_command`).

    Every subprocess is started with `--marker <uuid>`, so `server_pids` can find exactly the
    processes this client started.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        servers: Iterable[ServerName] = ALL_SERVERS,
        backoff_s: Sequence[float] = DEFAULT_BACKOFF_S,
    ) -> None:
        command = (
            tuple(settings.mcp_command)
            if settings.mcp_command
            else (sys.executable, "-m", "modelforge_mcp")
        )
        self.marker = uuid.uuid4().hex
        self.server_env = env = server_environment(settings)

        def factory(name: ServerName) -> ClientFactory:
            params = StdioServerParameters(
                command=command[0], args=[*command[1:], name, "--marker", self.marker], env=env
            )
            return lambda: Client(params)

        super().__init__(
            {name: factory(name) for name in servers},
            default_timeout_s=settings.tool_timeout_s,
            backoff_s=backoff_s,
        )

    def server_pids(self) -> list[int]:
        """Pids of the live processes whose command line carries this client's marker."""
        found = subprocess.run(["pgrep", "-f", self.marker], capture_output=True, text=True)
        return [int(pid) for pid in found.stdout.split()]


class InProcessToolClient(BaseToolClient):
    """Servers built in this process and connected through the SDK's in-memory transport."""

    def __init__(
        self,
        settings: Settings,
        *,
        servers: Iterable[ServerName] = ALL_SERVERS,
        backoff_s: Sequence[float] = DEFAULT_BACKOFF_S,
    ) -> None:
        ss = server_settings(settings)

        def factory(name: ServerName) -> ClientFactory:
            return lambda: Client(build_server(name, ss))

        super().__init__(
            {name: factory(name) for name in servers},
            default_timeout_s=settings.tool_timeout_s,
            backoff_s=backoff_s,
        )
