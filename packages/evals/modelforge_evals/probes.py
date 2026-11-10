"""Probe runs (spec 10.5): the real challenger node over a mutated copy of a sealed run.

Each probe is a run of its own (requester ``evals``) carrying the base run's identity, plan and
artifact rows, so the challenger's calls, findings rows and events never touch the base run. A
probe is marked ``completed`` without a manifest; probe runs are never replayed (`create_replay`
would accept one, `original_manifest` would then fail).
"""

import json
from datetime import UTC, datetime
from typing import Any

from modelforge_core.schemas import ModelingPlan, StrictModel
from modelforge_knowledge.index import KnowledgeIndex
from modelforge_lineage import runs, steps
from modelforge_lineage.events import append_event, list_events
from modelforge_lineage.ids import new_id
from modelforge_lineage.llm_calls import list_llm_calls
from modelforge_lineage.models import ArtifactRef, RunRecord
from modelforge_lineage.store import MEDIA_MARKDOWN
from modelforge_mcp.schemas import DatasetRef, TemplateSections
from modelforge_mcp.servers.doc_renderer import CheckCompletenessInput, ListTemplateSectionsInput
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import invoke_node
from modelforge_orchestrator.nodes.challenge import challenge
from modelforge_orchestrator.nodes.document import TEMPLATE_ID
from modelforge_orchestrator.schemas import Finding, FindingsList, Manifest, RefName, RunState
from modelforge_orchestrator.steps import Step

from modelforge_evals.defects import DefectCase, MutationContext, apply_mutation
from modelforge_evals.harness import EvalError

__all__ = ["ProbeResult", "document_template", "run_probe", "run_probes"]

COPIED_REFS: tuple[RefName, ...] = ("validation", "knowledge_index", "dictionary")
"""The base run's refs a probe state carries besides the mutated document and completeness."""


class ProbeResult(StrictModel):
    run_id: str
    defect_id: str
    applicable: bool
    """False when the mutation changes nothing; the challenger then does not run."""
    findings: list[Finding]
    cost_usd: float
    n_llm_calls: int


def document_template(deps: Deps) -> TemplateSections:
    """The development-document template's sections, from the doc-renderer."""
    request = ListTemplateSectionsInput(template_id=TEMPLATE_ID)
    return deps.tools.call("doc-renderer", "list_template_sections", request).output


def _base(deps: Deps, base_run_id: str) -> tuple[RunRecord, Manifest]:
    with deps.lineage.connection() as conn:
        base = runs.get_run(conn, base_run_id)
    if base.manifest_hash is None or base.plan_ref is None:
        raise ValueError(f"run {base_run_id} is not sealed; only a sealed run can be probed")
    return base, deps.store.get_model(base.manifest_hash, Manifest)


def _create_probe(deps: Deps, base: RunRecord) -> str:
    """The probe run row, running, with every artifact row of ``base`` and its plan ref."""
    assert base.plan_ref is not None
    probe_id = new_id("run")
    with deps.lineage.transaction() as conn:
        runs.create_run(
            conn,
            run_id=probe_id,
            requester="evals",
            profile=base.profile,
            brief_hash=base.brief_hash,
            brief_ref=base.brief_ref,
            dataset_ref=base.dataset_ref,
            seed=base.seed,
            cost_cap_usd=base.cost_cap_usd,
        )
        runs.set_status(conn, probe_id, "running")
        for row in steps.list_artifacts(conn, base.id):
            ref = ArtifactRef(
                id=row.id, kind=row.kind, media_type=row.media_type, size_bytes=row.size_bytes
            )
            steps.insert_artifact(
                conn,
                run_id=probe_id,
                ref=ref,
                uri=deps.store.uri(row.id),
                producer_step_id=None,
            )
        runs.set_plan_ref(conn, probe_id, base.plan_ref)
    return probe_id


def _state(deps: Deps, probe_id: str, base: RunRecord, manifest: Manifest) -> RunState:
    assert base.plan_ref is not None
    return RunState(
        run_id=probe_id,
        requester="evals",
        profile=base.profile,
        seed=base.seed,
        brief_hash=base.brief_hash,
        brief_ref=base.brief_ref,
        dataset_ref=DatasetRef.parse(base.dataset_ref),
        plan=deps.store.get_model(base.plan_ref, ModelingPlan),
        refs={name: manifest.refs[name] for name in COPIED_REFS},
        max_remediation_rounds=0,
    )


def _with_document(deps: Deps, state: RunState, mutated: str) -> RunState:
    """``state`` with the mutated document stored and its completeness recomputed on the probe."""
    with Step(deps, state, "probe") as step:
        document = step.store_bytes(
            mutated.encode("utf-8"), kind="document_md", media_type=MEDIA_MARKDOWN
        )
        request = CheckCompletenessInput(document_id=document.id, template_id=TEMPLATE_ID)
        completeness = step.tool("doc-renderer", "check_completeness", request).refs[0]
        refs: dict[RefName, ArtifactRef] = {"document_md": document, "completeness": completeness}
        step.complete({"refs": refs})
    return state.model_copy(update={"refs": {**state.refs, **refs}})


def _park(deps: Deps, probe_id: str, exc: Exception) -> None:
    """Record ``exc`` on the probe and mark it ``needs_attention``, as the worker parks a run.

    The error is the one the failing step recorded (its ``error.recorded`` event), else an
    internal record naming the exception.
    """
    with deps.lineage.connection() as conn:
        recorded = [e.payload for e in list_events(conn, probe_id) if e.type == "error.recorded"]
    error: dict[str, Any] = (
        recorded[-1]
        if recorded
        else {
            "node": "probe",
            "attempt": 0,
            "kind": "internal",
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "ts": datetime.now(UTC).isoformat(timespec="microseconds"),
        }
    )
    with deps.lineage.transaction() as conn:
        runs.set_error(conn, probe_id, error)
        runs.set_status(conn, probe_id, "needs_attention")


def run_probe(
    deps: Deps, base_run_id: str, case: DefectCase, template: TemplateSections
) -> ProbeResult:
    """Run the challenger over ``case``'s mutation of ``base_run_id``'s document on a probe run.

    An inapplicable case (the mutation changes nothing) still gets its probe run and event, but
    no challenger call. `EvalError` when ``deps.gateway`` serves another profile than the base
    run's. A failure of the probe's steps or the challenger parks the probe in
    ``needs_attention`` with its error recorded, then propagates.
    """
    base, manifest = _base(deps, base_run_id)
    if deps.gateway.profile.name != base.profile:
        raise EvalError(
            f"the gateway serves profile {deps.gateway.profile.name!r}, "
            f"run {base_run_id} ran under {base.profile!r}"
        )
    markdown = deps.store.get(manifest.refs["document_md"].id).decode("utf-8")
    ctx = MutationContext(
        validation=json.loads(deps.store.get(manifest.refs["validation"].id)),
        index=deps.store.get_model(manifest.refs["knowledge_index"].id, KnowledgeIndex),
    )
    mutated = apply_mutation(markdown, template, case.mutation, ctx)
    probe_id = _create_probe(deps, base)
    findings: list[Finding] = []
    if mutated is not None:
        try:
            state = _with_document(deps, _state(deps, probe_id, base, manifest), mutated)
            update, _ = invoke_node(challenge, state, deps)
        except Exception as exc:
            _park(deps, probe_id, exc)
            raise
        findings = deps.store.get_model(update["refs"]["findings"].id, FindingsList).findings
    with deps.lineage.transaction() as conn:
        append_event(
            conn, probe_id, "eval.probe", {"base_run_id": base_run_id, "defect_id": case.id}
        )
        runs.set_status(conn, probe_id, "completed")
    with deps.lineage.connection() as conn:
        calls = list_llm_calls(conn, probe_id)
    return ProbeResult(
        run_id=probe_id,
        defect_id=case.id,
        applicable=mutated is not None,
        findings=findings,
        cost_usd=sum(c.cost_usd for c in calls),
        n_llm_calls=len(calls),
    )


def run_probes(deps: Deps, base_run_id: str, cases: list[DefectCase]) -> list[ProbeResult]:
    """`run_probe` for each case, in order, over one template listing."""
    template = document_template(deps)
    return [run_probe(deps, base_run_id, case, template) for case in cases]
