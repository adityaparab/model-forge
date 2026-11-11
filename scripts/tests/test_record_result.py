"""Tests for scripts/record_result.py: records from a fixture report, tables and rendering."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import record_result
from record_result import RECORD_KEYS

FIXTURE_REPORT = Path(__file__).parent / "fixtures" / "report-fake.json"


@dataclass
class FakeRun:
    id: str = "run_b4b2b2f8e6d4"
    status: str = "completed"
    profile: str = "fake"
    cost_cap_usd: float = 10.0
    rerun_llm: bool = False


@dataclass
class FakeApproval:
    gate: str
    decision: str
    approver: str


@dataclass
class FakeReplayReport:
    ok: bool


@dataclass
class FakeReplay:
    run: FakeRun
    report: FakeReplayReport | None = field(default=None)


@pytest.fixture
def fixture_report() -> dict[str, Any]:
    return json.loads(FIXTURE_REPORT.read_text())


@pytest.fixture
def fake_run() -> FakeRun:
    return FakeRun()


@pytest.fixture
def fake_approvals() -> list[FakeApproval]:
    return [
        FakeApproval("scope", "approve", "alice"),
        FakeApproval("model", "accept", "bob"),
        FakeApproval("signoff", "approve", "bob"),
    ]


@pytest.fixture
def fake_replays() -> list[FakeReplay]:
    return [
        # An LLM rerun never decides replay_ok; the latest completed plain replay does.
        FakeReplay(FakeRun(id="run_r1", status="completed"), FakeReplayReport(ok=False)),
        FakeReplay(FakeRun(id="run_r2", status="completed"), FakeReplayReport(ok=True)),
        FakeReplay(
            FakeRun(id="run_r3", status="completed", rerun_llm=True), FakeReplayReport(ok=False)
        ),
        FakeReplay(FakeRun(id="run_r4", status="failed"), None),
    ]


@pytest.fixture
def record(fixture_report, fake_run, fake_approvals, fake_replays) -> dict[str, Any]:
    return record_result.build_record(
        fixture_report, fake_run, fake_approvals, fake_replays, label="hosted-default", note=None
    )


@pytest.fixture
def records(record) -> list[dict[str, Any]]:
    second = dict(record, run_id="run_000000000002", label="hosted-other", cost_usd=3.19)
    return [record, second]


@pytest.fixture
def records_with_local(record) -> list[dict[str, Any]]:
    return [dict(record, label="local", cost_usd=0.0)]


def test_build_record_from_report(fixture_report, fake_run, fake_approvals, fake_replays):
    rec = record_result.build_record(
        fixture_report, fake_run, fake_approvals, fake_replays, label="hosted-default", note=None
    )
    assert rec["run_id"] == fixture_report["run_id"] and rec["label"] == "hosted-default"
    assert rec["findings"] == {
        "total": 5,
        "precheck": 3,
        "review": 2,
        "high": 0,
        "medium": 4,
        "low": 1,
        "open_at_signoff": 0,
    }
    assert rec["rounds"] == {"challenge": 2, "remediation": 1} and rec["replay_ok"] is True
    assert set(rec) == RECORD_KEYS
    assert rec["profile"] == "fake" and rec["status"] == "completed"
    assert rec["signoff"] == "approve"
    assert rec["models"]["document"] == "fake/scripted-v1"
    assert rec["orchestrator_version"] == "0.1.0"
    assert rec["grounding_pass_rate"] == 1.0 and rec["completeness_complete"] is True
    assert rec["n_sections"] == 11 and rec["n_sections_regenerated"] == 2
    assert rec["n_llm_calls"] == 35 and rec["n_schema_retries"] == 0
    assert rec["input_tokens"] == 74564 and rec["output_tokens"] == 3424
    assert rec["cost_usd"] == 0.0
    assert rec["cost_by_purpose"] == {
        "challenge": 0.0,
        "document": 0.0,
        "remediate": 0.0,
        "scope": 0.0,
    }
    assert rec["cost_cap_usd"] == 10.0
    assert rec["wall_time_s"] == 17.177428
    assert rec["wall_time_by_node_s"]["scope"] == 0.064052
    assert len(rec["wall_time_by_node_s"]) == 14
    assert rec["scope_exact_match"] is True and rec["retrieval_recall_at_5"] == 1.0
    assert rec["note"] is None
    assert rec["recorded_at"].endswith("Z")


def test_replay_ok_is_none_without_a_plain_replay(fixture_report, fake_run, fake_approvals):
    rec = record_result.build_record(
        fixture_report, fake_run, fake_approvals, [], label="x", note="no replay yet"
    )
    assert rec["replay_ok"] is None and rec["note"] == "no replay yet"


def test_save_is_sorted_and_stable(tmp_path, record):
    path = tmp_path / "runs.json"
    record_result.save_records(path, record_result.upsert([], record))
    text = path.read_text()
    assert text.endswith("]\n") and not text.endswith("\n\n")
    assert text == json.dumps([record], indent=2, sort_keys=True) + "\n"
    loaded = record_result.load_records(path)
    assert loaded == [record]
    replaced = record_result.upsert(loaded, dict(record, label="relabelled"))
    record_result.save_records(path, replaced)
    again = record_result.load_records(path)
    assert len(again) == 1 and again[0]["label"] == "relabelled"
    other = dict(record, run_id="run_other")
    assert [r["run_id"] for r in record_result.upsert(again, other)] == [
        record["run_id"],
        "run_other",
    ]


def test_load_missing_file_is_empty(tmp_path):
    assert record_result.load_records(tmp_path / "absent.json") == []


def _data_rows(table: str) -> list[str]:
    return [line for line in table.splitlines() if line.startswith("| ") and "| ---" not in line]


def test_table_parity_has_one_row_per_record(records):
    rows = [
        line
        for line in record_result.table("parity", records).splitlines()
        if line.startswith("| ") and not line.startswith("| ---")
    ]
    assert len(rows) == len(records) + 1  # the header row plus one row per record


def test_table_parity_row_values(record):
    out = record_result.table("parity", [dict(record, cost_usd=3.19)])
    header, row = _data_rows(out)
    assert header.startswith("| Profile | Model | Status |")
    assert row == (
        "| hosted-default | fake/scripted-v1 | completed, approve | 100.0% | yes (0 flagged) "
        "| 0 | 3 / 2; 0 / 4 / 1 | 2 / 1 | 2 | 0 | 35 | 3.19 | 0:17 |"
    )


def test_table_is_stable(records):
    assert record_result.table("parity", records) == record_result.table("parity", records)


def test_table_cost_and_runs(record):
    _, cost = _data_rows(record_result.table("cost", [dict(record, cost_usd=1.5)]))
    assert cost == "| hosted-default | 74564 | 3424 | 0.00 | 0.00 | 0.00 | 0.00 | 1.50 | 10.00 |"
    _, runs = _data_rows(record_result.table("runs", [record]))
    assert runs == (
        f"| run_b4b2b2f8e6d4 | hosted-default | {record['recorded_at'][:10]} | 0.1.0 | yes |"
    )


def test_table_without_records():
    out = record_result.table("parity", [])
    assert "no records yet" in out
    header, row = _data_rows(out)
    assert row.startswith("| no records yet |")
    assert header.count("|") == row.count("|")


def test_table_unknown_name():
    with pytest.raises(ValueError, match="unknown table"):
        record_result.table("nope", [])


def test_local_cost_prints_as_local(records_with_local):
    assert "0 (local)" in record_result.table("parity", records_with_local)


def test_render_replaces_only_between_markers(record):
    text = "# Title\n\nbefore\n<!-- results:runs -->\nstale\n<!-- /results:runs -->\nafter\n"
    out = record_result.render(text, [record])
    expected_block = "<!-- results:runs -->\n" + record_result.table("runs", [record]) + "\n"
    assert out.startswith("# Title\n\nbefore\n" + expected_block)
    assert out.endswith("<!-- /results:runs -->\nafter\n")
    assert "stale" not in out


def test_render_two_blocks_and_unknown_block(record):
    text = (
        "<!-- results:parity --><!-- /results:parity -->\nmid\n"
        "<!-- results:cost -->x<!-- /results:cost -->\n"
    )
    out = record_result.render(text, [record])
    assert record_result.table("parity", [record]) in out
    assert record_result.table("cost", [record]) in out and "\nmid\n" in out
    with pytest.raises(ValueError, match="unknown table"):
        record_result.render("<!-- results:bad -->\n<!-- /results:bad -->\n", [record])


def test_render_check_ignores_trailing_newline(tmp_path, record):
    results = tmp_path / "runs.json"
    record_result.save_records(results, [record])
    rendered = record_result.render("a\n<!-- results:runs -->\n<!-- /results:runs -->\n", [record])
    doc = tmp_path / "doc.md"
    doc.write_bytes(rendered.rstrip("\n").replace("\n", "\r\n").encode())
    assert record_result.main(["render", "--check", str(doc), "--results", str(results)]) == 0


def test_render_check_detects_stale_block(tmp_path, record, capsys):
    results = tmp_path / "runs.json"
    record_result.save_records(results, [record])
    doc = tmp_path / "doc.md"
    doc.write_text("a\n<!-- results:runs -->\nold\n<!-- /results:runs -->\n")
    assert record_result.main(["render", "--check", str(doc), "--results", str(results)]) == 1
    assert "doc.md" in capsys.readouterr().err
    assert "old" in doc.read_text()  # --check never writes
    assert record_result.main(["render", str(doc), "--results", str(results)]) == 0
    assert "old" not in doc.read_text()
    assert record_result.main(["render", "--check", str(doc), "--results", str(results)]) == 0


def test_render_skips_missing_file(tmp_path, capsys):
    results = tmp_path / "runs.json"
    results.write_text("[]\n")
    missing = tmp_path / "absent.md"
    assert record_result.main(["render", "--check", str(missing), "--results", str(results)]) == 0
    assert "absent.md" in capsys.readouterr().err


def test_table_command_prints(tmp_path, capsys):
    results = tmp_path / "runs.json"
    results.write_text("[]\n")
    assert record_result.main(["table", "runs", "--results", str(results)]) == 0
    assert "no records yet" in capsys.readouterr().out


def test_add_runs_harness_when_no_report(
    monkeypatch, tmp_path, fake_run, fake_approvals, fake_replays
):
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    def fake_subprocess_run(argv, check):
        calls.append(argv)
        assert check is True
        out = tmp_path / "evals" / "reports" / "fake-run_b4b2b2f8e6d4.json"
        out.parent.mkdir(parents=True)
        out.write_text(FIXTURE_REPORT.read_text())
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(record_result.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        record_result,
        "load_lineage",
        lambda run_id: (fake_run, fake_approvals, fake_replays),
    )
    results = tmp_path / "runs.json"
    argv = ["add", "run_b4b2b2f8e6d4", "--profile", "fake", "--label", "fake-default"]
    assert record_result.main([*argv, "--results", str(results)]) == 0
    assert calls == [
        [
            "uv",
            "run",
            "modelforge-evals",
            "run",
            "--profile",
            "fake",
            "--run-id",
            "run_b4b2b2f8e6d4",
            "--layers",
            "pipeline,retrieval",
        ]
    ]
    (rec,) = record_result.load_records(results)
    assert rec["label"] == "fake-default" and rec["replay_ok"] is True


def test_add_with_report_skips_harness(
    monkeypatch, tmp_path, fake_run, fake_approvals, fake_replays
):
    def no_subprocess(*args, **kwargs):
        raise AssertionError("harness must not run when --report is given")

    monkeypatch.setattr(record_result.subprocess, "run", no_subprocess)
    monkeypatch.setattr(
        record_result, "load_lineage", lambda run_id: (fake_run, fake_approvals, fake_replays)
    )
    results = tmp_path / "runs.json"
    args = ["add", "run_b4b2b2f8e6d4", "--profile", "fake", "--label", "l"]
    args += ["--report", str(FIXTURE_REPORT), "--note", "n", "--results", str(results)]
    assert record_result.main(args) == 0
    (rec,) = record_result.load_records(results)
    assert rec["note"] == "n"


def test_add_refuses_mismatched_report(monkeypatch, tmp_path, fake_run):
    monkeypatch.setattr(record_result, "load_lineage", lambda run_id: (fake_run, [], []))
    args = ["add", "run_other", "--profile", "fake", "--label", "l"]
    args += ["--report", str(FIXTURE_REPORT), "--results", str(tmp_path / "runs.json")]
    with pytest.raises(SystemExit, match="run_b4b2b2f8e6d4"):
        record_result.main(args)
