"""Record sealed runs into docs/results/runs.json and render result tables into documents.

Usage (through ``uv run python scripts/record_result.py``):

    add RUN_ID --profile P --label L [--report PATH] [--note TEXT] [--results PATH]
    table parity|cost|runs [--results PATH]
    render FILE... [--results PATH] [--check]

Every number in a record comes from the eval report or from lineage; no metric is taken
on the command line. ``render`` rewrites the text between ``<!-- results:NAME -->`` and
``<!-- /results:NAME -->`` markers; ``render --check`` exits 1 when a file would change.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

DEFAULT_RESULTS = Path("docs/results/runs.json")
PURPOSES = ("scope", "document", "challenge", "remediate")

RECORD_KEYS: frozenset[str] = frozenset(
    {
        "run_id",
        "label",
        "profile",
        "models",
        "recorded_at",
        "orchestrator_version",
        "status",
        "signoff",
        "grounding_pass_rate",
        "completeness_complete",
        "n_flagged_sections",
        "n_ungrounded_numbers",
        "n_sections",
        "n_sections_regenerated",
        "findings",
        "rounds",
        "scope_exact_match",
        "retrieval_recall_at_5",
        "n_llm_calls",
        "n_schema_retries",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "cost_by_purpose",
        "cost_cap_usd",
        "wall_time_s",
        "wall_time_by_node_s",
        "replay_ok",
        "note",
    }
)

_BLOCK = re.compile(
    r"(<!-- results:(?P<name>[a-z]+) -->)(?P<body>.*?)(<!-- /results:(?P=name) -->)", re.S
)


# --- records -------------------------------------------------------------------------


def _prefixed(metrics: dict[str, Any], prefix: str) -> dict[str, Any]:
    """The flattened ``prefix.key`` metrics as ``{key: value}``, sorted by key."""
    start = prefix + "."
    return {k[len(start) :]: v for k, v in sorted(metrics.items()) if k.startswith(start)}


def _replay_ok(replays: Sequence[Any]) -> bool | None:
    """``ok`` of the latest completed plain (not LLM-rerun) replay's report; None without one."""
    plain = [
        r
        for r in replays
        if r.run.status == "completed" and not r.run.rerun_llm and r.report is not None
    ]
    return bool(plain[-1].report.ok) if plain else None


def _signoff(approvals: Sequence[Any], metrics: dict[str, Any]) -> str | None:
    signoffs = [a for a in approvals if a.gate == "signoff"]
    if signoffs:
        return signoffs[-1].decision
    return metrics.get("signoff_decision")


def build_record(
    report: dict[str, Any],
    run: Any,
    approvals: Sequence[Any],
    replays: Sequence[Any],
    *,
    label: str,
    note: str | None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """One results record (spec 4.2 shape) from an eval report and the run's lineage.

    ``run`` has ``status``, ``profile`` and ``cost_cap_usd``; each approval has ``gate``,
    ``decision`` and ``approver``; each replay has ``run`` (with ``status`` and
    ``rerun_llm``) and ``report`` (with ``ok``, or None when it has none).
    """
    m: dict[str, Any] = report["metrics"]
    sources = _prefixed(m, "findings_by_source")
    severities = _prefixed(m, "findings_by_severity")
    statuses = _prefixed(m, "findings_by_status")
    cost_by_purpose = {p: 0.0 for p in PURPOSES} | {
        k: float(v) for k, v in _prefixed(m, "cost_by_purpose").items()
    }
    when = recorded_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    record: dict[str, Any] = {
        "run_id": report["run_id"],
        "label": label,
        "profile": run.profile,
        "models": dict(report["models"]),
        "recorded_at": when,
        "orchestrator_version": report["orchestrator_version"],
        "status": run.status,
        "signoff": _signoff(approvals, m),
        "grounding_pass_rate": m["grounding_pass_rate"],
        "completeness_complete": m["completeness_complete"],
        "n_flagged_sections": m["n_flagged_sections"],
        "n_ungrounded_numbers": m["n_ungrounded_numbers"],
        "n_sections": m["n_sections"],
        "n_sections_regenerated": m["n_sections_regenerated"],
        "findings": {
            "total": m["n_findings"],
            "precheck": sources.get("precheck", 0),
            "review": sources.get("review", 0),
            "high": severities.get("high", 0),
            "medium": severities.get("medium", 0),
            "low": severities.get("low", 0),
            "open_at_signoff": statuses.get("open", 0),
        },
        "rounds": {"challenge": m["challenge_rounds"], "remediation": m["remediation_rounds"]},
        "scope_exact_match": m["scope_exact_match"],
        "retrieval_recall_at_5": m["retrieval_recall_at_5"],
        "n_llm_calls": m["n_llm_calls"],
        "n_schema_retries": m["n_schema_retries"],
        "input_tokens": m["input_tokens"],
        "output_tokens": m["output_tokens"],
        "cost_usd": float(m["cost_usd"]),
        "cost_by_purpose": cost_by_purpose,
        "cost_cap_usd": float(run.cost_cap_usd),
        "wall_time_s": m["wall_time_s"],
        "wall_time_by_node_s": _prefixed(m, "wall_time_by_node_s"),
        "replay_ok": _replay_ok(replays),
        "note": note,
    }
    assert set(record) == RECORD_KEYS
    return record


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path}: expected a JSON list of records")
    return data


def save_records(path: Path, records: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(records), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upsert(records: Sequence[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
    """``records`` with ``record`` replacing the one of the same ``run_id``, else appended."""
    out = [record if r["run_id"] == record["run_id"] else r for r in records]
    if not any(r["run_id"] == record["run_id"] for r in records):
        out.append(record)
    return out


# --- tables --------------------------------------------------------------------------


def _usd(value: float) -> str:
    return f"{value:.2f}"


def _total_usd(value: float) -> str:
    return "0 (local)" if value == 0 else _usd(value)


def _yes_no(value: bool | None) -> str:
    return "n/a" if value is None else ("yes" if value else "no")


def _mmss(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _parity_row(r: dict[str, Any]) -> list[str]:
    f, rounds = r["findings"], r["rounds"]
    return [
        r["label"],
        r["models"].get("document", "n/a"),
        f"{r['status']}, {r['signoff'] or 'n/a'}",
        f"{r['grounding_pass_rate'] * 100:.1f}%",
        f"{_yes_no(r['completeness_complete'])} ({r['n_flagged_sections']} flagged)",
        str(r["n_ungrounded_numbers"]),
        f"{f['precheck']} / {f['review']}; {f['high']} / {f['medium']} / {f['low']}",
        f"{rounds['challenge']} / {rounds['remediation']}",
        str(r["n_sections_regenerated"]),
        str(r["n_schema_retries"]),
        str(r["n_llm_calls"]),
        _total_usd(r["cost_usd"]),
        _mmss(r["wall_time_s"]),
    ]


def _cost_row(r: dict[str, Any]) -> list[str]:
    by = r["cost_by_purpose"]
    return [
        r["label"],
        str(r["input_tokens"]),
        str(r["output_tokens"]),
        *(_usd(by.get(p, 0.0)) for p in PURPOSES),
        _total_usd(r["cost_usd"]),
        _usd(r["cost_cap_usd"]),
    ]


def _runs_row(r: dict[str, Any]) -> list[str]:
    return [
        r["run_id"],
        r["label"],
        r["recorded_at"][:10],
        r["orchestrator_version"],
        _yes_no(r["replay_ok"]),
    ]


_TABLES: dict[str, tuple[list[str], Callable[[dict[str, Any]], list[str]]]] = {
    "parity": (
        [
            "Profile",
            "Model",
            "Status",
            "Grounding",
            "Complete",
            "Ungrounded numbers",
            "Findings (pre-check / review; high / medium / low)",
            "Rounds (challenge / remediation)",
            "Sections regenerated",
            "Schema retries",
            "LLM calls",
            "Cost USD",
            "Wall time",
        ],
        _parity_row,
    ),
    "cost": (
        [
            "Profile",
            "Input tokens",
            "Output tokens",
            *PURPOSES,
            "Total",
            "Cap",
        ],
        _cost_row,
    ),
    "runs": (["Run id", "Label", "Recorded", "Orchestrator version", "Replay ok"], _runs_row),
}


def _line(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def table(name: str, records: Sequence[dict[str, Any]]) -> str:
    """The Markdown table ``name`` for ``records``, without a trailing newline."""
    if name not in _TABLES:
        raise ValueError(f"unknown table {name!r}; expected one of {sorted(_TABLES)}")
    header, row = _TABLES[name]
    rows = [row(r) for r in records] or [["no records yet", *([""] * (len(header) - 1))]]
    lines = [_line(header), _line(["---"] * len(header)), *(_line(r) for r in rows)]
    return "\n".join(lines)


def render(text: str, records: Sequence[dict[str, Any]]) -> str:
    """``text`` with every results block's body replaced by its freshly rendered table."""

    def fill(match: re.Match[str]) -> str:
        body = table(match["name"], records)
        return f"{match[1]}\n{body}\n{match[4]}"

    return _BLOCK.sub(fill, text)


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n") + "\n"


# --- lineage -------------------------------------------------------------------------


def load_lineage(run_id: str) -> tuple[Any, list[Any], list[Any]]:
    """The run, its approvals and its replays (each with ``run`` and ``report``) from lineage."""
    from modelforge_lineage import approvals as lineage_approvals
    from modelforge_lineage import runs
    from modelforge_lineage.db import Database
    from modelforge_lineage.store import ArtifactNotFound, ArtifactStore
    from modelforge_orchestrator.config import load_settings
    from modelforge_orchestrator.inspect import run_refs
    from modelforge_orchestrator.schemas import ReplayReport

    settings = load_settings()
    db = Database(settings.database_url)
    store = ArtifactStore(settings.artifact_dir)
    with db.connection() as conn:
        run = runs.get_run(conn, run_id)
        approvals = lineage_approvals.list_approvals(conn, run_id)
        replay_runs = runs.list_replays(conn, run_id)
    replays: list[Any] = []
    for replay in replay_runs:
        ref = run_refs(db, replay.id).get("replay_report")
        try:
            report = store.get_model(ref.id, ReplayReport) if ref is not None else None
        except ArtifactNotFound:
            report = None
        replays.append(SimpleNamespace(run=replay, report=report))
    return run, approvals, replays


# --- commands ------------------------------------------------------------------------


def _add(args: argparse.Namespace) -> int:
    if args.report is None:
        subprocess.run(
            [
                "uv",
                "run",
                "modelforge-evals",
                "run",
                "--profile",
                args.profile,
                "--run-id",
                args.run_id,
                "--layers",
                "pipeline,retrieval",
            ],
            check=True,
        )
        report_path = Path("evals/reports") / f"{args.profile}-{args.run_id}.json"
    else:
        report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("run_id") != args.run_id:
        raise SystemExit(f"{report_path} is for {report.get('run_id')}, not {args.run_id}")
    run, approvals, replays = load_lineage(args.run_id)
    record = build_record(report, run, approvals, replays, label=args.label, note=args.note)
    save_records(args.results, upsert(load_records(args.results), record))
    print(f"recorded {args.run_id} as {args.label!r} in {args.results}")
    return 0


def _table(args: argparse.Namespace) -> int:
    print(table(args.name, load_records(args.results)))
    return 0


def _render(args: argparse.Namespace) -> int:
    records = load_records(args.results)
    stale: list[Path] = []
    for name in args.files:
        path = Path(name)
        if not path.is_file():
            print(f"skipping {path}: not found", file=sys.stderr)
            continue
        current = _normalise(path.read_text(encoding="utf-8"))
        rendered = _normalise(render(current, records))
        if rendered == current:
            continue
        if args.check:
            stale.append(path)
            print(f"{path}: results blocks are stale; run `make results`", file=sys.stderr)
        else:
            path.write_text(rendered, encoding="utf-8")
            print(f"rendered {path}")
    return 1 if stale else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="record_result.py", description=(__doc__ or "").splitlines()[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="record a sealed run")
    add.add_argument("run_id")
    add.add_argument("--profile", required=True)
    add.add_argument("--label", required=True)
    add.add_argument("--report", default=None, help="an existing eval report; skips the harness")
    add.add_argument("--note", default=None)
    add.set_defaults(func=_add)

    tab = sub.add_parser("table", help="print one results table")
    tab.add_argument("name", choices=sorted(_TABLES))
    tab.set_defaults(func=_table)

    ren = sub.add_parser("render", help="rewrite the results blocks of FILE...")
    ren.add_argument("files", nargs="+", metavar="FILE")
    ren.add_argument("--check", action="store_true", help="exit 1 when a file would change")
    ren.set_defaults(func=_render)

    for p in (add, tab, ren):
        p.add_argument("--results", type=Path, default=DEFAULT_RESULTS)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
