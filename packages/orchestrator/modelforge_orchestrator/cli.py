"""The ``modelforge`` command line: submit, approve, pause, inspect, replay and verify runs.

Exit codes: 0 success, 1 a check that ran and failed (replay mismatch, verify failure),
2 a request the run cannot take (bad brief, wrong gate, unknown run, ...).
"""

import atexit
import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

import typer
import yaml
from modelforge_core.schemas import ModelingPlan
from modelforge_gateway.schemas import GatewayError
from modelforge_lineage import migrate as lineage_migrate
from modelforge_lineage import runs
from modelforge_lineage import settings as server_settings
from modelforge_lineage.approvals import ApprovalError, record_approval
from modelforge_lineage.db import Database
from modelforge_lineage.findings import list_findings
from modelforge_lineage.models import GateDecision, GateName, RunRecord, RunStatus
from modelforge_lineage.observability import configure_logging
from modelforge_lineage.queue import enqueue_if_absent, release
from modelforge_lineage.steps import list_steps
from modelforge_lineage.store import ArtifactStore
from modelforge_lineage.tracing import configure_tracing, shutdown_tracing
from modelforge_lineage.verify import verify_run
from pydantic import TypeAdapter, ValidationError

from modelforge_orchestrator.briefs import BriefError
from modelforge_orchestrator.config import Settings, load_settings, setting_seeds
from modelforge_orchestrator.inspect import gate_prompt, run_refs, run_snapshot
from modelforge_orchestrator.replay import ReplayError, create_replay
from modelforge_orchestrator.schemas import ReplayReport
from modelforge_orchestrator.submit import UnknownAdapterError, create_run_from_brief
from modelforge_orchestrator.worker import (
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

_ABORTABLE: tuple[RunStatus, ...] = (
    "queued",
    "awaiting_scope",
    "awaiting_model",
    "awaiting_signoff",
    "needs_attention",
    "paused",
)
"""The statuses an abort moves from: neither running nor final."""


class _RefusedError(Exception):
    """Raised inside a transaction to roll a refused resume or abort back."""


JsonOption = Annotated[bool, typer.Option("--json", help="Print one JSON document.")]
DetachOption = Annotated[
    bool, typer.Option("--detach", help="Only enqueue; leave execution to a worker.")
]


def _context() -> tuple[Settings, Database, ArtifactStore]:
    """Settings, logging (quiet unless MODELFORGE_LOG_LEVEL is set), tracing (when OTLP
    endpoints are set; flushed at exit), database and store."""
    settings = load_settings()
    configure_logging("WARNING" if "MODELFORGE_LOG_LEVEL" not in os.environ else settings.log_level)
    if configure_tracing("modelforge-cli", settings.gateway.otlp_endpoints):
        atexit.register(shutdown_tracing)
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


_SNAPSHOT = TypeAdapter(dict[str, Any])

_SEVERITIES = ("critical", "high", "medium", "low")
_FINDING_STATUSES = ("open", "accepted", "disputed", "closed")


def _findings_line(findings: list[dict[str, Any]]) -> str:
    """``findings: <n> (open <a>, ...; max severity <s>)`` over a run's findings rows."""
    counts = ", ".join(f"{s} {sum(f['status'] == s for f in findings)}" for s in _FINDING_STATUSES)
    worst = min((f["severity"] for f in findings), key=_SEVERITIES.index)
    return f"findings: {len(findings)} ({counts}; max severity {worst})"


def _run_lines(db: Database, run: RunRecord) -> list[str]:
    lines = [
        f"{run.id} {run.status}",
        f"cost: {run.cost_usd:.4f} USD / cap {run.cost_cap_usd:.2f} USD",
    ]
    if run.status in _GATE_OF_STATUS:
        lines.append(f"gate: {_GATE_OF_STATUS[run.status]}")
        prompt = gate_prompt(db, run.id) or {}
        lines.append(f"summary: {json.dumps(prompt.get('summary', {}), separators=(',', ':'))}")
    if run.status == "needs_attention" and run.error is not None:
        detail = run.error.get("error", {})
        message = detail.get("message", json.dumps(detail)) if isinstance(detail, dict) else detail
        if run.error.get("kind") == "llm" and isinstance(detail, dict):
            # a gateway failure: name its kind (cost_cap, redaction, schema, refusal, ...)
            lines.append(f"error: llm {detail.get('kind', 'unknown')}: {message}")
        else:
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
            record = create_run_from_brief(
                db,
                store,
                brief_path,
                requester=requester or os.environ.get("USER", "requester"),
                profile=profile or settings.profile,
                cost_cap_usd=settings.gateway.cost_cap_usd,
                gateway=settings.gateway,
            )
        except (BriefError, OSError) as exc:
            _fail(f"invalid brief {brief_path}: {exc}")
        except UnknownAdapterError as exc:
            _fail(str(exc))
        except GatewayError as exc:
            _fail(str(exc))
        if not detach:
            record = _execute(settings, db, store, record.id)
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
                if not enqueue_if_absent(conn, run_id, "resume"):
                    raise _RefusedError  # rolls the approval back
        except _RefusedError:
            _fail("a decision at this gate is already queued")
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
    cost_cap: Annotated[
        float | None,
        typer.Option("--cost-cap", help="Raise the run's cost cap (USD) before resuming."),
    ] = None,
    detach: DetachOption = False,
) -> None:
    """Retry a run in needs_attention, or continue a paused one, from its last checkpoint."""
    settings, db, store = _context()
    try:
        if cost_cap is not None and cost_cap <= 0:
            _fail(f"invalid cost cap {cost_cap}; it must be positive")
        _get_run(db, run_id)  # an unknown run fails here
        try:
            with db.transaction() as conn:
                # Locked: a concurrent abort either lands first (and is seen here) or waits.
                current = runs.get_run(conn, run_id, for_update=True)
                if current.status not in ("needs_attention", "paused"):
                    raise _RefusedError(
                        f"run {run_id} is {current.status};"
                        " only a needs_attention or paused run resumes"
                    )
                if cost_cap is not None:
                    runs.set_cost_cap(conn, run_id, cost_cap)
                runs.set_error(conn, run_id, None)
                if not enqueue_if_absent(conn, run_id, "resume"):
                    # rolls the cap and error changes back
                    raise _RefusedError(f"run {run_id} is already queued")
        except _RefusedError as exc:
            _fail(str(exc))
        record = _execute(settings, db, store, run_id) if not detach else _get_run(db, run_id)
        _print_run(db, record, as_json=False)
    finally:
        db.close()


@app.command()
def pause(run_id: Annotated[str, typer.Argument(metavar="RUN_ID")]) -> None:
    """Ask a running run to pause at its next node boundary."""
    _, db, _ = _context()
    try:
        try:
            with db.transaction() as conn:
                requested = runs.request_pause(conn, run_id)
        except runs.RunNotFound:
            _fail(f"run not found: {run_id}")
        current = _get_run(db, run_id)
        if not requested:
            _fail(f"run {run_id} is {current.status}; only a running run pauses")
        _print_run(db, current, as_json=False)
    finally:
        db.close()


@app.command()
def abort(run_id: Annotated[str, typer.Argument(metavar="RUN_ID")]) -> None:
    """Mark a run aborted."""
    _, db, _ = _context()
    try:
        _get_run(db, run_id)  # exits 2 for an unknown run
        try:
            with db.transaction() as conn:
                # The run row first, then its queue row: the lock order `resume` takes.
                if not runs.transition(conn, run_id, from_statuses=_ABORTABLE, to="aborted"):
                    raise _RefusedError
                release(conn, run_id)  # a pending start or resume
        except _RefusedError:
            current = _get_run(db, run_id)
            if current.status == "running":
                _fail(
                    f"run {run_id} is running and there is no cancellation; pause it first"
                    f" (modelforge pause {run_id}), then abort it once it is paused"
                )
            _fail(f"run {run_id} is {current.status} and cannot be aborted")
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
        if as_json:
            snapshot = run_snapshot(db, run_id)
            typer.echo(json.dumps(_SNAPSHOT.dump_python(snapshot, mode="json")))
            return
        with db.connection() as conn:
            steps = list_steps(conn, run_id)
            findings = list_findings(conn, run_id)
        refs = run_refs(db, run_id)
        lines = _run_lines(db, record)
        if findings:
            lines.insert(2, _findings_line(findings))  # after the `cost:` line
        lines.append(f"gate prompt: {json.dumps(gate_prompt(db, run_id))}")
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
    """Apply lineage migrations, seed absent server-side settings, create the checkpoint tables."""
    settings, db, _ = _context()
    try:
        applied = lineage_migrate.apply(db)
        with db.transaction() as conn:
            seeded = server_settings.seed(conn, setting_seeds(settings))
        setup_checkpointer(db)
        typer.echo(f"applied: {', '.join(map(str, applied)) if applied else 'none'}")
        typer.echo(f"seeded: {', '.join(seeded) if seeded else 'none'}")
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
        f"regenerated: {', '.join(report.regenerated)}",
    ]
    typer.echo("\n".join(lines))


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(metavar="RUN_ID")],
    rerun_llm: Annotated[
        bool,
        typer.Option(
            "--rerun-llm",
            help="Call the LLM again for scope, the document, the challenger and remediation"
            " instead of copying the original's outputs; the LLM-derived refs are then reported"
            " as regenerated.",
        ),
    ] = False,
    detach: DetachOption = False,
    as_json: JsonOption = False,
) -> None:
    """Replay a sealed run and compare its deterministic artifacts with the original's."""
    settings, db, store = _context()
    try:
        try:
            with db.transaction() as conn:
                record = create_replay(conn, run_id, rerun_llm=rerun_llm)
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
        ref = run_refs(db, record.id).get("replay_report")
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
