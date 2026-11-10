"""The challenger's lineage-only context and its validate hooks (spec 3.1 and 3.3).

`challenger_context` is the one place the challenger's model context is read from: the rendered
document, the validation report through its excerpt projection, the plan from ``runs.plan_ref``,
the knowledge index and, for the pre-checks only, the completeness report and the run's artifact
kinds. Nothing here reads the brief body, the scope notes, the section drafts, a raw model
response or the column dictionary.
"""

import json
from collections import Counter
from collections.abc import Collection
from typing import Any, cast

from modelforge_core.schemas import ModelingPlan, StrictModel
from modelforge_knowledge.index import KnowledgeIndex
from modelforge_lineage import runs, steps
from modelforge_lineage.citations import PathError, resolve_path
from modelforge_mcp.schemas import CompletenessReport, TemplateSections
from modelforge_mcp.servers.doc_renderer import MARKER, ListTemplateSectionsInput
from pydantic import BaseModel

from modelforge_orchestrator.agent_schemas import ReReviewDraft, ReviewDraft
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.excerpts import PLAN_ALIAS, excerpt
from modelforge_orchestrator.grounding import expand_aliases
from modelforge_orchestrator.nodes.document import TEMPLATE_ID
from modelforge_orchestrator.prechecks import VALIDATION_ALIAS, PrecheckContext
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.sections import split_sections
from modelforge_orchestrator.steps import Step

__all__ = [
    "CHALLENGER_ALIASES",
    "ChallengerContext",
    "challenger_context",
    "expand_evidence",
    "precheck_context",
    "validate_re_review",
    "validate_review",
]

CHALLENGER_ALIASES = (VALIDATION_ALIAS, PLAN_ALIAS)
"""The only aliases a challenger evidence marker may use."""


class ChallengerContext(StrictModel):
    """What the challenger and its pre-checks see, read from lineage by id."""

    document_md: str
    sections: dict[str, str]
    template: TemplateSections
    completeness: CompletenessReport
    validation_excerpt: dict[str, Any]
    validation_id: str
    plan: ModelingPlan
    plan_id: str
    artifact_kinds: dict[str, str]
    passages: dict[str, tuple[str, str]]
    """Every knowledge-index chunk: citation id to ``(doc_id, text)``."""


def challenger_context(state: RunState, deps: Deps, step: Step) -> ChallengerContext:
    refs = state.refs
    request = ListTemplateSectionsInput(template_id=TEMPLATE_ID)
    template: TemplateSections = step.tool("doc-renderer", "list_template_sections", request).output
    document_md = deps.store.get(refs["document_md"].id).decode("utf-8")
    validation_id = refs["validation"].id
    with deps.lineage.connection() as conn:
        plan_id = runs.get_run(conn, state.run_id).plan_ref
        kinds = {row.id: row.kind for row in steps.list_artifacts(conn, state.run_id)}
    if plan_id is None:
        raise RuntimeError(f"run {state.run_id} has no plan ref")
    index = deps.store.get_model(refs["knowledge_index"].id, KnowledgeIndex)
    validation = excerpt("validation_report", json.loads(deps.store.get(validation_id)))
    return ChallengerContext(
        document_md=document_md,
        sections=split_sections(document_md, template),
        template=template,
        completeness=deps.store.get_model(refs["completeness"].id, CompletenessReport),
        validation_excerpt=cast("dict[str, Any]", validation),
        validation_id=validation_id,
        plan=deps.store.get_model(plan_id, ModelingPlan),
        plan_id=plan_id,
        artifact_kinds=kinds,
        passages={c.citation_id: (c.doc_id, c.text) for c in index.chunks},
    )


def precheck_context(ctx: ChallengerContext, deps: Deps) -> PrecheckContext:
    """The pre-checks' view of ``ctx``; cited artifacts are loaded raw by id from the store."""
    return PrecheckContext(
        sections=ctx.sections,
        template=ctx.template,
        completeness=ctx.completeness,
        validation=ctx.validation_excerpt,
        validation_id=ctx.validation_id,
        artifact_kinds=ctx.artifact_kinds,
        load_artifact=lambda artifact_id: json.loads(deps.store.get(artifact_id)),
        passages=ctx.passages,
    )


def expand_evidence(evidence: list[str], ctx: ChallengerContext) -> list[str]:
    """``evidence`` with ``[A:validation#p]`` and ``[A:plan#p]`` rewritten to artifact ids."""
    aliases = {VALIDATION_ALIAS: ctx.validation_id, PLAN_ALIAS: ctx.plan_id}
    return [expand_aliases(item, aliases)[0] for item in evidence]


def _evidence_problem(
    item: str,
    section_ids: set[str],
    excerpts: dict[str, Any],
    retrieved: Collection[str],
) -> str | None:
    if item in section_ids:
        return None
    match = MARKER.fullmatch(item)
    if match is None:
        return "is neither a template section id nor a single citation marker"
    if match.group(1) == "K":
        if match.group(2) in retrieved:
            return None
        return "cites a passage that was not retrieved for this review"
    alias, _, path = match.group(2).partition("#")
    if alias not in CHALLENGER_ALIASES:
        return f"uses the alias {alias!r}; only `validation` and `plan` may be cited"
    try:
        resolve_path(excerpts[alias], path)
    except PathError:
        return f"has a path that does not resolve in the {alias} excerpt"
    return None


def validate_review(
    draft: BaseModel, ctx: ChallengerContext, retrieved: Collection[str]
) -> str | None:
    """The review call's hook: every evidence entry follows the evidence grammar."""
    if not isinstance(draft, ReviewDraft):
        return f"expected a ReviewDraft, got {type(draft).__name__}"
    section_ids = {spec.id for spec in ctx.template.sections}
    excerpts = {
        VALIDATION_ALIAS: ctx.validation_excerpt,
        PLAN_ALIAS: ctx.plan.model_dump(mode="json"),
    }
    problems: list[str] = []
    for n, finding in enumerate(draft.findings, start=1):
        label = f"finding {n} ({finding.title!r})"
        if not finding.evidence:
            problems.append(f"{label} carries no evidence")
        for item in finding.evidence:
            problem = _evidence_problem(item, section_ids, excerpts, retrieved)
            if problem is not None:
                problems.append(f"{label}: evidence {item!r} {problem}")
    if not problems:
        return None
    return "Fix each evidence entry:\n" + "\n".join(f"- {p}" for p in problems)


def validate_re_review(draft: BaseModel, expected_ids: set[str]) -> str | None:
    """The re-review call's hook: exactly one verdict per finding given, and no other id."""
    if not isinstance(draft, ReReviewDraft):
        return f"expected a ReReviewDraft, got {type(draft).__name__}"
    counts = Counter(v.finding_id for v in draft.verdicts)
    problems = [f"no verdict for {i}" for i in sorted(expected_ids - counts.keys())]
    problems += [f"{i} has {counts[i]} verdicts" for i in sorted(counts) if counts[i] > 1]
    problems += [f"{i} is not a finding under review" for i in sorted(counts.keys() - expected_ids)]
    if not problems:
        return None
    return "Return exactly one verdict per finding given:\n" + "\n".join(f"- {p}" for p in problems)
