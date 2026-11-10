"""`document`: the documentation agent writes each template section, then renders the document.

For every section of the template, in order: a query-planning call, knowledge retrieval for the
planned queries, a writing call over the section's excerpts and passages, alias expansion and the
grounding checks. A section that fails the checks is written once more with the failures; one
that still fails carries the author-attention banner and the list of failures. On replay (unless
the LLM steps are re-run) the original run's sections are copied instead.

Citation paths: an ``[A:<artifact_id>#<path>]`` marker's path is a dotted sequence of steps into
the artifact's JSON. A step applied to an object selects that key; a step applied to a list of
objects selects the element whose discriminator field equals the step, where the discriminator
is ``split`` for ``by_split``, ``test_id`` for ``tests``, ``feature`` for feature lists and
``grade`` for ``grades``. So ``by_split.oot.auc`` is the ``auc`` of the ``by_split`` element
whose ``split`` is ``oot``.
"""

import json
import math
from typing import Any, Literal, cast

from langgraph.runtime import Runtime
from modelforge_core.schemas import ColumnDictionary, StrictModel
from modelforge_gateway.schemas import CompletionRequest, Message, Purpose, RedactionContext
from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.schemas import (
    Citation,
    DocumentSection,
    DocumentSections,
    GroundingReport,
    Passage,
    Passages,
    SectionSpec,
    TemplateSections,
)
from modelforge_mcp.servers.doc_renderer import (
    BANNER,
    MARKER,
    CheckCompletenessInput,
    ListTemplateSectionsInput,
    RenderDocumentInput,
)
from modelforge_mcp.servers.knowledge import SearchRegulationInput
from pydantic import BaseModel

from modelforge_orchestrator.agent_schemas import QueryPlan, SectionDraft
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.excerpts import (
    ALIAS_DESCRIPTIONS,
    KIND_TO_REF,
    SectionContext,
    excerpt,
    for_section,
)
from modelforge_orchestrator.grounding import check_section, expand_aliases
from modelforge_orchestrator.nodes import brief_of, original_manifest, plan_of
from modelforge_orchestrator.schemas import RefName, RunState
from modelforge_orchestrator.steps import Step

__all__ = [
    "DOCUMENT_PROMPTS",
    "TEMPLATE_ID",
    "SectionOutcome",
    "SectionPrompts",
    "document",
    "excerpt_floats_to_four_sig_figs",
    "render_and_check",
    "restricted_terms",
    "write_section",
]

SERVER = "doc-renderer"
TEMPLATE_ID = "sr11-7-dev-doc"
QUERY_K = 3
GOVERNANCE_SECTION = "governance"
LLM_CALLS_PER_ATTEMPT = 2
PLAN_MAX_OUTPUT_TOKENS = 1024
WRITE_MAX_OUTPUT_TOKENS = 8192
"""A section draft is long, and a thinking model's reasoning counts against the limit."""
ALIAS_TO_KIND: dict[str, str] = {alias: kind for kind, alias in KIND_TO_REF.items()}


class SectionPrompts(StrictModel):
    """The prompts, their version and the gateway purpose one section is written under."""

    purpose: Purpose
    system: str
    plan_queries: str
    write: str
    version: int


DOCUMENT_PROMPTS = SectionPrompts(
    purpose="document",
    system="document/system",
    plan_queries="document/plan_queries",
    write="document/write_section",
    version=1,
)


class SectionOutcome(StrictModel):
    """One written section, the passages retrieved for it and how many attempts it took."""

    section: DocumentSection
    retrieved: list[str]
    attempts: int


def section_json(spec: SectionSpec, context: SectionContext, state: RunState) -> str:
    """The ``section`` block both document prompts render."""
    section: dict[str, Any] = {
        "id": spec.id,
        "title": spec.title,
        "guidance": spec.guidance,
        "aliases": {alias: ALIAS_DESCRIPTIONS[alias] for alias in context.aliases},
        "unavailable": context.unavailable,
        "regulatory_scope": plan_of(state).regulatory_scope,
    }
    if spec.id == GOVERNANCE_SECTION:
        section["run_artifacts"] = [
            {"ref": name, "kind": ref.kind, "id": ref.id} for name, ref in state.refs.items()
        ]
    return json.dumps(section, indent=2)


def excerpt_floats_to_four_sig_figs(value: Any) -> Any:
    """``value`` with every finite float rounded to four significant figures, nested or not.

    Applied to the excerpts sent to the writing prompt: full-precision floats would read as long
    digit runs to the gateway's redaction scan, and the writing rule is four significant figures
    anyway. Integers, strings and booleans pass through. Grounding still checks each stated
    number against the unrounded excerpt.
    """
    if isinstance(value, float):
        return float(f"{value:.4g}") if math.isfinite(value) else value
    if isinstance(value, dict):
        return {
            k: excerpt_floats_to_four_sig_figs(v) for k, v in cast("dict[str, Any]", value).items()
        }
    if isinstance(value, list):
        return [excerpt_floats_to_four_sig_figs(v) for v in cast("list[Any]", value)]
    return value


def _passages_json(passages: list[Passage]) -> str:
    return json.dumps(
        [
            {"citation_id": p.citation_id, "title": p.title, "section": p.section, "text": p.text}
            for p in passages
        ],
        indent=2,
    )


def _request(
    step: Step,
    spec: SectionSpec,
    prompts: SectionPrompts,
    prompt_name: str,
    output_schema: type[BaseModel],
    restricted_terms: list[str],
    max_output_tokens: int,
    **values: Any,
) -> CompletionRequest:
    registry = step.deps.gateway.registry
    system = registry.render(registry.get(prompts.system, prompts.version))
    user = registry.render(registry.get(prompt_name, prompts.version), **values)
    return CompletionRequest(
        purpose=prompts.purpose,
        prompt_name=prompt_name,
        prompt_version=prompts.version,
        system=system,
        messages=[Message(role="user", content=user)],
        output_schema=output_schema,
        max_output_tokens=max_output_tokens,
        redaction=RedactionContext(restricted_terms=restricted_terms),
        metadata=step.call_metadata(section_id=spec.id),
    )


def _retrieve(step: Step, queries: list[str]) -> list[Passage]:
    """The hits of every query, deduplicated by citation id in score order."""
    seen: dict[str, Passage] = {}
    for query in queries:
        found: Passages = step.tool(
            "knowledge", "search_regulation", SearchRegulationInput(query=query, k=QUERY_K)
        ).output
        for hit in found.hits:
            seen.setdefault(hit.citation_id, hit)
    return sorted(seen.values(), key=lambda p: p.score, reverse=True)


def _citations(body: str) -> list[Citation]:
    return [
        Citation(marker=m.group(0), kind=cast("Literal['A', 'K']", m.group(1)), target=m.group(2))
        for m in MARKER.finditer(body)
    ]


def _code_span(item: str) -> str:
    """``item`` as one inline code span: the completeness check reads no marker or regulation
    name inside one, and the grounding check reads no number.

    A code span cannot escape a backtick, so one inside ``item`` becomes ``'``; whitespace
    runs collapse so the span stays on its bullet's line.
    """
    return "`" + " ".join(item.replace("`", "'").split()) + "`"


def _bannered(body: str, report: GroundingReport) -> str:
    """``body`` under the author-attention banner and the list of failures.

    Each failure is quoted in a code span, so the banner does not re-inject the markers and
    regulatory sentences it lists into the rendered document's checks.
    """
    failures = [
        f"- {kind}: {_code_span(item)}"
        for kind, items in (
            ("ungrounded_numbers", report.ungrounded_numbers),
            ("unresolved_markers", report.unresolved_markers),
            ("uncited_regulatory_sentences", report.uncited_regulatory_sentences),
            ("foreign_passages", report.foreign_passages),
        )
        for item in items
    ]
    return BANNER + "\n\n" + "\n".join(failures) + "\n\n" + body


def _excerpt_kinds(context: SectionContext, state: RunState) -> dict[str, str]:
    """Artifact id to the kind whose excerpt grounding resolves its markers against.

    The section's aliases map through `KIND_TO_REF`; any other run artifact (the governance
    section lists them by id) is projected by its own kind. So a marker resolves against the
    same projection the writer saw, and a bulk list the excerpt hid grounds nothing.
    """
    kinds = {ref.id: ref.kind for ref in state.refs.values()}
    kinds.update({ref.id: ALIAS_TO_KIND[alias] for alias, ref in context.aliases.items()})
    return kinds


def write_section(
    step: Step,
    deps: Deps,
    state: RunState,
    spec: SectionSpec,
    context: SectionContext,
    restricted_terms: list[str],
    *,
    prompts: SectionPrompts = DOCUMENT_PROMPTS,
    findings_json: str | None = None,
) -> SectionOutcome:
    """Plan, retrieve, write and check one section; regenerate once, then banner on failure.

    ``prompts`` selects the prompts and gateway purpose (the documentation agent's by default);
    ``findings_json`` is passed to both prompts, which the documentation prompts ignore.
    """
    section = section_json(spec, context, state)
    excerpts = json.dumps(excerpt_floats_to_four_sig_figs(context.excerpts), indent=2)
    aliases = {alias: ref.id for alias, ref in context.aliases.items()}
    kind_of = _excerpt_kinds(context, state)

    def attempt(failures_json: str | None) -> tuple[str, GroundingReport, list[Passage]]:
        plan_request = _request(
            step,
            spec,
            prompts,
            prompts.plan_queries,
            QueryPlan,
            restricted_terms,
            PLAN_MAX_OUTPUT_TOKENS,
            section_json=section,
            findings_json=findings_json,
        )
        plan = step.llm(plan_request).parsed
        if not isinstance(plan, QueryPlan):
            raise RuntimeError(f"the query-planning call returned {type(plan).__name__}")
        passages = _retrieve(step, plan.queries)
        write_request = _request(
            step,
            spec,
            prompts,
            prompts.write,
            SectionDraft,
            restricted_terms,
            WRITE_MAX_OUTPUT_TOKENS,
            section_json=section,
            excerpts_json=excerpts,
            passages_json=_passages_json(passages),
            failures_json=failures_json,
            findings_json=findings_json,
        )
        draft = step.llm(write_request).parsed
        if not isinstance(draft, SectionDraft):
            raise RuntimeError(f"the section-writing call returned {type(draft).__name__}")
        expanded, _unknown = expand_aliases(draft.body_md, aliases)
        report = check_section(
            expanded,
            load_artifact=lambda artifact_id: excerpt(
                kind_of[artifact_id], json.loads(deps.store.get(artifact_id))
            ),
            retrieved={p.citation_id for p in passages},
        )
        return expanded, report, passages

    body, report, passages = attempt(None)
    attempts = 1
    if not report.ok:
        body, report, passages = attempt(report.model_dump_json(indent=2))
        attempts = 2
    citations = _citations(body)
    if not report.ok:
        body = _bannered(body, report)
    return SectionOutcome(
        section=DocumentSection(
            section_id=spec.id, body_md=body, citations=citations, grounding=report
        ),
        retrieved=[p.citation_id for p in passages],
        attempts=attempts,
    )


def restricted_terms(deps: Deps, state: RunState) -> list[str]:
    """The brief's restricted terms plus the dataset's original column names."""
    dictionary = deps.store.get_model(state.refs["dictionary"].id, ColumnDictionary)
    originals = [column.original for column in dictionary.columns]
    return [*brief_of(state, deps.store).restricted_terms, *originals]


def _outcomes(step: Step, deps: Deps, state: RunState) -> list[SectionOutcome]:
    request = ListTemplateSectionsInput(template_id=TEMPLATE_ID)
    template: TemplateSections = step.tool(SERVER, "list_template_sections", request).output
    plan_ref: ArtifactRef = deps.store.put_model(plan_of(state), kind="modeling_plan")
    terms = restricted_terms(deps, state)
    return [
        write_section(
            step, deps, state, spec, for_section(spec, state, deps.store, plan_ref), terms
        )
        for spec in template.sections
    ]


def build_sections(step: Step, deps: Deps, state: RunState) -> DocumentSections:
    """Every template section, written in template order."""
    return _sections(_outcomes(step, deps, state))


def _sections(outcomes: list[SectionOutcome]) -> DocumentSections:
    return DocumentSections(template_id=TEMPLATE_ID, sections=[o.section for o in outcomes])


def _summary(outcomes: list[SectionOutcome]) -> dict[str, Any]:
    reports = [o.section.grounding for o in outcomes if o.section.grounding is not None]
    return {
        "n_sections": len(outcomes),
        "n_flagged_sections": sum(not r.ok for r in reports),
        "n_ungrounded_numbers": sum(len(r.ungrounded_numbers) for r in reports),
        "n_llm_calls": sum(LLM_CALLS_PER_ATTEMPT * o.attempts for o in outcomes),
    }


def render_and_check(
    step: Step, state: RunState, sections_ref: ArtifactRef
) -> dict[RefName, ArtifactRef]:
    """Render ``sections_ref`` and check the document's completeness; the four document refs."""
    plan = plan_of(state)
    rendered = step.tool(
        SERVER,
        "render_document",
        RenderDocumentInput(
            template_id=TEMPLATE_ID,
            sections_id=sections_ref.id,
            title=f"{plan.model_name} development document",
            context={"model": plan.model_name, "dataset": state.dataset_ref.as_string()},
        ),
    ).output
    completeness = step.tool(
        SERVER,
        "check_completeness",
        CheckCompletenessInput(document_id=rendered.markdown.id, template_id=TEMPLATE_ID),
    ).refs[0]
    return {
        "document_sections": sections_ref,
        "document_md": rendered.markdown,
        "document_html": rendered.html,
        "completeness": completeness,
    }


def document(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "document") as step:
        if state.replay_of is not None and not state.rerun_llm:
            original = original_manifest(state, deps).refs["document_sections"]
            sections = deps.store.get_model(original.id, DocumentSections)
            sections_ref = step.store(sections, kind="document_sections", summary=original.summary)
        else:
            outcomes = _outcomes(step, deps, state)
            sections_ref = step.store(
                _sections(outcomes), kind="document_sections", summary=_summary(outcomes)
            )
        return step.complete(
            {
                "refs": render_and_check(step, state, sections_ref),
                "cost_usd": state.cost_usd + step.spent_usd,
            }
        )
