"""End to end through the CLI: real stdio MCP servers, three gates, replay and verify."""

import json
import subprocess

import pytest

from .conftest import REPO, Cli, assert_llm_outputs, run_id_from

SYNTHETIC = REPO / "examples/briefs/synthetic.yaml"
UNREGISTERED = "Deserializing unregistered type"


def _server_pids() -> set[int]:
    """Pids of live MCP server subprocesses started by any `StdioToolClient` on this machine."""
    found = subprocess.run(
        ["pgrep", "-f", "modelforge_mcp.*--marker"], capture_output=True, text=True
    )
    return {int(pid) for pid in found.stdout.split()}


def _parent_pid(pid: int) -> int | None:
    found = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    return int(found.stdout) if found.stdout.strip() else None


@pytest.mark.db
def test_full_run_through_three_gates_then_replay_and_verify(cli: Cli) -> None:
    servers_before = _server_pids()
    out = cli("run", str(SYNTHETIC), "--requester", "alice")
    assert out.returncode == 0 and "awaiting_scope" in out.stdout, out.stderr
    run_id = run_id_from(out.stdout)
    wrong_gate = cli("approve", run_id, "model", "--decision", "accept", "--approver", "bob")
    assert wrong_gate.returncode == 2
    scope = cli("approve", run_id, "scope", "--decision", "approve", "--approver", "alice")
    assert scope.returncode == 0 and "awaiting_model" in scope.stdout, scope.stderr
    four_eyes = cli("approve", run_id, "model", "--decision", "accept", "--approver", "alice")
    assert four_eyes.returncode == 2
    model = cli("approve", run_id, "model", "--decision", "accept", "--approver", "bob")
    assert model.returncode == 0 and "awaiting_signoff" in model.stdout, model.stderr
    signoff = cli(
        "approve",
        run_id,
        "signoff",
        "--decision",
        "approve_with_conditions",
        "--approver",
        "bob",
        "--condition",
        "monitor PSI quarterly",
    )
    assert signoff.returncode == 0 and "completed" in signoff.stdout, signoff.stderr
    status = cli("status", run_id, "--json")
    assert status.returncode == 0 and json.loads(status.stdout)["run"]["status"] == "completed"
    for result in (scope, model, signoff, status):
        assert UNREGISTERED not in result.stderr
    assert_llm_outputs(json.loads(status.stdout))
    plain = cli("status", run_id)
    assert plain.returncode == 0 and any(
        line.startswith("cost:") for line in plain.stdout.splitlines()
    ), plain.stdout
    replay = cli("replay", run_id, "--json")
    assert replay.returncode == 0 and json.loads(replay.stdout)["ok"] is True, replay.stderr
    assert cli("verify", run_id).returncode == 0
    # Every CLI subprocess has exited; a server it started and left behind is re-parented to
    # init. Servers of concurrent sessions either predate this test or still have a live parent.
    orphans = [pid for pid in _server_pids() - servers_before if _parent_pid(pid) == 1]
    assert not orphans
