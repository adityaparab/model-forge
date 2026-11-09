"""End to end through the CLI: real stdio MCP servers, three gates, replay and verify."""

import json
import os
import subprocess

import pytest

from .conftest import REPO, Cli, run_id_from

SYNTHETIC = REPO / "examples/briefs/synthetic.yaml"


@pytest.mark.db
def test_full_run_through_three_gates_then_replay_and_verify(cli: Cli) -> None:
    out = cli("run", str(SYNTHETIC), "--requester", "alice")
    assert out.returncode == 0 and "awaiting_scope" in out.stdout, out.stderr
    run_id = run_id_from(out.stdout)
    wrong_gate = cli("approve", run_id, "model", "--decision", "accept", "--approver", "bob")
    assert wrong_gate.returncode == 2
    out = cli("approve", run_id, "scope", "--decision", "approve", "--approver", "alice")
    assert out.returncode == 0 and "awaiting_model" in out.stdout, out.stderr
    four_eyes = cli("approve", run_id, "model", "--decision", "accept", "--approver", "alice")
    assert four_eyes.returncode == 2
    out = cli("approve", run_id, "model", "--decision", "accept", "--approver", "bob")
    assert out.returncode == 0 and "awaiting_signoff" in out.stdout, out.stderr
    out = cli(
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
    assert out.returncode == 0 and "completed" in out.stdout, out.stderr
    status = cli("status", run_id, "--json")
    assert status.returncode == 0 and json.loads(status.stdout)["run"]["status"] == "completed"
    replay = cli("replay", run_id, "--json")
    assert replay.returncode == 0 and json.loads(replay.stdout)["ok"] is True, replay.stderr
    assert cli("verify", run_id).returncode == 0
    found = subprocess.run(["pgrep", "-f", "--", "--marker"], capture_output=True, text=True)
    survivors = [pid for pid in found.stdout.split() if int(pid) != os.getpid()]
    assert not survivors
