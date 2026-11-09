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
    assert document["gate_prompt"] is None
    assert cli("status", "run_000000000000").returncode == 2


@pytest.mark.db
def test_cli_resume_and_abort(cli: Cli, settings: Settings) -> None:
    run_id = run_id_from(cli("run", str(SYNTHETIC), "--requester", "alice", "--detach").stdout)
    resume = cli("resume", run_id)
    assert resume.returncode == 2 and "needs_attention" in resume.stderr
    assert cli("abort", run_id).returncode == 0
    assert cli("status", run_id).stdout.splitlines()[0] == f"{run_id} aborted"
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        queued = conn.execute("select 1 from run_queue where run_id = %s", (run_id,)).fetchone()
        assert queued is None  # aborting a queued run releases its queue row
        conn.execute("update runs set status = 'completed' where id = %s", (run_id,))
    assert cli("abort", run_id).returncode == 2


@pytest.mark.db
def test_cli_abort_refuses_running_run(cli: Cli, settings: Settings) -> None:
    run_id = run_id_from(cli("run", str(SYNTHETIC), "--requester", "alice", "--detach").stdout)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("update runs set status = 'running' where id = %s", (run_id,))
    result = cli("abort", run_id)
    assert result.returncode == 2 and "no cancellation" in result.stderr
    assert cli("status", run_id).stdout.splitlines()[0] == f"{run_id} running"


@pytest.mark.db
def test_cli_run_unknown_threshold_exits_2_without_a_run(
    cli: Cli, settings: Settings, tmp_path: Path
) -> None:
    text = SYNTHETIC.read_text()
    assert "thresholds: {}" in text
    brief = tmp_path / "b.yaml"
    brief.write_text(text.replace("thresholds: {}", "thresholds: {auc_minimum: 0.75}"))

    def count() -> int:
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            row = conn.execute("select count(*) from runs").fetchone()
        assert row is not None
        return row[0]

    before = count()
    result = cli("run", str(brief), "--requester", "alice")
    assert result.returncode == 2 and "auc_minimum" in result.stderr
    assert result.stdout == "" and count() == before


@pytest.mark.db
def test_cli_run_records_brief_artifact_and_shows_gate_prompt(cli: Cli, settings: Settings) -> None:
    result = cli("run", str(SYNTHETIC), "--requester", "alice")
    assert result.returncode == 0, result.stderr
    run_id = run_id_from(result.stdout)
    lines = result.stdout.splitlines()
    assert lines[:2] == [f"{run_id} awaiting_scope", "gate: scope"]
    assert lines[2].startswith("summary: {") and '"plan":{' in lines[2]
    status = cli("status", run_id, "--json")
    assert status.returncode == 0, status.stderr
    prompt = json.loads(status.stdout)["gate_prompt"]
    assert prompt["gate"] == "scope" and "plan" in prompt["summary"]
    plain = cli("status", run_id).stdout
    prompt_line = next(line for line in plain.splitlines() if line.startswith("gate prompt: "))
    assert json.loads(prompt_line.removeprefix("gate prompt: "))["gate"] == "scope"
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        row = conn.execute(
            "select a.id from artifacts a join runs r on r.id = a.run_id"
            " where a.run_id = %s and a.kind = 'brief'",
            (run_id,),
        ).fetchone()
        brief_ref = conn.execute("select brief_ref from runs where id = %s", (run_id,)).fetchone()
    assert row is not None and brief_ref is not None and row[0] == brief_ref[0]


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


def _fake_sealed_run(cli: Cli, settings: Settings) -> str:
    """A detached run marked ``completed`` by hand: enough for `create_replay` to accept it."""
    run_id = run_id_from(cli("run", str(SYNTHETIC), "--requester", "alice", "--detach").stdout)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("update runs set status = 'completed' where id = %s", (run_id,))
    return run_id


@pytest.mark.db
def test_cli_replay_detach_json_prints_one_document(cli: Cli, settings: Settings) -> None:
    run_id = _fake_sealed_run(cli, settings)
    result = cli("replay", run_id, "--detach", "--json")
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["report"] is None
    assert document["run"]["replay_of"] == run_id and document["run"]["status"] == "queued"


@pytest.mark.db
def test_cli_replay_without_report_json_exits_1(cli: Cli, settings: Settings) -> None:
    run_id = _fake_sealed_run(cli, settings)
    result = cli("replay", run_id, "--json")
    assert result.returncode == 1
    document = json.loads(result.stdout)
    assert document["report"] is None and document["run"]["replay_of"] == run_id
    assert "produced no replay report" in result.stderr


@pytest.mark.db
def test_cli_approve_missing_plan_file_exits_2(cli: Cli, tmp_path: Path) -> None:
    run_id = run_id_from(cli("run", str(SYNTHETIC), "--requester", "alice", "--detach").stdout)
    missing = tmp_path / "missing-plan.yaml"
    result = cli(
        "approve",
        run_id,
        "scope",
        "--decision",
        "edit",
        "--approver",
        "alice",
        "--plan",
        str(missing),
    )
    assert result.returncode == 2
    assert f"invalid plan {missing}" in result.stderr
    assert "Traceback" not in result.stderr
