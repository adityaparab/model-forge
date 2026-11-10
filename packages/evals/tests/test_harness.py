"""The harness: drive a brief through the worker and the scripted gate decisions."""

from typing import Any

import pytest
from modelforge_evals import harness
from modelforge_evals.config import load_config
from modelforge_evals.metrics import run_metrics
from modelforge_lineage.approvals import list_approvals
from modelforge_lineage.db import Database
from modelforge_lineage.findings import insert_findings
from modelforge_lineage.models import GateDecision
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.config import Settings
from psycopg import Connection
from psycopg.rows import DictRow

from .conftest import REPO, SYNTHETIC_BRIEF

APPROVALS = load_config(REPO / "evals/evals.yml").approvals


@pytest.mark.db
def test_drive_run_seals_the_synthetic_brief_under_fake(
    settings: Settings, database: Database, store: ArtifactStore, checkpointer: object
) -> None:
    driven = harness.drive_run(
        settings, database, store, SYNTHETIC_BRIEF, profile="fake", approvals=APPROVALS
    )
    assert driven.status == "completed" and driven.error is None
    assert driven.approvals == [
        "scope approve alice",
        "model accept bob",
        "signoff approve bob",
    ]
    metrics = run_metrics(database, store, driven.run_id)
    assert metrics.pipeline_status == "completed" and metrics.n_findings >= 1


@pytest.mark.db
def test_drive_run_falls_back_to_conditions_when_approve_is_blocked(
    settings: Settings,
    database: Database,
    store: ArtifactStore,
    checkpointer: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = harness.record_approval

    def planting(conn: Connection[DictRow], run_id: str, decision: GateDecision, **kw: Any) -> Any:
        if decision.gate == "signoff":
            insert_findings(
                conn,
                run_id,
                [
                    {
                        "id": f"{run_id}/F-99",
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
        return real(conn, run_id, decision, **kw)

    monkeypatch.setattr(harness, "record_approval", planting)
    driven = harness.drive_run(
        settings, database, store, SYNTHETIC_BRIEF, profile="fake", approvals=APPROVALS
    )
    assert driven.status == "completed"
    assert driven.approvals[-1] == "signoff approve_with_conditions bob"
    with database.connection() as conn:
        signoff = list_approvals(conn, driven.run_id, gate="signoff")
    assert [a.decision for a in signoff] == ["approve_with_conditions"]
    assert signoff[0].conditions and "Planted high finding" in signoff[0].conditions[0]
