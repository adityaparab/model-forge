"""Shared envelope for every ModelForge MCP server: settings, typed errors, tool registration."""

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

import pandas as pd
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from modelforge_core.datasets.download import ChecksumMismatchError
from modelforge_core.schemas import StrictModel
from modelforge_lineage.models import ArtifactRef, Producer
from modelforge_lineage.observability import get_logger
from modelforge_lineage.store import ArtifactNotFound, ArtifactStore
from pydantic import BaseModel, ConfigDict
from structlog.typing import FilteringBoundLogger

ServerName = Literal["data-profiler", "credit-modeling", "validation", "doc-renderer"]
ToolErrorCode = Literal["invalid_input", "artifact_not_found", "dataset_unavailable", "internal"]


class ServerSettings(StrictModel):
    artifact_dir: Path
    data_dir: Path
    templates_dir: Path


def settings_from_env(env: Mapping[str, str] | None = None) -> ServerSettings:
    source = os.environ if env is None else env
    return ServerSettings(
        artifact_dir=Path(source.get("MODELFORGE_ARTIFACT_DIR", "./artifacts")),
        data_dir=Path(source.get("MODELFORGE_DATA_DIR", "./data")),
        templates_dir=Path(source.get("MODELFORGE_TEMPLATES_DIR", "./templates")),
    )


class ToolSpec(StrictModel):
    """Contract of one tool: which server serves it, its version, its input and output models."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    server: ServerName
    tool: str
    version: str
    input_model: type[StrictModel]
    output_model: type[StrictModel]


class ToolErrorPayload(StrictModel):
    code: ToolErrorCode
    message: str


def fail(code: ToolErrorCode, message: str) -> NoReturn:
    raise ToolError(ToolErrorPayload(code=code, message=message).model_dump_json())


@dataclass
class ToolContext:
    store: ArtifactStore
    settings: ServerSettings
    server: ServerName
    log: FilteringBoundLogger


def make_context(server: ServerName, settings: ServerSettings) -> ToolContext:
    return ToolContext(
        store=ArtifactStore(settings.artifact_dir),
        settings=settings,
        server=server,
        log=get_logger(server=server),
    )


def load_model[T: BaseModel](ctx: ToolContext, artifact_id: str, model_type: type[T]) -> T:
    try:
        return ctx.store.get_model(artifact_id, model_type)
    except ArtifactNotFound:
        fail("artifact_not_found", f"artifact {artifact_id!r} not found")


def load_frame(ctx: ToolContext, artifact_id: str) -> pd.DataFrame:
    try:
        return ctx.store.get_frame(artifact_id)
    except ArtifactNotFound:
        fail("artifact_not_found", f"artifact {artifact_id!r} not found")


def producer(ctx: ToolContext, tool: str, version: str) -> Producer:
    return Producer(server=ctx.server, tool=tool, version=version)


def _artifact_ids(output: BaseModel) -> list[str]:
    ids: list[str] = []
    if isinstance(output, ArtifactRef):
        return [output.id]
    for name in type(output).model_fields:
        value = getattr(output, name)
        if isinstance(value, ArtifactRef):
            ids.append(value.id)
    return ids


def _error_payload(exc: Exception) -> ToolErrorPayload:
    """Classify an exception into a typed payload (the SDK masks non-ToolError exceptions)."""
    if isinstance(exc, ArtifactNotFound):
        return ToolErrorPayload(
            code="artifact_not_found", message=f"artifact {exc.args[0]!r} not found"
        )
    if isinstance(exc, ChecksumMismatchError) or type(exc).__module__.startswith("httpx"):
        return ToolErrorPayload(code="dataset_unavailable", message=f"{type(exc).__name__}: {exc}")
    if isinstance(exc, KeyError) and len(exc.args) == 1:
        # str(KeyError) wraps its message in quotes; report the message itself.
        return ToolErrorPayload(code="invalid_input", message=str(exc.args[0]))
    if isinstance(exc, ValueError | KeyError):
        return ToolErrorPayload(code="invalid_input", message=str(exc))
    return ToolErrorPayload(code="internal", message=f"{type(exc).__name__}: {exc}")


def _error_code(err: ToolError) -> str:
    try:
        return ToolErrorPayload.model_validate_json(str(err)).code
    except ValueError:
        return "unknown"


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def register_tool[I: BaseModel, O: BaseModel](
    server: MCPServer,
    ctx: ToolContext,
    *,
    name: str,
    version: str,
    description: str,
    fn: Callable[[ToolContext, I], O],
    input_model: type[I],
    output_model: type[O],
) -> None:
    def wrapper(request: Any) -> Any:
        log = ctx.log.bind(tool=name, version=version)
        log.info("tool.start")
        started = time.perf_counter()
        try:
            output = fn(ctx, request)
        except ToolError as err:
            log.warning("tool.error", code=_error_code(err), duration_ms=_elapsed_ms(started))
            raise
        except Exception as exc:
            payload = _error_payload(exc)
            if payload.code == "internal":
                log.error(
                    "tool.error",
                    code=payload.code,
                    duration_ms=_elapsed_ms(started),
                    exc_info=exc,
                )
            else:
                log.warning("tool.error", code=payload.code, duration_ms=_elapsed_ms(started))
            raise ToolError(payload.model_dump_json()) from exc
        log.info(
            "tool.end",
            duration_ms=_elapsed_ms(started),
            artifact_ids=_artifact_ids(output),
        )
        return output

    wrapper.__name__ = name
    wrapper.__qualname__ = name
    wrapper.__doc__ = description
    wrapper.__annotations__ = {"request": input_model, "return": output_model}
    server.tool(name=name, description=description, meta={"version": version})(wrapper)


def make_server(name: ServerName, version: str, instructions: str) -> MCPServer:
    return MCPServer(name, version=version, instructions=instructions)
