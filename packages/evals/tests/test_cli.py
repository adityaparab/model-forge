"""The ``modelforge-evals`` command line, run as a real subprocess against the test database."""

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from modelforge_evals.report import EvalReport
from modelforge_lineage.db import Database
from modelforge_lineage.steps import list_artifacts
from modelforge_orchestrator.config import Settings

from .conftest import REPO

BASELINE = REPO / "evals/baselines/fake.json"

Cli = Callable[..., "subprocess.CompletedProcess[str]"]


@pytest.fixture
def cli(settings: Settings, checkpointer: object) -> Cli:
    """``cli(*args, env=None)`` runs ``uv run modelforge-evals *args`` on the test database."""
    del checkpointer  # the checkpoint tables must exist before the CLI drives a run
    base_env = {
        **{k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"},
        "MODELFORGE_DATABASE_URL": settings.database_url,
        "MODELFORGE_ARTIFACT_DIR": str(settings.artifact_dir),
        "MODELFORGE_DATA_DIR": str(settings.data_dir),
        "MODELFORGE_TEMPLATES_DIR": str(settings.templates_dir),
        "MODELFORGE_PROFILE": settings.profile,
        "MODELFORGE_KNOWLEDGE_DIR": str(settings.knowledge_dir),
        "MODELFORGE_PROMPTS_DIR": str(settings.gateway.prompts_dir),
        "MODELFORGE_PROFILES_DIR": str(settings.gateway.profiles_dir),
    }

    def run(*args: str, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["uv", "run", "modelforge-evals", *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            env={**base_env, **(env or {})},
            check=False,
        )

    return run


def _reports(report_dir: Path) -> list[Path]:
    return sorted(report_dir.glob("*.json"))


@pytest.mark.db
def test_evals_run_fake_matches_committed_baseline(
    cli: Cli, database: Database, tmp_path: Path
) -> None:
    summary = tmp_path / "step-summary.md"
    reports = tmp_path / "reports"
    result = cli(
        "run",
        "--profile",
        "fake",
        "--baseline",
        str(BASELINE),
        "--report-dir",
        str(reports),
        env={"GITHUB_STEP_SUMMARY": str(summary)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    [path] = _reports(reports)
    report = EvalReport.model_validate_json(path.read_text())
    assert report.run_id is not None and path.name == f"fake-{report.run_id}.json"
    assert len(report.probe_run_ids) == len(report.defect_recall_by_class) > 0
    assert report.approvals == ["scope approve alice", "model accept bob", "signoff approve bob"]
    assert "grounding_pass_rate" in result.stdout and "No regressions." in result.stdout
    assert "grounding_pass_rate" in summary.read_text()
    with database.connection() as conn:
        kinds = [a.kind for a in list_artifacts(conn, report.run_id)]
    assert kinds.count("eval_report") == 1


@pytest.mark.db
def test_evals_run_exits_1_on_regression(cli: Cli, tmp_path: Path) -> None:
    baseline = json.loads(BASELINE.read_text())
    baseline["metrics"]["n_findings"] += 10
    regressed = tmp_path / "baseline.json"
    regressed.write_text(json.dumps(baseline))
    rewritten = tmp_path / "rewritten.json"
    result = cli(
        "run",
        "--profile",
        "fake",
        "--baseline",
        str(regressed),
        "--write-baseline",
        str(rewritten),
        "--report-dir",
        str(tmp_path / "reports"),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "`n_findings`" in result.stdout
    assert not rewritten.exists()  # a regression blocks --write-baseline


@pytest.mark.db
def test_evals_run_retrieval_only_needs_no_database_run(cli: Cli, tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    written = tmp_path / "baseline.json"
    result = cli(
        "run",
        "--profile",
        "fake",
        "--layers",
        "retrieval",
        "--report-dir",
        str(reports),
        "--write-baseline",
        str(written),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert [p.name for p in _reports(reports)] == ["fake-retrieval.json"]
    report = EvalReport.model_validate_json((reports / "fake-retrieval.json").read_text())
    assert report.run_id is None and set(report.metrics) == {"retrieval_recall_at_5"}
    assert json.loads(written.read_text()) == {"profile": "fake", "metrics": report.metrics}


@pytest.mark.db
def test_evals_run_rejects_challenger_without_pipeline(cli: Cli, tmp_path: Path) -> None:
    result = cli(
        "run", "--profile", "fake", "--layers", "challenger", "--report-dir", str(tmp_path)
    )
    assert result.returncode == 2 and "pipeline" in result.stderr
    unknown = cli("run", "--profile", "nope", "--report-dir", str(tmp_path))
    assert unknown.returncode == 2 and "nope" in unknown.stderr
    assert _reports(tmp_path) == []


@pytest.mark.db
def test_evals_compare_reports_regressions(cli: Cli, tmp_path: Path) -> None:
    report = EvalReport(
        profile="fake",
        brief="synthetic",
        run_id=None,
        models={},
        orchestrator_version="0.1.0",
        prompts={},
        metrics={"retrieval_recall_at_5": 0.5},
        defect_recall_by_class={},
        probe_run_ids=[],
        approvals=[],
        misses=[],
    )
    report_path = tmp_path / "report.json"
    report_path.write_text(report.model_dump_json())
    same, worse = tmp_path / "same.json", tmp_path / "worse.json"
    same.write_text(json.dumps({"profile": "fake", "metrics": {"retrieval_recall_at_5": 0.5}}))
    worse.write_text(json.dumps({"profile": "fake", "metrics": {"retrieval_recall_at_5": 1.0}}))

    clean = cli("compare", str(report_path), str(same), "--profile", "fake")
    assert clean.returncode == 0, clean.stdout + clean.stderr
    regressed = cli("compare", str(report_path), str(worse), "--profile", "fake")
    assert regressed.returncode == 1 and "retrieval_recall_at_5" in regressed.stdout
