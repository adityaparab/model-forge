import json
from pathlib import Path

import psycopg
import pytest
from modelforge_orchestrator.config import Settings

from .conftest import REPO, Cli, run_id_from

SYNTHETIC = REPO / "examples/briefs/synthetic.yaml"


@pytest.mark.db
def test_cli_run_unknown_adapter_exits_2(cli: Cli, tmp_path: Path) -> None:
    brief = tmp_path / "b.yaml"
    brief.write_text(SYNTHETIC.read_text().replace("synthetic_credit", "nope"))
    result = cli("run", str(brief))
    assert result.returncode == 2 and "nope" in result.stderr
    assert result.stdout == ""


@pytest.mark.db
def test_cli_run_invalid_brief_exits_2(cli: Cli, tmp_path: Path) -> None:
    brief = tmp_path / "b.yaml"
    brief.write_text("model_name: [unclosed\n")
    result = cli("run", str(brief))
    assert result.returncode == 2 and "brief" in result.stderr


@pytest.mark.db
def test_cli_approve_wrong_gate_exits_2_and_four_eyes(cli: Cli) -> None:
    result = cli("run", str(SYNTHETIC), "--requester", "alice", "--detach")
    assert result.returncode == 0, result.stderr
    run_id = run_id_from(result.stdout)
    approve = cli("approve", run_id, "model", "--decision", "accept", "--approver", "bob")
    assert approve.returncode == 2  # queued, not awaiting
    assert "not awaiting gate model" in approve.stderr
    status = cli("status", run_id)
    assert status.returncode == 0, status.stderr
    assert status.stdout.splitlines()[0].endswith("queued")


@pytest.mark.db
def test_cli_status_json_and_unknown_run(cli: Cli) -> None:
    run_id = run_id_from(cli("run", str(SYNTHETIC), "--requester", "alice", "--detach").stdout)
    status = cli("status", run_id, "--json")
    assert status.returncode == 0, status.stderr
    document = json.loads(status.stdout)
    assert document["run"]["id"] == run_id and document["run"]["status"] == "queued"
    assert document["steps"] == [] and document["refs"] == {}
    assert cli("status", "run_000000000000").returncode == 2


@pytest.mark.db
def test_cli_resume_and_abort(cli: Cli, settings: Settings) -> None:
    run_id = run_id_from(cli("run", str(SYNTHETIC), "--requester", "alice", "--detach").stdout)
    resume = cli("resume", run_id)
    assert resume.returncode == 2 and "needs_attention" in resume.stderr
    assert cli("abort", run_id).returncode == 0
    assert cli("status", run_id).stdout.splitlines()[0] == f"{run_id} aborted"
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("update runs set status = 'completed' where id = %s", (run_id,))
    assert cli("abort", run_id).returncode == 2


@pytest.mark.db
def test_cli_replay_of_unsealed_run_exits_2(cli: Cli) -> None:
    run_id = run_id_from(cli("run", str(SYNTHETIC), "--requester", "alice", "--detach").stdout)
    result = cli("replay", run_id)
    assert result.returncode == 2 and "only a completed or rejected run replays" in result.stderr


@pytest.mark.db
def test_cli_verify_detects_tamper(cli: Cli, completed_cli_run: str, settings: Settings) -> None:
    assert cli("verify", completed_cli_run).returncode == 0
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(
            "update events set payload = '{}' where run_id = %s and type = 'run.created'",
            (completed_cli_run,),
        )
    result = cli("verify", completed_cli_run, "--json")
    assert result.returncode == 1 and json.loads(result.stdout)["chain"]["ok"] is False
