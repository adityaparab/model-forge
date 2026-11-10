"""The ``modelforge-evals`` command line, run as a real subprocess against the test database."""

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
import typer
from modelforge_evals import cli as evals_cli
from modelforge_evals.defects import DefectCase
from modelforge_evals.probes import run_probes
from modelforge_evals.report import EvalReport
from modelforge_gateway.schemas import GatewayError
from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.steps import list_artifacts
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps

from .conftest import REPO, SYNTHETIC_BRIEF, queued_run

BASELINE = REPO / "evals/baselines/fake.json"

Cli = Callable[..., "subprocess.CompletedProcess[str]"]


def _env(settings: Settings) -> dict[str, str]:
    """The ``MODELFORGE_*`` variables pointing the CLI at the test database and directories."""
    return {
        "MODELFORGE_DATABASE_URL": settings.database_url,
        "MODELFORGE_ARTIFACT_DIR": str(settings.artifact_dir),
        "MODELFORGE_DATA_DIR": str(settings.data_dir),
        "MODELFORGE_TEMPLATES_DIR": str(settings.templates_dir),
        "MODELFORGE_PROFILE": settings.profile,
        "MODELFORGE_KNOWLEDGE_DIR": str(settings.knowledge_dir),
        "MODELFORGE_PROMPTS_DIR": str(settings.gateway.prompts_dir),
        "MODELFORGE_PROFILES_DIR": str(settings.gateway.profiles_dir),
    }


@pytest.fixture
def cli(settings: Settings, checkpointer: object) -> Cli:
    """``cli(*args, env=None)`` runs ``uv run modelforge-evals *args`` on the test database."""
    del checkpointer  # the checkpoint tables must exist before the CLI drives a run
    base_env = {
        **{k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"},
        **_env(settings),
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


def _run_count(database: Database) -> int:
    with database.connection() as conn:
        return len(runs.list_runs(conn, limit=1000))


@pytest.mark.db
def test_evals_run_checks_the_baseline_before_any_layer(
    cli: Cli, database: Database, tmp_path: Path
) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"profile": "fake"}')
    blocked = tmp_path / "file"
    blocked.write_text("")
    before = _run_count(database)
    for args, needle in (
        (("--baseline", str(tmp_path / "missing.json")), "missing.json"),
        (("--baseline", str(malformed)), "metrics"),
        (("--write-baseline", str(blocked / "baseline.json")), "baseline.json"),
    ):
        result = cli("run", "--profile", "fake", *args, "--report-dir", str(tmp_path / "r"))
        assert result.returncode == 2, result.stdout + result.stderr
        assert needle in result.stderr and "Traceback" not in result.stderr
    assert _run_count(database) == before
    assert not (tmp_path / "r").exists()


@pytest.mark.db
def test_evals_run_warns_when_the_step_summary_cannot_be_written(cli: Cli, tmp_path: Path) -> None:
    result = cli(
        "run",
        "--profile",
        "fake",
        "--layers",
        "retrieval",
        "--report-dir",
        str(tmp_path / "reports"),
        env={"GITHUB_STEP_SUMMARY": str(tmp_path / "absent" / "summary.md")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning: cannot append" in result.stderr and "No regressions." in result.stdout


@pytest.mark.db
def test_evals_run_exits_2_when_a_probe_fails(
    settings: Settings,
    database: Database,
    checkpointer: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """In process: the challenger of the first probe raises a provider `GatewayError`."""
    del checkpointer
    for name, value in _env(settings).items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def failing_probes(deps: Deps, base_run_id: str, cases: list[DefectCase]) -> object:
        def fail(*args: object, **kwargs: object) -> object:
            raise GatewayError("provider", "planted provider failure")

        monkeypatch.setattr(deps.gateway, "complete", fail)
        return run_probes(deps, base_run_id, cases)

    monkeypatch.setattr(evals_cli, "run_probes", failing_probes)
    with pytest.raises(typer.Exit) as exited:
        evals_cli.run(
            profile="fake",
            report_dir=tmp_path / "reports",
            config=REPO / "evals/evals.yml",
        )
    assert exited.value.exit_code == 2
    err = capsys.readouterr().err.strip().splitlines()[-1]
    assert "GatewayError" in err and "planted provider failure" in err
    pipeline_id = re.search(r"pipeline run (run_\w+)", err)
    probe_id = re.search(r"probe run (run_\w+)", err)
    assert pipeline_id is not None and probe_id is not None, err
    with database.connection() as conn:
        pipeline = runs.get_run(conn, pipeline_id.group(1))
        probe = runs.get_run(conn, probe_id.group(1))
    assert pipeline.status == "completed" and pipeline.requester != "evals"
    assert probe.status == "needs_attention" and probe.requester == "evals"
    assert probe.brief_hash == pipeline.brief_hash
    assert not (tmp_path / "reports").exists()


def _pipeline_run_count(database: Database) -> int:
    """Runs other than the probe runs (requester ``evals``)."""
    with database.connection() as conn:
        return sum(r.requester != "evals" for r in runs.list_runs(conn, limit=1000))


@pytest.mark.db
def test_evals_run_with_run_id_evaluates_an_existing_run(
    cli: Cli, database: Database, completed_run_id: str, tmp_path: Path
) -> None:
    reports = tmp_path / "reports"
    before = _pipeline_run_count(database)
    result = cli(
        "run",
        "--profile",
        "fake",
        "--run-id",
        completed_run_id,
        "--report-dir",
        str(reports),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert [p.name for p in _reports(reports)] == [f"fake-{completed_run_id}.json"]
    report = EvalReport.model_validate_json((reports / f"fake-{completed_run_id}.json").read_text())
    assert report.run_id == completed_run_id
    assert report.approvals == ["scope approve alice", "model accept bob", "signoff approve bob"]
    assert report.brief == "synthetic" and len(report.probe_run_ids) > 0
    assert _pipeline_run_count(database) == before
    with database.connection() as conn:
        kinds = [a.kind for a in list_artifacts(conn, completed_run_id)]
    assert kinds.count("eval_report") == 1


@pytest.mark.db
def test_evals_run_with_run_id_rejects_unsealed_or_mismatched_runs(
    cli: Cli,
    database: Database,
    store: ArtifactStore,
    completed_run_id: str,
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    queued = queued_run(database, store, SYNTHETIC_BRIEF)
    result = cli("run", "--profile", "fake", "--run-id", queued, "--report-dir", str(reports))
    assert result.returncode == 2, result.stdout + result.stderr
    assert f"run {queued} is queued, not completed" in result.stderr
    assert "parked" not in result.stderr
    assert "Traceback" not in result.stderr
    mismatch = cli(
        "run", "--profile", "default", "--run-id", completed_run_id, "--report-dir", str(reports)
    )
    assert mismatch.returncode == 2, mismatch.stdout + mismatch.stderr
    assert completed_run_id in mismatch.stderr and "fake" in mismatch.stderr
    assert "Traceback" not in mismatch.stderr
    unknown = cli("run", "--profile", "fake", "--run-id", "run_nope", "--report-dir", str(reports))
    assert unknown.returncode == 2 and "unknown run 'run_nope'" in unknown.stderr
    other_brief = cli(
        "run",
        "--profile",
        "fake",
        "--brief",
        "polish",
        "--run-id",
        completed_run_id,
        "--report-dir",
        str(reports),
    )
    assert other_brief.returncode == 2, other_brief.stdout + other_brief.stderr
    assert "different brief" in other_brief.stderr and "'polish'" in other_brief.stderr
    retrieval = cli(
        "run",
        "--profile",
        "fake",
        "--layers",
        "retrieval",
        "--run-id",
        completed_run_id,
        "--report-dir",
        str(reports),
    )
    assert retrieval.returncode == 2 and "pipeline" in retrieval.stderr
    assert not reports.exists()
