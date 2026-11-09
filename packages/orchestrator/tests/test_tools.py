import dataclasses
import os
import subprocess
import sys
import time

import pytest
from modelforge_lineage.canonical import canonical_json, sha256_hex
from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.schemas import DatasetRef
from modelforge_mcp.servers.credit_modeling import FitLogisticOutput
from modelforge_mcp.servers.data_profiler import (
    LoadDatasetInput,
    LoadDatasetOutput,
    ProfileDatasetInput,
)
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.tools import (
    InProcessToolClient,
    StdioToolClient,
    ToolError,
    extract_refs,
    parse_error,
)


def _synthetic() -> LoadDatasetInput:
    return LoadDatasetInput(dataset_ref=DatasetRef(adapter="synthetic_credit"))


def _surviving_servers() -> list[str]:
    out = subprocess.run(["pgrep", "-fl", "modelforge_mcp"], capture_output=True, text=True).stdout
    me = str(os.getpid())
    return [line for line in out.splitlines() if "modelforge_mcp" in line and line.split()[0] != me]


def test_in_process_call_returns_typed_output_and_refs(settings: Settings) -> None:
    with InProcessToolClient(settings, servers=["data-profiler"]) as client:
        call = client.call("data-profiler", "load_dataset", _synthetic())
    assert isinstance(call.output, LoadDatasetOutput)
    assert [r.kind for r in call.refs] == ["dataset", "column_dictionary"]
    expected = sha256_hex(
        canonical_json({"dataset_ref": {"adapter": "synthetic_credit", "seed": 42}})
    )
    assert call.version == "1.0.0" and call.input_hash == expected and call.duration_ms >= 0


def test_typed_error_is_not_retried(settings: Settings) -> None:
    with InProcessToolClient(settings, servers=["data-profiler"], backoff_s=(0.01, 0.01)) as client:
        t0 = time.monotonic()
        with pytest.raises(ToolError) as exc:
            client.call(
                "data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id="0" * 64)
            )
    assert exc.value.code == "artifact_not_found" and exc.value.server == "data-profiler"
    assert time.monotonic() - t0 < 1.0


def test_stdio_client_calls_real_subprocess(settings: Settings) -> None:
    with StdioToolClient(settings) as client:
        call = client.call("data-profiler", "load_dataset", _synthetic())
        assert isinstance(call.output, LoadDatasetOutput)
        again = client.call(
            "data-profiler",
            "profile_dataset",
            ProfileDatasetInput(dataset_id=call.output.dataset.id),
        )
    assert again.refs[0].kind == "profile"
    assert _surviving_servers() == []


def test_dead_server_is_transport_error_and_no_orphans(settings: Settings) -> None:
    # The command dies before the MCP handshake.
    dead = dataclasses.replace(
        settings, mcp_command=(sys.executable, "-c", "import sys; sys.exit(3)")
    )
    with pytest.raises(ToolError) as exc, StdioToolClient(dead, backoff_s=(0.01,)) as client:
        client.call("data-profiler", "load_dataset", _synthetic())
    assert exc.value.code == "transport"
    assert _surviving_servers() == []


def test_parse_error_and_extract_refs() -> None:
    text = 'Error executing tool x: {"code": "invalid_input", "message": "bad"}'
    assert parse_error(text) == ("invalid_input", "bad")
    assert (
        parse_error("Error executing tool x: 1 validation error for xArguments")[0]
        == "invalid_input"
    )
    assert parse_error("boom")[0] == "internal"
    ref = ArtifactRef(id="a" * 64, kind="scorecard", media_type="application/json", size_bytes=1)
    assert extract_refs(FitLogisticOutput(scorecard=ref, selection=ref)) == [ref, ref]
    assert extract_refs(ref) == [ref]


def test_tool_error_str_and_dict() -> None:
    err = ToolError(server="s", tool="t", version="1", code="timeout", message="slow")
    assert str(err) == "s/t@1 timeout: slow"
    assert err.to_dict() == {
        "server": "s",
        "tool": "t",
        "version": "1",
        "code": "timeout",
        "message": "slow",
    }
