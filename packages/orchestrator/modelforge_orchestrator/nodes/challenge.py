"""`challenge`: a deterministic stand-in for the challenger agent (SP4), round 1.

The findings come from ``fixtures/findings.yaml``: the dataset-proxy finding always, and the
calibration finding when the binomial calibration test did not pass.
"""

from typing import Any

from langgraph.runtime import Runtime
from modelforge_lineage.findings import insert_findings
from modelforge_lineage.store import ArtifactStore

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import load_fixture, original_manifest
from modelforge_orchestrator.schemas import Finding, FindingsList, RunState
from modelforge_orchestrator.steps import Step

__all__ = ["build_findings", "challenge"]

_SEVERITY_ORDER = ("critical", "high", "medium", "low")


def build_findings(state: RunState, store: ArtifactStore) -> FindingsList:
    del store  # the calibration status is read from the validation ref's summary
    validation = state.refs["validation"]
    fixture = load_fixture("findings.yaml")
    ids = ["F-1"]
    if validation.summary["statuses"]["cal.binomial"] != "pass":
        ids.append("F-2")
    findings = [
        Finding(
            id=finding_id, round=1, evidence=[validation.id], status="open", **fixture[finding_id]
        )
        for finding_id in ids
    ]
    return FindingsList(round=1, findings=findings)


def _max_severity(findings: FindingsList) -> str | None:
    present = {f.severity for f in findings.findings}
    return next((s for s in _SEVERITY_ORDER if s in present), None)


def challenge(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "challenge") as step:
        if state.replay_of is not None:
            original = original_manifest(state, deps).refs["findings"]
            findings = deps.store.get_model(original.id, FindingsList)
            ref = step.store(findings, kind="findings", summary=original.summary)
        else:
            findings = build_findings(state, deps.store)
            ref = step.store(
                findings,
                kind="findings",
                summary={
                    "n_findings": len(findings.findings),
                    "max_severity": _max_severity(findings),
                },
            )
        rows = [
            {**f.model_dump(mode="json", exclude={"id"}), "id": f"{state.run_id}/{f.id}"}
            for f in findings.findings
        ]
        with deps.lineage.transaction() as conn:
            insert_findings(conn, state.run_id, rows)
        return step.complete({"refs": {"findings": ref}})
