import asyncio
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent
from modelforge_core.datasets.download import ChecksumMismatchError
from modelforge_core.schemas import StrictModel
from modelforge_lineage.store import ArtifactNotFound
from modelforge_mcp.common import (
    ServerSettings,
    ToolContext,
    ToolErrorPayload,
    _error_payload,
    fail,
    make_context,
    make_server,
    register_tool,
    settings_from_env,
)
from modelforge_mcp.schemas import DatasetRef
from modelforge_mcp.servers.data_profiler import LoadDatasetInput, ProfileDatasetInput
from structlog.testing import capture_logs


def test_dataset_ref_string_forms() -> None:
    assert DatasetRef.parse("polish_bankruptcy") == DatasetRef(adapter="polish_bankruptcy")
    assert DatasetRef.parse("taiwan_bankruptcy@7").as_string() == "taiwan_bankruptcy@7"
    assert DatasetRef(adapter="x").as_string() == "x"


def test_settings_from_env_defaults_and_overrides(tmp_path: Path) -> None:
    assert settings_from_env({}) == ServerSettings(
        artifact_dir=Path("artifacts"),
        data_dir=Path("data"),
        templates_dir=Path("templates"),
        knowledge_dir=Path("knowledge-base"),
    )
    assert settings_from_env({"MODELFORGE_ARTIFACT_DIR": str(tmp_path)}).artifact_dir == tmp_path


def test_errors_are_typed_payloads(call, settings) -> None:
    r = call("data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id="0" * 64))
    assert r.is_error
    payload = ToolErrorPayload.model_validate_json(r.content[0].text.split(": ", 1)[1])
    assert payload.code == "artifact_not_found"


class _FakeConnectError(Exception):
    pass


_FakeConnectError.__module__ = "httpx"


@pytest.mark.parametrize(
    ("exc", "code", "message"),
    [
        (ArtifactNotFound("ab" * 32), "artifact_not_found", f"artifact {'ab' * 32!r} not found"),
        (
            ChecksumMismatchError("https://example.org/d.zip", "aa", "bb"),
            "dataset_unavailable",
            "ChecksumMismatchError: checksum mismatch for https://example.org/d.zip: "
            "expected sha256 aa, got bb",
        ),
        (_FakeConnectError("refused"), "dataset_unavailable", "_FakeConnectError: refused"),
        (KeyError("unknown dataset 'x'"), "invalid_input", "unknown dataset 'x'"),
        (ValueError("bad share"), "invalid_input", "bad share"),
        (RuntimeError("boom"), "internal", "RuntimeError: boom"),
    ],
)
def test_error_payload_maps_each_exception(exc: Exception, code: str, message: str) -> None:
    assert _error_payload(exc) == ToolErrorPayload.model_validate(
        {"code": code, "message": message}
    )


def test_fail_raises_tool_error_with_payload() -> None:
    with pytest.raises(ToolError) as info:
        fail("internal", "x")
    assert ToolErrorPayload.model_validate_json(str(info.value)).code == "internal"


def test_unknown_adapter_is_invalid_input(call) -> None:
    r = call(
        "data-profiler", "load_dataset", LoadDatasetInput(dataset_ref=DatasetRef(adapter="nope"))
    )
    assert r.is_error
    payload = ToolErrorPayload.model_validate_json(r.content[0].text.split(": ", 1)[1])
    assert payload.code == "invalid_input" and "unknown dataset 'nope'" in payload.message


class _Empty(StrictModel):
    pass


def _passthrough(ctx: ToolContext, request: _Empty) -> _Empty:
    fail("dataset_unavailable", "mirror down")


def _crash(ctx: ToolContext, request: _Empty) -> _Empty:
    raise RuntimeError("boom")


def test_wrapper_passes_tool_errors_through_and_logs_internal_with_traceback(settings) -> None:
    server = make_server("data-profiler", "1.0.0", "test")
    ctx = make_context("data-profiler", settings)
    for name, fn in (("passthrough", _passthrough), ("crash", _crash)):
        register_tool(
            server,
            ctx,
            name=name,
            version="1.0.0",
            description=name,
            fn=fn,
            input_model=_Empty,
            output_model=_Empty,
        )

    async def _run() -> list[str]:
        async with Client(server) as c:
            results = [
                await c.call_tool(name, {"request": {}}) for name in ("passthrough", "crash")
            ]
        texts = [r.content[0] for r in results]
        assert all(isinstance(t, TextContent) for t in texts)
        return [t.text.split(": ", 1)[1] for t in texts if isinstance(t, TextContent)]

    with capture_logs() as logs:
        passthrough, crash = asyncio.run(_run())
    assert ToolErrorPayload.model_validate_json(passthrough) == ToolErrorPayload(
        code="dataset_unavailable", message="mirror down"
    )
    assert ToolErrorPayload.model_validate_json(crash).code == "internal"
    errors = {e["tool"]: e for e in logs if e["event"] == "tool.error"}
    assert errors["passthrough"]["log_level"] == "warning"
    assert "exc_info" not in errors["passthrough"]
    assert errors["crash"]["log_level"] == "error"
    assert isinstance(errors["crash"]["exc_info"], RuntimeError)
