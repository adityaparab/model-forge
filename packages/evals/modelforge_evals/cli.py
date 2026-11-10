"""The ``modelforge-evals`` command line: run a profile's eval layers, compare with a baseline.

Exit codes: 0 clean, 1 a regression against the baseline or a budget exceeded, 2 a
configuration error (bad ``evals.yml``, unknown profile, brief or layer, ``challenger``
without ``pipeline``, an unreadable baseline), a pipeline run that parked, or any other
failure while the layers ran (a provider error or cost cap in a probe, ...).
"""

import json
import os
from pathlib import Path
from typing import Annotated, Any, NoReturn, get_args

import typer
from modelforge_lineage import runs, steps
from modelforge_lineage.approvals import list_approvals
from modelforge_lineage.db import Database
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.observability import configure_logging
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator import __version__ as orchestrator_version
from modelforge_orchestrator.config import Settings, load_settings
from modelforge_orchestrator.schemas import Finding, FindingsList, Manifest

from modelforge_evals.config import EvalConfigError, EvalsConfig, Layer, load_config
from modelforge_evals.defects import defect_metrics, load_defects
from modelforge_evals.harness import EvalError, build_deps, drive_run
from modelforge_evals.metrics import flatten, retrieval_recall_at_5, run_metrics
from modelforge_evals.probes import run_probes
from modelforge_evals.report import (
    EvalReport,
    MetricValue,
    Regression,
    baseline_of,
    compare,
    markdown_summary,
)

__all__ = ["app", "run_eval"]

app = typer.Typer(no_args_is_help=True, add_completion=False, help="ModelForge evals.")

LAYER_ORDER: tuple[Layer, ...] = ("retrieval", "pipeline", "challenger", "cost")
DEFAULT_CONFIG = Path("evals/evals.yml")

ConfigOption = Annotated[Path, typer.Option("--config", help="The evals configuration.")]


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=2)


def _layers(spec_layers: list[Layer], option: str | None) -> list[Layer]:
    """The layers to run, in `LAYER_ORDER`: ``--layers`` when given, else the profile's."""
    names: list[str] = (
        [n.strip() for n in option.split(",") if n.strip()] if option else list(spec_layers)
    )
    unknown = sorted(set(names) - set(get_args(Layer)))
    if unknown:
        raise EvalConfigError(f"unknown layer(s) {', '.join(unknown)}; known: {LAYER_ORDER}")
    chosen: list[Layer] = [layer for layer in LAYER_ORDER if layer in names]
    if "challenger" in chosen and "pipeline" not in chosen:
        raise EvalConfigError("the challenger layer probes the pipeline run; add 'pipeline'")
    return chosen


def _run_identity(db: Database, run_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """Purpose to ``provider/model`` and ``name@version`` to prompt hash, from ``llm_calls``."""
    with db.connection() as conn:
        calls = list_llm_calls(conn, run_id)
    models = {c.purpose: f"{c.provider}/{c.model}" for c in calls}
    prompts = {f"{c.prompt_name}@{c.prompt_version}": c.prompt_hash for c in calls}
    return dict(sorted(models.items())), dict(sorted(prompts.items()))


def _existing_run(db: Database, run_id: str, profile: str) -> list[str]:
    """Check that ``run_id`` is a completed, sealed run under ``profile``; its decisions.

    `EvalError` unless it is completed with a manifest; `EvalConfigError` when it ran under
    another profile. The decisions are those recorded on the run, in order.
    """
    with db.connection() as conn:
        run = runs.get_run(conn, run_id)
        recorded = [a.decision for a in list_approvals(conn, run_id)]
    if run.status != "completed" or run.manifest_hash is None:
        sealed = "sealed" if run.manifest_hash is not None else "not sealed"
        raise EvalError(
            f"run {run_id} is {run.status} ({sealed}); --run-id needs a completed, sealed run"
        )
    if run.profile != profile:
        raise EvalConfigError(
            f"run {run_id} ran under profile {run.profile!r}, not {profile!r}; "
            "--run-id needs a run under --profile"
        )
    return recorded


def _challenger_metrics(
    settings: Settings, db: Database, store: ArtifactStore, run_id: str, defects: Path
) -> tuple[dict[str, MetricValue], dict[str, float | None], list[str]]:
    """Probe the run's document with every planted defect; metrics, recall by class, probe ids.

    The probes run under the profile the pipeline run recorded.
    """
    with db.connection() as conn:
        run = runs.get_run(conn, run_id)
    assert run.manifest_hash is not None
    manifest = store.get_model(run.manifest_hash, Manifest)
    known = (
        store.get_model(manifest.refs["findings"].id, FindingsList).findings
        if "findings" in manifest.refs
        else []
    )
    cases = load_defects(defects)
    deps = build_deps(settings, db, store, run.profile)
    with deps.tools:
        results = run_probes(deps, run_id, cases)
    outcomes: dict[str, tuple[bool | None, list[Finding]]] = {
        r.defect_id: (r.applicable, r.findings) for r in results
    }
    by_class, recall, precision = defect_metrics(cases, outcomes, known)
    metrics: dict[str, MetricValue] = {
        "defect_recall": recall,
        "defect_precision": precision,
        "probe_cost_usd": sum(r.cost_usd for r in results),
    }
    metrics |= {f"defect_recall_by_class.{name}": value for name, value in by_class.items()}
    return metrics, by_class, [r.run_id for r in results]


def run_eval(
    settings: Settings,
    config: EvalsConfig,
    config_path: Path,
    profile: str,
    *,
    brief: str | None = None,
    layers: str | None = None,
    run_id: str | None = None,
) -> EvalReport:
    """Run ``profile``'s layers and build its report; nothing is stored or written.

    With ``run_id`` the ``pipeline`` layer evaluates that existing run instead of driving a
    new one, and the report's approvals are the decisions recorded on it.

    `EvalConfigError` for an unknown profile, brief or layer, ``challenger`` without
    ``pipeline``, ``run_id`` without ``pipeline`` or a ``run_id`` under another profile;
    `EvalError` when the pipeline run parks or ``run_id`` is not completed and sealed.
    """
    spec = config.profiles.get(profile)
    if spec is None:
        raise EvalConfigError(f"unknown profile {profile!r}; known: {sorted(config.profiles)}")
    brief_key = brief or spec.brief
    if brief_key not in config.briefs:
        raise EvalConfigError(f"unknown brief {brief_key!r}; known: {sorted(config.briefs)}")
    chosen = _layers(spec.layers, layers)
    if run_id is not None and "pipeline" not in chosen:
        raise EvalConfigError("--run-id evaluates a pipeline run; add the 'pipeline' layer")
    goldens = config_path.parent / "goldens"

    metrics: dict[str, MetricValue] = {}
    misses: list[str] = []
    recorded: list[str] = []
    if run_id is not None:  # checked before any layer runs
        recorded = _existing_run(Database(settings.database_url), run_id, profile)
    models: dict[str, str] = {}
    prompts: dict[str, str] = {}
    by_class: dict[str, float | None] = {}
    probe_ids: list[str] = []
    if "retrieval" in chosen:
        recall, misses = retrieval_recall_at_5(settings.knowledge_dir, goldens / "retrieval.yaml")
        metrics["retrieval_recall_at_5"] = recall
    if "pipeline" in chosen:
        db, store = Database(settings.database_url), ArtifactStore(settings.artifact_dir)
        if run_id is None:
            driven = drive_run(
                settings,
                db,
                store,
                config.briefs[brief_key],
                profile=profile,
                approvals=config.approvals,
            )
            run_id, recorded = driven.run_id, driven.approvals
        metrics |= flatten(run_metrics(db, store, run_id))
        models, prompts = _run_identity(db, run_id)
        if "challenger" in chosen:
            try:
                found, by_class, probe_ids = _challenger_metrics(
                    settings, db, store, run_id, goldens / "defects.yaml"
                )
            except Exception as exc:
                exc.add_note(f"pipeline run {run_id}")
                raise
            metrics |= found
    return EvalReport(
        profile=profile,
        brief=brief_key,
        run_id=run_id,
        models=models,
        orchestrator_version=orchestrator_version,
        prompts=prompts,
        metrics=dict(sorted(metrics.items())),
        defect_recall_by_class=by_class,
        probe_run_ids=probe_ids,
        approvals=recorded,
        misses=misses,
    )


def _record(settings: Settings, report: EvalReport, report_dir: Path) -> Path:
    """Store the report as an ``eval_report`` artifact on its run (if any); write its file."""
    if report.run_id is not None:
        store = ArtifactStore(settings.artifact_dir)
        ref = store.put_model(report, kind="eval_report")
        with Database(settings.database_url).transaction() as conn:
            steps.insert_artifact(
                conn,
                run_id=report.run_id,
                ref=ref,
                uri=store.uri(ref.id),
                producer_step_id=None,
            )
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{report.profile}-{report.run_id or 'retrieval'}.json"
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _summarize(report: EvalReport, regressions: list[Regression], *, as_json: bool) -> None:
    summary = markdown_summary(report, regressions)
    typer.echo(report.model_dump_json(indent=2) if as_json else summary, nl=not as_json)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with Path(step_summary).open("a", encoding="utf-8") as handle:
                handle.write(summary)
        except OSError as exc:
            typer.echo(f"warning: cannot append to GITHUB_STEP_SUMMARY: {exc}", err=True)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"cannot read {path}: {exc}")


def _read_baseline(path: Path) -> dict[str, Any]:
    """The baseline at ``path``; exit 2 unless it is a mapping with a ``metrics`` mapping."""
    document = _read_json(path)
    if not isinstance(document, dict) or not isinstance(document.get("metrics"), dict):
        _fail(f"{path} is not a baseline: expected a mapping with a 'metrics' mapping")
    return dict(document)


def _failure(exc: Exception) -> str:
    """``exc`` on one line, with its notes (the pipeline and probe runs it concerns)."""
    notes = "; ".join(getattr(exc, "__notes__", []))
    line = f"eval failed: {type(exc).__name__}: {exc}" + (f" ({notes})" if notes else "")
    return " ".join(line.split())


def _load_config(path: Path) -> EvalsConfig:
    try:
        return load_config(path)
    except (OSError, EvalConfigError) as exc:
        _fail(str(exc))


@app.command()
def run(
    profile: Annotated[str, typer.Option(help="The evals.yml profile (and gateway profile).")],
    brief: Annotated[
        str | None, typer.Option(help="A brief key of evals.yml; defaults to the profile's.")
    ] = None,
    layers: Annotated[
        str | None,
        typer.Option(help="Comma-separated: retrieval,pipeline,challenger,cost; default all."),
    ] = None,
    baseline: Annotated[
        Path | None, typer.Option(help="Compare with this baseline; exit 1 on a regression.")
    ] = None,
    write_baseline: Annotated[
        Path | None,
        typer.Option(
            help="Write the report's metrics as a baseline here. With --baseline, the "
            "comparison runs first and the file is written only when nothing regressed."
        ),
    ] = None,
    report_dir: Annotated[Path, typer.Option(help="Where the report file goes.")] = Path(
        "evals/reports"
    ),
    config: ConfigOption = DEFAULT_CONFIG,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the report JSON instead of the summary.")
    ] = False,
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help="Evaluate this existing completed run instead of driving a new one "
            "(its profile must match --profile).",
        ),
    ] = None,
) -> None:
    """Run a profile's eval layers, record the report, and compare it with a baseline."""
    # Inputs and outputs are checked before any layer runs (and spends).
    against = _read_baseline(baseline) if baseline is not None else None
    if write_baseline is not None:
        try:
            write_baseline.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _fail(f"cannot create the directory of {write_baseline}: {exc}")
    settings = load_settings()
    configure_logging("WARNING" if "MODELFORGE_LOG_LEVEL" not in os.environ else settings.log_level)
    evals = _load_config(config)
    try:
        report = run_eval(
            settings, evals, config, profile, brief=brief, layers=layers, run_id=run_id
        )
    except EvalConfigError as exc:
        _fail(str(exc))
    except EvalError as exc:
        _fail(f"pipeline run parked: {exc}")
    except Exception as exc:
        _fail(_failure(exc))
    _record(settings, report, report_dir)
    spec = evals.profiles[profile]
    # Without a baseline the report is its own, so only the budgets can fail.
    if against is None:
        against = baseline_of(report)
    regressions = compare(report, against, spec)
    _summarize(report, regressions, as_json=as_json)
    if regressions:
        typer.echo(f"{len(regressions)} regression(s) against the baseline:", err=True)
        for r in regressions:
            typer.echo(f"  {r.metric}: {r.value!r} (baseline {r.baseline!r}; {r.rule})", err=True)
        raise typer.Exit(code=1)
    if write_baseline is not None:
        text = json.dumps(baseline_of(report), indent=2, sort_keys=True) + "\n"
        write_baseline.write_text(text, encoding="utf-8")


@app.command(name="compare")
def compare_command(
    report: Annotated[Path, typer.Argument(metavar="REPORT", help="An eval report JSON file.")],
    baseline: Annotated[Path, typer.Argument(metavar="BASELINE", help="A baseline JSON file.")],
    profile: Annotated[str, typer.Option(help="The evals.yml profile whose rules apply.")],
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Compare a report file with a baseline file; exit 1 on a regression."""
    evals = _load_config(config)
    spec = evals.profiles.get(profile)
    if spec is None:
        _fail(f"unknown profile {profile!r}; known: {sorted(evals.profiles)}")
    try:
        loaded = EvalReport.model_validate(_read_json(report))
    except ValueError as exc:
        _fail(f"{report} is not an eval report: {exc}")
    regressions = compare(loaded, _read_baseline(baseline), spec)
    typer.echo(markdown_summary(loaded, regressions))
    if regressions:
        raise typer.Exit(code=1)
