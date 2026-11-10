"""Read-only views of a run, shared by the ``modelforge`` CLI and the API.

These readers hide the LangGraph checkpoint behind plain models and dicts, so callers above
the orchestrator never touch LangGraph types.
"""

from typing import Any

from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.events import list_events
from modelforge_lineage.findings import list_findings
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.models import ArtifactRef
from modelforge_lineage.steps import list_steps

from modelforge_orchestrator.graph import build_graph
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.worker import make_checkpointer

__all__ = ["gate_prompt", "run_refs", "run_snapshot", "run_state"]


def _checkpoint_values(db: Database, run_id: str) -> dict[str, Any]:
    graph = build_graph(make_checkpointer(db))
    values: dict[str, Any] = graph.get_state({"configurable": {"thread_id": run_id}}).values
    return values


def run_state(db: Database, run_id: str) -> RunState | None:
    """The run's latest checkpointed state; `None` before its first checkpoint."""
    values = _checkpoint_values(db, run_id)
    return RunState.model_validate(values) if values else None


def run_refs(db: Database, run_id: str) -> dict[str, ArtifactRef]:
    """The run's refs from its latest checkpoint; empty when none exists."""
    values = _checkpoint_values(db, run_id)
    return {name: ArtifactRef.model_validate(ref) for name, ref in values.get("refs", {}).items()}


def gate_prompt(db: Database, run_id: str) -> dict[str, Any] | None:
    """The payload of the run's latest ``gate.requested`` event; `None` when none exists."""
    with db.connection() as conn:
        events = list_events(conn, run_id)
    requested = [e for e in events if e.type == "gate.requested"]
    return requested[-1].payload if requested else None


def run_snapshot(db: Database, run_id: str) -> dict[str, Any]:
    """The run with its steps, refs, latest gate prompt, LLM calls and findings.

    Keys ``run``, ``steps``, ``refs``, ``gate_prompt``, ``llm_calls`` and ``findings``; the
    values are models and dicts, so dump with ``mode="json"`` to print. Raises `RunNotFound`.
    """
    with db.connection() as conn:
        record = runs.get_run(conn, run_id)
        steps = list_steps(conn, run_id)
        llm_calls = list_llm_calls(conn, run_id)
        findings = list_findings(conn, run_id)
    return {
        "run": record,
        "steps": steps,
        "refs": run_refs(db, run_id),
        "gate_prompt": gate_prompt(db, run_id),
        "llm_calls": llm_calls,
        "findings": findings,
    }
