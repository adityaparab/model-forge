"""The Polish brief end to end through the CLI on the real data: three gates, replay, verify."""

import json

import pytest

from .conftest import REPO, Cli, assert_llm_outputs, run_id_from

POLISH = REPO / "examples/briefs/polish-pd.yaml"


@pytest.mark.slow
@pytest.mark.db
def test_polish_brief_runs_to_completion_and_replays(cli: Cli) -> None:
    out = cli("run", str(POLISH), "--requester", "alice")
    assert out.returncode == 0, out.stderr
    run_id = run_id_from(out.stdout)
    for gate, decision, approver in (
        ("scope", "approve", "alice"),
        ("model", "accept", "bob"),
        ("signoff", "approve", "bob"),
    ):
        result = cli("approve", run_id, gate, "--decision", decision, "--approver", approver)
        assert result.returncode == 0, result.stderr
    status = json.loads(cli("status", run_id, "--json").stdout)
    assert status["run"]["status"] == "completed"
    assert status["refs"]["discrimination"]["summary"]["auc"]["oot"] > 0.65
    assert status["refs"]["validation"]["summary"]["overall"] == "fail"
    assert_llm_outputs(status)
    plain = cli("status", run_id)
    assert plain.returncode == 0 and any(
        line.startswith("cost:") for line in plain.stdout.splitlines()
    ), plain.stdout
    replay = cli("replay", run_id, "--json")
    assert replay.returncode == 0 and json.loads(replay.stdout)["ok"] is True, replay.stderr
    assert cli("verify", run_id).returncode == 0
