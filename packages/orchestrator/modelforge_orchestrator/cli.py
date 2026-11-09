"""The ``modelforge`` command line: submit, approve, inspect, replay and verify runs.

Exit codes: 0 success, 1 a check that ran and failed (replay mismatch, verify failure),
2 a request the run cannot take (bad brief, wrong gate, unknown run, ...).
"""

import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

import typer
import yaml
from modelforge_core.datasets.registry import ADAPTERS
from modelforge_core.schemas import ModelingPlan
from modelforge_lineage import migrate as lineage_migrate
from modelforge_lineage import runs
from modelforge_lineage.approvals import ApprovalError, record_approval
from modelforge_lineage.db import Database
from modelforge_lineage.events import list_events
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import ArtifactRef, GateDecision, GateName, RunRecord
from modelforge_lineage.observability import configure_logging
from modelforge_lineage.queue import enqueue
from modelforge_lineage.steps import list_steps
from modelforge_lineage.store import MEDIA_YAML, ArtifactStore
from modelforge_lineage.verify import verify_run
from pydantic import ValidationError

from modelforge_orchestrator.briefs import BriefError, brief_hash, load_brief
from modelforge_orchestrator.config import Settings, load_settings
from modelforge_orchestrator.graph import build_graph
from modelforge_orchestrator.replay import ReplayError, create_replay
from modelforge_orchestrator.schemas import ReplayReport
from modelforge_orchestrator.worker import (
    make_checkpointer,
    run_loop,
    run_once,
    setup_checkpointer,
)

__all__ = ["app"]

app = typer.Typer(no_args_is_help=True, add_completion=False, help="ModelForge orchestrator.")

_GATE_OF_STATUS: dict[str, GateName] = {
    "awaiting_scope": "scope",
    "awaiting_model": "model",
    "awaiting_signoff": "signoff",
}

JsonOption = Annotated[bool, typer.Option("--json", help="Print one JSON document.")]
DetachOption = Annotated[
    bool, typer.Option("--detach", help="Only enqueue; leave execution to a worker.")
]


def _context() -> tuple[Settings, Database, ArtifactStore]:
    """Settings, logging (quiet unless MODELFORGE_LOG_LEVEL is set), database and store."""
    settings = load_settings()
    configure_logging("WARNING" if "MODELFORGE_LOG_LEVEL" not in os.environ else settings.log_level)
    return settings, Database(settings.database_url), ArtifactStore(settings.artifact_dir)


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=2)


def _get_run(db: Database, run_id: str) -> RunRecord:
    try:
        with db.connection() as conn:
            return runs.get_run(conn, run_id)
    except runs.RunNotFound:
        _fail(f"run not found: {run_id}")


def _refs(db: Database, run_id: str) -> dict[str, ArtifactRef]:
    """The run's refs from its latest checkpoint; empty when none exists."""
    graph = build_graph(make_checkpointer(db))
    values: dict[str, Any] = graph.get_state({"configurable": {"thread_id": run_id}}).values
    return {name: ArtifactRef.model_validate(ref) for name, ref in values.get("refs", {}).items()}


def _gate_summary_keys(db: Database, run_id: str) -> list[str]:
    with db.connection() as conn:
        events = list_events(conn, run_id)
    requested = [e for e in events if e.type == "gate.requested"]
    return list(requested[-1].payload.get("summary", {})) if requested else []


def _run_lines(db: Database, run: RunRecord) -> list[str]:
    lines = [f"{run.id} {run.status}"]
    if run.status in _GATE_OF_STATUS:
        lines.append(f"gate: {_GATE_OF_STATUS[run.status]}")
        lines.append(f"summary keys: {', '.join(_gate_summary_keys(db, run.id))}")
    if run.status == "needs_attention" and run.error is not None:
        detail = run.error.get("error", {})
        message = detail.get("message", json.dumps(detail)) if isinstance(detail, dict) else detail
        lines.append(f"error: {run.error.get('kind', 'unknown')} {message}")
    if run.replay_of is not None:
        lines.append(f"replay_of: {run.replay_of}")
    return lines


def _print_run(db: Database, run: RunRecord, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(run.model_dump(mode="json")))
    else:
        typer.echo("\n".join(_run_lines(db, run)))


def _execute(settings: Settings, db: Database, store: ArtifactStore, run_id: str) -> RunRecord:
    """Execute ``run_id``'s queue row in the foreground; the run as it stands afterwards."""
    run = run_once(settings, db, store, run_id=run_id)
    return run if run is not None else _get_run(db, run_id)


@app.command()
def run(
    brief_path: Annotated[Path, typer.Argument(metavar="BRIEF", help="The brief YAML file.")],
    requester: Annotated[str | None, typer.Option(help="Defaults to $USER.")] = None,
    profile: Annotated[str | None, typer.Option(help="Defaults to MODELFORGE_PROFILE.")] = None,
    detach: DetachOption = False,
    as_json: JsonOption = False,
) -> None:
    """Submit a brief: create the run, enqueue it, and execute it to its first gate."""
    settings, db, store = _context()
    try:
        try:
            brief, data = load_brief(brief_path)
        except (BriefError, OSError) as exc:
            _fail(f"invalid brief {brief_path}: {exc}")
        if brief.dataset.adapter not in ADAPTERS:
            _fail(f"unknown dataset adapter {brief.dataset.adapter!r}; known: {sorted(ADAPTERS)}")
        ref = store.put(data, kind="brief", media_type=MEDIA_YAML)
        run_id = new_id("run")
        with db.transaction() as conn:
            record = runs.create_run(
                conn,
                run_id=run_id,
                requester=requester or os.environ.get("USER", "requester"),
                profile=profile or settings.profile,
                brief_hash=brief_hash(data),
                brief_ref=ref.id,
                dataset_ref=brief.dataset.as_string(),
                seed=brief.dataset.seed,
            )
            enqueue(conn, run_id, "start")
        if not detach:
            record = _execute(settings, db, store, run_id)
        _print_run(db, record, as_json=as_json)
    finally:
        db.close()


@app.command()
def approve(
    run_id: Annotated[str, typer.Argument(metavar="RUN_ID")],
    gate: Annotated[Literal["scope", "model", "signoff"], typer.Argument(metavar="GATE")],
    decision: Annotated[str, typer.Option(help="The gate decision.")],
    approver: Annotated[str, typer.Option(help="Who decides.")],
    comment: Annotated[str, typer.Option()] = "",
    plan: Annotated[Path | None, typer.Option(help="A ModelingPlan YAML (edit, adjust).")] = None,
    adjust_target: Annotated[Literal["features", "build"] | None, typer.Option()] = None,
    condition: Annotated[
        list[str] | None, typer.Option(help="A sign-off condition; repeatable.")
    ] = None,
    detach: DetachOption = False,
) -> None:
    """Record a gate decision, enqueue the resume, and execute it to the next gate or the end."""
    settings, db, store = _context()
    try:
        new_plan: ModelingPlan | None = None
        if plan is not None:
            try:
                new_plan = ModelingPlan.model_validate(yaml.safe_load(plan.read_text()))
            except (OSError, yaml.YAMLError, ValidationError) as exc:
                _fail(f"invalid plan {plan}: {exc}")
        gate_decision = GateDecision(
            gate=gate,
            decision=decision,
            approver=approver,
            comment=comment,
            plan=new_plan,
            adjust_target=adjust_target,
            conditions=condition or [],
        )
        current = _get_run(db, run_id)
        current_plan = store.get_model(current.plan_ref, ModelingPlan) if current.plan_ref else None
        try:
            with db.transaction() as conn:
                record_approval(
                    conn, run_id, gate_decision, current_plan=current_plan, loop_bound=3
                )
                enqueue(conn, run_id, "resume")
        except ApprovalError as exc:
            _fail(str(exc))
        except runs.RunNotFound:
            _fail(f"run not found: {run_id}")
        record = _execute(settings, db, store, run_id) if not detach else _get_run(db, run_id)
        _print_run(db, record, as_json=False)
    finally:
        db.close()


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(metavar="RUN_ID")],
    detach: DetachOption = False,
) -> None:
    """Retry a run in needs_attention from its last checkpoint."""
    settings, db, store = _context()
    try:
        current = _get_run(db, run_id)
        if current.status != "needs_attention":
            _fail(f"run {run_id} is {current.status}; only a needs_attention run resumes")
        with db.transaction() as conn:
            runs.set_error(conn, run_id, None)
            enqueue(conn, run_id, "resume")
        record = _execute(settings, db, store, run_id) if not detach else _get_run(db, run_id)
        _print_run(db, record, as_json=False)
    finally:
        db.close()


@app.command()
def abort(run_id: Annotated[str, typer.Argument(metavar="RUN_ID")]) -> None:
    """Mark a run aborted."""
    _, db, _ = _context()
    try:
        current = _get_run(db, run_id)
        if current.status in ("completed", "rejected"):
            _fail(f"run {run_id} is {current.status} and cannot be aborted")
        with db.transaction() as conn:
            runs.set_status(conn, run_id, "aborted")
        _print_run(db, _get_run(db, run_id), as_json=False)
    finally:
        db.close()


@app.command()
def status(
    run_id: Annotated[str, typer.Argument(metavar="RUN_ID")],
    as_json: JsonOption = False,
) -> None:
    """Show a run: status, error, steps and refs."""
    _, db, _ = _context()
    try:
        record = _get_run(db, run_id)
        with db.connection() as conn:
            steps = list_steps(conn, run_id)
        refs = _refs(db, run_id)
        if as_json:
            document = {
                "run": record.model_dump(mode="json"),
                "steps": [s.model_dump(mode="json") for s in steps],
                "refs": {name: ref.model_dump(mode="json") for name, ref in refs.items()},
            }
            typer.echo(json.dumps(document))
            return
        lines = _run_lines(db, record)
        lines += [f"step {s.node} {s.attempt} {s.status}" for s in steps]
        lines += [
            f"ref {name} {ref.id} {json.dumps(ref.summary, sort_keys=True)}"
            for name, ref in refs.items()
        ]
        typer.echo("\n".join(lines))
    finally:
        db.close()


@app.command()
def worker(
    once: Annotated[bool, typer.Option("--once", help="Execute at most one run.")] = False,
    poll_interval: Annotated[float, typer.Option(help="Seconds between idle polls.")] = 2.0,
) -> None:
    """Execute queued runs until stopped."""
    run_loop(load_settings(), once=once, poll_interval_s=poll_interval)


@app.command()
def migrate() -> None:
    """Apply lineage migrations and create the checkpoint tables."""
    _, db, _ = _context()
    try:
        applied = lineage_migrate.apply(db)
        setup_checkpointer(db)
        typer.echo(f"applied: {', '.join(map(str, applied)) if applied else 'none'}")
    finally:
        db.close()


def _print_replay(db: Database, run: RunRecord, report: ReplayReport, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json")))
        return
    mismatched = ",".join(f"{m.ref}={m.original_id}..{m.replay_id}" for m in report.mismatched)
    lines = _run_lines(db, run)
    lines += [
        f"ok: {report.ok}",
        f"matched: {len(report.matched)}",
        f"mismatched: {mismatched}",
        f"missing: {', '.join(report.missing)}",
    ]
    typer.echo("\n".join(lines))


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(metavar="RUN_ID")],
    detach: DetachOption = False,
    as_json: JsonOption = False,
) -> None:
    """Replay a sealed run and compare its deterministic artifacts with the original's."""
    settings, db, store = _context()
    try:
        try:
            with db.transaction() as conn:
                record = create_replay(conn, run_id)
        except ReplayError as exc:
            _fail(str(exc))
        except runs.RunNotFound:
            _fail(f"run not found: {run_id}")
        if detach:
            if as_json:
                typer.echo(json.dumps({"run": record.model_dump(mode="json"), "report": None}))
                typer.echo(f"replay {record.id} queued; no report until a worker runs it", err=True)
            else:
                typer.echo(f"{record.id} queued")
            return
        record = _execute(settings, db, store, record.id)
        ref = _refs(db, record.id).get("replay_report")
        if ref is None:
            if as_json:
                typer.echo(json.dumps({"run": record.model_dump(mode="json"), "report": None}))
            else:
                typer.echo("\n".join(_run_lines(db, record)))
            typer.echo(f"replay {record.id} produced no replay report", err=True)
            raise typer.Exit(code=1)
        report = store.get_model(ref.id, ReplayReport)
        _print_replay(db, record, report, as_json=as_json)
        if not report.ok:
            raise typer.Exit(code=1)
    finally:
        db.close()


@app.command()
def verify(
    run_id: Annotated[str, typer.Argument(metavar="RUN_ID")],
    as_json: JsonOption = False,
) -> None:
    """Check a run's event chain, artifacts and manifest."""
    _, db, store = _context()
    try:
        _get_run(db, run_id)
        with db.connection() as conn:
            report = verify_run(conn, store, run_id)
        if as_json:
            typer.echo(json.dumps(report.model_dump(mode="json")))
        else:
            typer.echo(
                "\n".join(
                    [
                        f"{run_id} verify",
                        f"ok: {report.ok}",
                        f"chain ok: {report.chain.ok}",
                        f"artifacts checked: {report.artifacts_checked}",
                        f"missing: {', '.join(report.artifacts_missing)}",
                        f"corrupt: {', '.join(report.artifacts_corrupt)}",
                        f"manifest ok: {report.manifest_ok}",
                    ]
                )
            )
        if not report.ok:
            raise typer.Exit(code=1)
    finally:
        db.close()


if __name__ == "__main__":
    app()
