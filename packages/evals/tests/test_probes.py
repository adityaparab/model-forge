"""Probe runs: the real challenger over a mutated document, recorded on a run of its own."""

import pytest
from modelforge_evals.defects import load_defects
from modelforge_evals.probes import document_template, run_probe, run_probes
from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.events import list_events
from modelforge_lineage.findings import list_findings
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.steps import list_artifacts, list_steps, list_tool_calls
from modelforge_orchestrator.deps import Deps

from .conftest import REPO

CASES = {c.id: c for c in load_defects(REPO / "evals/goldens/defects.yaml")}


@pytest.mark.db
def test_probe_run_is_isolated_and_records_calls(
    deps: Deps, database: Database, completed_run_id: str
) -> None:
    with database.connection() as conn:
        base = runs.get_run(conn, completed_run_id)
        base_calls = len(list_llm_calls(conn, completed_run_id))
        base_findings = len(list_findings(conn, completed_run_id))
        base_artifacts = {a.id: a.kind for a in list_artifacts(conn, completed_run_id)}

    result = run_probe(deps, completed_run_id, CASES["swapped_number"], document_template(deps))

    assert result.run_id != completed_run_id and result.applicable
    with database.connection() as conn:
        probe = runs.get_run(conn, result.run_id)
        calls = list_llm_calls(conn, result.run_id)
        events = list_events(conn, result.run_id)
        artifacts = {a.id: a.kind for a in list_artifacts(conn, result.run_id)}
        tools = [t.tool for t in list_tool_calls(conn, result.run_id)]
        nodes = [s.node for s in list_steps(conn, result.run_id)]
        findings = list_findings(conn, result.run_id)
        after = runs.get_run(conn, completed_run_id)
        after_calls = len(list_llm_calls(conn, completed_run_id))
        after_findings = len(list_findings(conn, completed_run_id))
    assert probe.status == "completed" and probe.requester == "evals"
    assert (probe.profile, probe.brief_hash, probe.dataset_ref, probe.seed) == (
        base.profile,
        base.brief_hash,
        base.dataset_ref,
        base.seed,
    )
    assert probe.plan_ref == base.plan_ref and probe.cost_cap_usd == base.cost_cap_usd
    assert probe.manifest_hash is None
    assert calls and {c.purpose for c in calls} == {"challenge"}
    assert result.n_llm_calls == len(calls)
    assert result.cost_usd == sum(c.cost_usd for c in calls)
    assert nodes == ["probe", "challenge"] and "check_completeness" in tools
    assert base_artifacts.items() <= artifacts.items()
    new = {kind for artifact_id, kind in artifacts.items() if artifact_id not in base_artifacts}
    assert new == {"document_md", "completeness", "findings", "llm_response"}
    assert {f["id"] for f in findings} == {f"{result.run_id}/{f.id}" for f in result.findings}
    probes = [e for e in events if e.type == "eval.probe"]
    assert [e.payload for e in probes] == [
        {"base_run_id": completed_run_id, "defect_id": "swapped_number"}
    ]
    # The base run is untouched.
    assert after.cost_usd == base.cost_usd and after.status == base.status
    assert (after_calls, after_findings) == (base_calls, base_findings)


@pytest.mark.db
def test_inapplicable_probe_runs_no_challenger(
    deps: Deps, database: Database, completed_run_id: str
) -> None:
    results = run_probes(deps, completed_run_id, [CASES["undiscussed_breach"]])
    assert [(r.defect_id, r.applicable, r.findings, r.n_llm_calls) for r in results] == [
        ("undiscussed_breach", False, [], 0)
    ]
    with database.connection() as conn:
        probe = runs.get_run(conn, results[0].run_id)
        calls = list_llm_calls(conn, results[0].run_id)
        events = [e.type for e in list_events(conn, results[0].run_id)]
    assert probe.status == "completed" and calls == [] and "eval.probe" in events
