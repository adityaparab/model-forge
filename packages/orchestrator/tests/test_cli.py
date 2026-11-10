import json
import re
import shutil
from collections.abc import Mapping
from pathlib import Path

import psycopg
import pytest
import yaml
from modelforge_lineage import settings as server_settings
from modelforge_lineage.db import Database
from modelforge_lineage.findings import insert_findings, list_findings
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_orchestrator.config import Settings

from .conftest import REPO, Cli, run_id_from

SYNTHETIC = REPO / "examples/briefs/synthetic.yaml"
_FINDING_STATUSES = ("open", "accepted", "disputed", "closed")


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
def test_cli_pause_refuses_unless_running(cli: Cli, settings: Settings, db: Database) -> None:
    del db  # truncated: `worker --once` below must claim this run's row, not another test's
    run_id = run_id_from(cli("run", str(SYNTHETIC), "--requester", "alice", "--detach").stdout)
    refused = cli("pause", run_id)
    assert refused.returncode == 2 and "running" in refused.stderr
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("update runs set status = 'running' where id = %s", (run_id,))
    paused = cli("pause", run_id)
    assert paused.returncode == 0
    assert paused.stdout.splitlines()[0] == f"{run_id} running"
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        row = conn.execute("select pause_requested from runs where id = %s", (run_id,)).fetchone()
        assert row is not None and row[0] is True
        # back to queued so the worker takes its start row and pauses before `intake`
        conn.execute("update runs set status = 'queued' where id = %s", (run_id,))
    assert cli("worker", "--once").returncode == 0
    assert cli("status", run_id).stdout.splitlines()[0] == f"{run_id} paused"
    resumed = cli("resume", run_id)
    assert resumed.returncode == 0
    assert resumed.stdout.splitlines()[0] == f"{run_id} awaiting_scope"


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
    assert lines[:3] == [
        f"{run_id} awaiting_scope",
        "cost: 0.0000 USD / cap 10.00 USD",
        "gate: scope",
    ]
    assert lines[3].startswith("summary: {") and '"plan":{' in lines[3]
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
    assert re.fullmatch(r"[0-9a-f]{32}", document["run"]["trace_id"])


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


@pytest.mark.db
def test_cli_run_rejects_profile_without_credentials(cli: Cli, settings: Settings) -> None:
    def count() -> int:
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            row = conn.execute("select count(*) from runs").fetchone()
        assert row is not None
        return row[0]

    before = count()
    # The empty value overrides any ANTHROPIC_API_KEY in the repository's .env.
    result = cli("run", str(SYNTHETIC), "--profile", "default", env={"ANTHROPIC_API_KEY": ""})
    assert result.returncode == 2 and "ANTHROPIC_API_KEY" in result.stderr
    unknown = cli("run", str(SYNTHETIC), "--profile", "nope")
    assert unknown.returncode == 2 and "nope" in unknown.stderr
    assert result.stdout == "" and unknown.stdout == "" and count() == before


@pytest.mark.db
def test_cli_replay_rerun_llm_regenerates_and_records_calls(
    cli: Cli, completed_cli_run: str, database: Database
) -> None:
    plain_result = cli("replay", completed_cli_run, "--json")
    assert plain_result.returncode == 0, plain_result.stderr
    plain = json.loads(plain_result.stdout)
    assert plain["ok"] and plain["regenerated"] == []
    rerun_result = cli("replay", completed_cli_run, "--rerun-llm", "--json")
    assert rerun_result.returncode == 0, rerun_result.stderr
    rerun = json.loads(rerun_result.stdout)
    assert rerun["ok"]
    assert rerun["regenerated"] == [
        "document_md",
        "document_html",
        "completeness",
        "findings",
        "responses",
    ]
    assert "knowledge_index" in rerun["matched"]
    with database.connection() as conn:
        assert list_llm_calls(conn, plain["replay_run_id"]) == []
        rerun_calls = list_llm_calls(conn, rerun["replay_run_id"])
    assert len(rerun_calls) >= 23
    assert {"challenge", "remediate"} <= {c.purpose for c in rerun_calls}


@pytest.mark.db
def test_cli_status_shows_findings_line_and_json_rows(
    cli: Cli, completed_cli_run: str, database: Database
) -> None:
    with database.connection() as conn:
        rows = list_findings(conn, completed_cli_run)
    assert rows
    plain = cli("status", completed_cli_run)
    assert plain.returncode == 0, plain.stderr
    lines = plain.stdout.splitlines()
    cost = next(i for i, line in enumerate(lines) if line.startswith("cost:"))
    counts = {s: sum(r["status"] == s for r in rows) for s in _FINDING_STATUSES}
    order = ["critical", "high", "medium", "low"]
    worst = min((r["severity"] for r in rows), key=order.index)
    assert lines[cost + 1] == (
        f"findings: {len(rows)} (open {counts['open']}, accepted {counts['accepted']},"
        f" disputed {counts['disputed']}, closed {counts['closed']}; max severity {worst})"
    )
    document = json.loads(cli("status", completed_cli_run, "--json").stdout)
    assert document["findings"] == rows
    assert [f["id"] for f in document["findings"]][0].startswith(f"{completed_cli_run}/F-")
    queued = run_id_from(cli("run", str(SYNTHETIC), "--requester", "alice", "--detach").stdout)
    queued_lines = cli("status", queued).stdout.splitlines()
    assert not any(line.startswith("findings:") for line in queued_lines)
    assert json.loads(cli("status", queued, "--json").stdout)["findings"] == []


@pytest.mark.db
def test_cli_signoff_approve_blocked_by_open_high_finding(cli: Cli, database: Database) -> None:
    result = cli("run", str(SYNTHETIC), "--requester", "alice")
    assert result.returncode == 0, result.stderr
    run_id = run_id_from(result.stdout)
    for args in (
        ("scope", "--decision", "approve", "--approver", "alice"),
        ("model", "--decision", "accept", "--approver", "bob"),
    ):
        result = cli("approve", run_id, *args)
        assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"{run_id} awaiting_signoff"
    with database.transaction() as conn:
        insert_findings(
            conn,
            run_id,
            [
                {
                    "id": f"{run_id}/F-9",
                    "round": 1,
                    "severity": "high",
                    "category": "governance",
                    "title": "Planted high finding",
                    "description": "A finding planted by the test.",
                    "evidence": [],
                    "required_action": "Close it.",
                    "status": "open",
                    "response": None,
                    "response_evidence": [],
                }
            ],
        )
    refused = cli("approve", run_id, "signoff", "--decision", "approve", "--approver", "bob")
    assert refused.returncode == 2
    assert "approve is refused" in refused.stderr and "Planted high finding" in refused.stderr
    assert cli("status", run_id).stdout.splitlines()[0] == f"{run_id} awaiting_signoff"
    conditional = cli(
        "approve",
        run_id,
        "signoff",
        "--decision",
        "approve_with_conditions",
        "--approver",
        "bob",
        "--condition",
        "close F-9",
    )
    assert conditional.returncode == 0, conditional.stderr
    assert conditional.stdout.splitlines()[0] == f"{run_id} completed"


def _priced_profiles(tmp_path: Path) -> Path:
    """A copy of the repo's profiles plus ``fake-priced``: the fake routes at 4/20 USD per Mtok."""
    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)
    fake = yaml.safe_load((profiles / "fake.yaml").read_text())
    priced = {
        "name": "fake-priced",
        "routes": fake["routes"],
        "prices": {
            "scripted-v1": {"input": 4.0, "output": 20.0, "cache_read": 0.0, "cache_write": 0.0}
        },
    }
    (profiles / "fake-priced.yaml").write_text(yaml.safe_dump(priced, sort_keys=False))
    return profiles


@pytest.mark.db
def test_cli_cost_cap_parks_and_resume_raises_cap(cli: Cli, tmp_path: Path) -> None:
    profiles = _priced_profiles(tmp_path)
    env = {"MODELFORGE_PROFILES_DIR": str(profiles), "MODELFORGE_COST_CAP_USD": "0.000001"}
    out = cli("run", str(SYNTHETIC), "--requester", "alice", "--profile", "fake-priced", env=env)
    assert out.returncode == 0, out.stderr
    assert "needs_attention" in out.stdout and "cost_cap" in out.stdout, out.stdout
    run_id = run_id_from(out.stdout)
    assert "cap 0.00 USD" in cli("status", run_id, env=env).stdout
    refused = cli("resume", run_id, "--cost-cap", "0", env=env)
    assert refused.returncode == 2 and "cost cap" in refused.stderr
    resumed = cli("resume", run_id, "--cost-cap", "5", env=env)
    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.splitlines()[0] == f"{run_id} awaiting_scope"
    status = cli("status", run_id, env=env).stdout
    assert "cap 5.00 USD" in status
    document = json.loads(cli("status", run_id, "--json", env=env).stdout)
    assert document["llm_calls"]


def _drive_through_gates(cli: Cli, env: Mapping[str, str]) -> str:
    result = cli("run", str(SYNTHETIC), "--requester", "alice", env=env)
    assert result.returncode == 0, result.stderr
    run_id = run_id_from(result.stdout)
    for args in (
        ("scope", "--decision", "approve", "--approver", "alice"),
        ("model", "--decision", "accept", "--approver", "bob"),
        ("signoff", "--decision", "approve", "--approver", "bob"),
    ):
        result = cli("approve", run_id, *args, env=env)
        assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"{run_id} completed"
    return run_id


@pytest.mark.db
def test_replay_detects_corpus_change(cli: Cli, tmp_path: Path) -> None:
    corpus = tmp_path / "kb"
    shutil.copytree(REPO / "knowledge-base", corpus)
    env = {"MODELFORGE_KNOWLEDGE_DIR": str(corpus)}
    run_id = _drive_through_gates(cli, env)
    governance = corpus / "modelforge-governance.md"
    governance.write_text(governance.read_text() + "\n\nAn extra paragraph for the drift test.\n")
    replay = cli("replay", run_id, "--json", env=env)
    assert replay.returncode == 1, replay.stderr
    report = json.loads(replay.stdout)
    assert any(m["ref"] == "knowledge_index" for m in report["mismatched"])


@pytest.mark.db
def test_cli_migrate_seeds_settings(cli: Cli, db: Database) -> None:
    first = cli("migrate", env={"MODELFORGE_AUTO_REPLAY": "0", "MODELFORGE_COST_CAP_USD": "4"})
    assert first.returncode == 0, first.stderr
    assert "seeded: default_profile, default_cost_cap_usd, auto_replay" in first.stdout
    with db.connection() as conn:
        assert server_settings.get_all(conn) == {
            "default_profile": "fake",
            "default_cost_cap_usd": 4.0,
            "auto_replay": False,
        }
    second = cli("migrate", env={"MODELFORGE_AUTO_REPLAY": "1"})
    assert second.returncode == 0, second.stderr
    assert "seeded: none" in second.stdout
    with db.connection() as conn:
        assert server_settings.get(conn, "auto_replay") is False
