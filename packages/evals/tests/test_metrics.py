"""Metrics over the retrieval goldens and over a completed run."""

from pathlib import Path

import pytest
from modelforge_evals.metrics import RunMetrics, flatten, retrieval_recall_at_5, run_metrics
from modelforge_lineage.db import Database
from modelforge_lineage.store import ArtifactStore

from .conftest import REPO


def test_retrieval_recall_at_5_on_the_committed_goldens() -> None:
    assert retrieval_recall_at_5(
        REPO / "knowledge-base", REPO / "evals/goldens/retrieval.yaml"
    ) == (1.0, [])


def test_retrieval_recall_reports_misses(tmp_path: Path) -> None:
    goldens = tmp_path / "g.yaml"
    goldens.write_text(
        "- {question: 'What does effective challenge require?', doc_id: sr11-7,"
        " must_contain: 'effective challenge'}\n"
        "- {question: 'What does effective challenge require?', doc_id: basel-irb,"
        " must_contain: 'zebra'}\n"
    )
    recall, misses = retrieval_recall_at_5(REPO / "knowledge-base", goldens)
    assert recall == 0.5
    assert misses == ["What does effective challenge require?"]


def _metrics(**overrides: object) -> RunMetrics:
    fields: dict[str, object] = {
        "pipeline_status": "completed",
        "signoff_decision": "approve",
        "n_sections": 11,
        "n_flagged_sections": 0,
        "grounding_pass_rate": 1.0,
        "n_ungrounded_numbers": 0,
        "completeness_complete": True,
        "scope_exact_match": True,
        "challenge_rounds": 2,
        "remediation_rounds": 1,
        "n_findings": 2,
        "findings_by_source": {"review": 2},
        "findings_by_severity": {"medium": 2},
        "findings_by_status": {"closed": 2},
        "n_sections_regenerated": 1,
        "cost_usd": 0.0,
        "cost_by_purpose": {"document": 0.0},
        "n_llm_calls": 24,
        "n_schema_retries": 0,
        "input_tokens": 100,
        "output_tokens": 50,
        "latency_ms_p50": 1.0,
        "latency_ms_p95": 2.0,
        "wall_time_s": 3.0,
        "wall_time_by_node_s": {"challenge": 0.5},
    }
    fields.update(overrides)
    return RunMetrics.model_validate(fields)


def test_flatten_joins_nested_keys_with_a_dot() -> None:
    flat = flatten(_metrics())
    assert flat["findings_by_status.closed"] == 2
    assert flat["cost_by_purpose.document"] == 0.0
    assert flat["wall_time_by_node_s.challenge"] == 0.5
    assert flat["signoff_decision"] == "approve"
    assert "findings_by_status" not in flat
    assert all(not isinstance(v, dict) for v in flat.values())


@pytest.mark.db
def test_run_metrics_from_a_completed_fake_run(
    database: Database, store: ArtifactStore, completed_run_id: str
) -> None:
    metrics = run_metrics(database, store, completed_run_id)
    assert metrics.pipeline_status == "completed"
    assert metrics.signoff_decision == "approve"
    assert metrics.grounding_pass_rate == 1.0
    assert metrics.n_flagged_sections == 0
    assert metrics.n_sections == 11
    assert metrics.completeness_complete is True
    assert metrics.scope_exact_match is True
    assert metrics.n_findings >= 1
    assert sum(metrics.findings_by_status.values()) == metrics.n_findings
    assert sum(metrics.findings_by_source.values()) == metrics.n_findings
    assert sum(metrics.findings_by_severity.values()) == metrics.n_findings
    assert metrics.cost_usd == 0.0
    assert set(metrics.cost_by_purpose) == {"scope", "document", "challenge", "remediate"}
    assert metrics.n_llm_calls >= 24
    assert metrics.n_schema_retries == 0
    assert metrics.input_tokens > 0 and metrics.output_tokens > 0
    assert metrics.challenge_rounds >= 2
    assert metrics.remediation_rounds >= 1
    assert metrics.latency_ms_p50 <= metrics.latency_ms_p95
    assert {"intake", "scope", "document", "challenge", "remediate"} <= set(
        metrics.wall_time_by_node_s
    )
    assert metrics.wall_time_s >= max(metrics.wall_time_by_node_s.values())
