"""Metrics: retrieval recall over the goldens, and the measurable facts of a completed run."""

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from modelforge_core.schemas import ModelingPlan, StrictModel
from modelforge_knowledge.corpus import load_corpus
from modelforge_knowledge.index import build_index
from modelforge_knowledge.retrieval import Retriever
from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.models import LlmCallRecord, StepRecord
from modelforge_lineage.steps import list_steps
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.briefs import parse_brief
from modelforge_orchestrator.schemas import Manifest, RefName

SCOPE_IDENTITY_FIELDS = (
    "model_name",
    "portfolio",
    "target",
    "intended_use",
    "regulatory_scope",
    "tier",
    "vintage_policy",
)
"""The plan fields the scope agent must copy from the brief unchanged."""


def retrieval_recall_at_5(knowledge_dir: Path, goldens: Path) -> tuple[float, list[str]]:
    """Recall at k = 5 over the golden questions, and the questions missed.

    A question is found when some top-5 hit comes from its ``doc_id`` and contains its
    ``must_contain`` (case-insensitive).
    """
    entries = yaml.safe_load(goldens.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{goldens} must be a non-empty list of golden questions")
    retriever = Retriever(build_index(load_corpus(knowledge_dir)))
    misses: list[str] = []
    for entry in entries:
        needle = str(entry["must_contain"]).lower()
        hits = retriever.search(str(entry["question"]), k=5)
        if not any(h.doc_id == entry["doc_id"] and needle in h.text.lower() for h in hits):
            misses.append(str(entry["question"]))
    return (len(entries) - len(misses)) / len(entries), misses


class RunMetrics(StrictModel):
    pipeline_status: str
    signoff_decision: str | None
    n_sections: int
    n_flagged_sections: int
    grounding_pass_rate: float
    n_ungrounded_numbers: int
    completeness_complete: bool
    scope_exact_match: bool
    challenge_rounds: int
    remediation_rounds: int
    n_findings: int
    findings_by_source: dict[str, int]
    findings_by_severity: dict[str, int]
    findings_by_status: dict[str, int]
    n_sections_regenerated: int
    cost_usd: float
    cost_by_purpose: dict[str, float]
    n_llm_calls: int
    n_schema_retries: int
    input_tokens: int
    output_tokens: int
    latency_ms_p50: float
    latency_ms_p95: float
    wall_time_s: float
    wall_time_by_node_s: dict[str, float]


def _percentile(values: list[int], p: float) -> float:
    """Nearest-rank percentile; 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[max(math.ceil(p / 100 * len(ordered)), 1) - 1])


def _wall_times(steps: list[StepRecord]) -> tuple[float, dict[str, float]]:
    """Total wall time (last finish minus first start) and the summed durations per node."""
    spans = [(s.node, s.started_at, s.finished_at) for s in steps if s.finished_at is not None]
    if not spans:
        return 0.0, {}
    by_node: dict[str, float] = defaultdict(float)
    for node, started, finished in spans:
        by_node[node] += (finished - started).total_seconds()
    total = max(f for _, _, f in spans) - min(s.started_at for s in steps)
    return total.total_seconds(), dict(by_node)


def _scope_exact_match(
    store: ArtifactStore, manifest: Manifest, brief_ref: str, calls: list[LlmCallRecord]
) -> bool:
    if sum(c.purpose == "scope" for c in calls) != 1:
        return False
    plan = store.get_model(manifest.initial_plan_ref, ModelingPlan)
    brief = parse_brief(store.get(brief_ref))
    return all(getattr(plan, f) == getattr(brief, f) for f in SCOPE_IDENTITY_FIELDS)


def _summary(manifest: Manifest, name: RefName) -> dict[str, Any]:
    ref = manifest.refs.get(name)
    return dict(ref.summary) if ref is not None else {}


def run_metrics(db: Database, store: ArtifactStore, run_id: str) -> RunMetrics:
    """The metrics of the sealed run ``run_id``.

    Document, findings and responses facts come from the manifest's ref summaries; calls,
    tokens, cost and latency from the run's `llm_calls` rows; wall times from its `steps`.
    """
    with db.connection() as conn:
        run = runs.get_run(conn, run_id)
        calls = list_llm_calls(conn, run_id)
        steps = list_steps(conn, run_id)
    if run.manifest_hash is None:
        raise ValueError(f"run {run_id} has no sealed manifest")
    manifest = store.get_model(run.manifest_hash, Manifest)

    sections = _summary(manifest, "document_sections")
    findings = _summary(manifest, "findings")
    responses = _summary(manifest, "responses")
    n_sections = int(sections.get("n_sections", 0))
    n_flagged = int(sections.get("n_flagged_sections", 0))
    signoff = [a.decision for a in manifest.approvals if a.gate == "signoff"]

    cost_by_purpose: dict[str, float] = defaultdict(float)
    for call in calls:
        cost_by_purpose[call.purpose] += call.cost_usd
    latencies = [c.latency_ms for c in calls]
    wall_time, by_node = _wall_times(steps)

    return RunMetrics(
        pipeline_status=run.status,
        signoff_decision=signoff[-1] if signoff else None,
        n_sections=n_sections,
        n_flagged_sections=n_flagged,
        grounding_pass_rate=1 - n_flagged / n_sections if n_sections else 0.0,
        n_ungrounded_numbers=int(sections.get("n_ungrounded_numbers", 0)),
        completeness_complete=bool(_summary(manifest, "completeness").get("complete", False)),
        scope_exact_match=_scope_exact_match(store, manifest, run.brief_ref, calls),
        challenge_rounds=int(findings.get("round", 0)),
        remediation_rounds=int(responses.get("round", 0)),
        n_findings=int(findings.get("n_findings", 0)),
        findings_by_source=dict(findings.get("by_source", {})),
        findings_by_severity=dict(findings.get("by_severity", {})),
        findings_by_status=dict(findings.get("by_status", {})),
        n_sections_regenerated=int(responses.get("n_sections_regenerated", 0)),
        cost_usd=sum(c.cost_usd for c in calls),
        cost_by_purpose=dict(cost_by_purpose),
        n_llm_calls=len(calls),
        n_schema_retries=sum(not c.schema_valid for c in calls),
        input_tokens=sum(c.input_tokens for c in calls),
        output_tokens=sum(c.output_tokens for c in calls),
        latency_ms_p50=_percentile(latencies, 50),
        latency_ms_p95=_percentile(latencies, 95),
        wall_time_s=wall_time,
        wall_time_by_node_s=by_node,
    )


def flatten(metrics: RunMetrics) -> dict[str, float | int | bool | str | None]:
    """``metrics`` as one flat mapping; nested keys are joined with a dot."""
    flat: dict[str, float | int | bool | str | None] = {}
    for name, value in metrics.model_dump().items():
        if isinstance(value, dict):
            for key, inner in value.items():
                flat[f"{name}.{key}"] = inner
        else:
            flat[name] = value
    return flat
