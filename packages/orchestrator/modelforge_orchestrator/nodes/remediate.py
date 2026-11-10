"""`remediate`: the developer answers the challenger's open findings (spec 4).

One gateway call per `open` finding accepts it (naming the sections to rewrite) or disputes it
with artifact evidence. The sections the accepted findings name are rewritten through the
document node's write path under the remediation prompts, the document is re-rendered and its
completeness checked again (a round that rewrites nothing re-renders nothing), and the node
stores the cumulative `responses` artifact and the `findings` artifact with the new statuses,
then hands back to `challenge`. On replay (unless the LLM steps are re-run) the original run's
responses are copied and nothing is re-rendered: the document node already copied the final
sections.
"""

import json
import re
from typing import Any, Literal

from langgraph.runtime import Runtime
from langgraph.types import Command
from modelforge_lineage.citations import PathError, resolve_path
from modelforge_lineage.findings import set_response
from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.schemas import DocumentSections, TemplateSections
from modelforge_mcp.servers.doc_renderer import MARKER, ListTemplateSectionsInput
from pydantic import BaseModel

from modelforge_orchestrator.agent_schemas import FindingResponseDraft
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.excerpts import PLAN_ALIAS, for_section
from modelforge_orchestrator.grounding import expand_aliases
from modelforge_orchestrator.nodes import original_manifest, plan_of
from modelforge_orchestrator.nodes.challenge import findings_summary
from modelforge_orchestrator.nodes.document import (
    TEMPLATE_ID,
    SectionPrompts,
    agent_request,
    excerpt_floats_to_four_sig_figs,
    render_and_check,
    restricted_terms,
    write_section,
)
from modelforge_orchestrator.schemas import (
    Finding,
    FindingResponse,
    FindingsList,
    RefName,
    ResponsesList,
    RunState,
)
from modelforge_orchestrator.sections import split_sections
from modelforge_orchestrator.steps import Step

__all__ = [
    "REMEDIATE_PROMPTS",
    "regenerate",
    "remediate",
    "respond",
    "responses_summary",
    "validate_response",
]

REMEDIATE_PROMPTS = SectionPrompts(
    purpose="remediate",
    system="remediate/system",
    plan_queries="remediate/plan_queries",
    write="remediate/rewrite_section",
    version=1,
)
RESPOND_PROMPT = "remediate/respond"
RESPOND_MAX_OUTPUT_TOKENS = 2048
_FINDING_FIELDS = {
    "id",
    "round",
    "severity",
    "category",
    "title",
    "description",
    "evidence",
    "required_action",
    "status",
    "source",
    "check_key",
}
"""The finding fields the respond prompt describes."""
_REWRITE_FIELDS = {"title", "description", "evidence", "required_action"}
"""The finding fields the rewrite prompts describe."""
_ARTIFACT_MARKER = re.compile(r"\[A:([^#\]]+)#")


def validate_response(
    draft: BaseModel, aliases: dict[str, Any], template_ids: set[str]
) -> str | None:
    """The respond call's hook.

    ``aliases`` maps each alias the developer may cite to its excerpt. A dispute carries at
    least one ``[A:<alias>#<path>]`` marker resolving in those excerpts; an accept has a
    non-empty action; every section to regenerate is a template section id.
    """
    if not isinstance(draft, FindingResponseDraft):
        return f"expected a FindingResponseDraft, got {type(draft).__name__}"
    problems: list[str] = []
    if draft.decision == "dispute" and not any(_resolves(item, aliases) for item in draft.evidence):
        problems.append(
            "a dispute needs evidence: at least one [A:<alias>#<path>] marker whose alias is one "
            f"of {sorted(aliases)} and whose path resolves in that excerpt; got {draft.evidence!r}"
        )
    if draft.decision == "accept" and not draft.action.strip():
        problems.append("an accept needs a concrete, non-empty action")
    unknown = [s for s in draft.sections_to_regenerate if s not in template_ids]
    if unknown:
        problems.append(
            f"sections_to_regenerate names {unknown!r}, which are not template section ids; "
            f"use only {sorted(template_ids)}"
        )
    if not problems:
        return None
    return "Fix the response:\n" + "\n".join(f"- {p}" for p in problems)


def _resolves(item: str, aliases: dict[str, Any]) -> bool:
    match = MARKER.fullmatch(item)
    if match is None or match.group(1) != "A":
        return False
    alias, _, path = match.group(2).partition("#")
    if alias not in aliases:
        return False
    try:
        resolve_path(aliases[alias], path)
    except PathError:
        return False
    return True


def _collapse(item: str, alias_of: dict[str, str]) -> str:
    """``item`` with each ``[A:<artifact_id>#`` the developer can cite written by its alias."""
    return _ARTIFACT_MARKER.sub(lambda m: f"[A:{alias_of.get(m.group(1), m.group(1))}#", item)


def _plan_ref(deps: Deps, state: RunState) -> ArtifactRef:
    return deps.store.put_model(plan_of(state), kind="modeling_plan")


def respond(
    step: Step,
    deps: Deps,
    state: RunState,
    finding: Finding,
    sections: dict[str, str],
    template: TemplateSections,
    restricted: list[str],
) -> FindingResponse:
    """The developer's answer to ``finding``, its evidence expanded to artifact ids.

    The model sees the bodies of the sections the finding's evidence names (every section when
    it names none) and the excerpts those sections may cite, plus the plan's.
    """
    template_ids = {spec.id for spec in template.sections}
    named = [item for item in dict.fromkeys(finding.evidence) if item in template_ids]
    specs = [spec for spec in template.sections if not named or spec.id in named]
    plan_ref = _plan_ref(deps, state)
    refs: dict[str, ArtifactRef] = {PLAN_ALIAS: plan_ref}
    excerpts: dict[str, Any] = {PLAN_ALIAS: plan_of(state).model_dump(mode="json")}
    for spec in specs:
        context = for_section(spec, state, deps.store, plan_ref)
        refs.update(context.aliases)
        excerpts.update(context.excerpts)
    ids = {alias: ref.id for alias, ref in refs.items()}
    alias_of = {ref_id: alias for alias, ref_id in ids.items()}
    shown = finding.model_dump(mode="json", include=_FINDING_FIELDS)
    shown["evidence"] = [_collapse(item, alias_of) for item in finding.evidence]
    request = agent_request(
        step,
        purpose=REMEDIATE_PROMPTS.purpose,
        system=REMEDIATE_PROMPTS.system,
        prompt_name=RESPOND_PROMPT,
        version=REMEDIATE_PROMPTS.version,
        output_schema=FindingResponseDraft,
        restricted_terms=restricted,
        max_output_tokens=RESPOND_MAX_OUTPUT_TOKENS,
        finding_json=json.dumps(shown, indent=2),
        sections_json=json.dumps({s.id: sections.get(s.id, "") for s in specs}, indent=2),
        excerpts_json=json.dumps(excerpt_floats_to_four_sig_figs(excerpts), indent=2),
    )
    draft = step.llm(
        request, validate=lambda parsed: validate_response(parsed, excerpts, template_ids)
    ).parsed
    if not isinstance(draft, FindingResponseDraft):
        raise RuntimeError(f"the respond call returned {type(draft).__name__}")
    accepted = draft.decision == "accept"
    return FindingResponse(
        finding_id=finding.id,
        decision=draft.decision,
        action=draft.action,
        evidence=[expand_aliases(item, ids)[0] for item in draft.evidence],
        round=state.counters.remediation_rounds + 1,
        sections_regenerated=list(dict.fromkeys(draft.sections_to_regenerate)) if accepted else [],
    )


def regenerate(
    step: Step,
    deps: Deps,
    state: RunState,
    template: TemplateSections,
    section_ids: set[str],
    findings_by_section: dict[str, list[Finding]],
    restricted: list[str],
) -> DocumentSections:
    """The current sections with each of ``section_ids`` rewritten against its findings."""
    current = deps.store.get_model(state.refs["document_sections"].id, DocumentSections)
    by_id = {s.section_id: s for s in current.sections}
    plan_ref = _plan_ref(deps, state)
    for spec in template.sections:
        if spec.id not in section_ids:
            continue
        context = for_section(spec, state, deps.store, plan_ref)
        alias_of = {ref.id: alias for alias, ref in context.aliases.items()}
        shown = [
            {
                **f.model_dump(mode="json", include=_REWRITE_FIELDS),
                "evidence": [_collapse(item, alias_of) for item in f.evidence],
            }
            for f in findings_by_section.get(spec.id, [])
        ]
        outcome = write_section(
            step,
            deps,
            state,
            spec,
            context,
            restricted,
            prompts=REMEDIATE_PROMPTS,
            findings_json=json.dumps(shown, indent=2),
        )
        by_id[spec.id] = outcome.section
    return DocumentSections(
        template_id=current.template_id,
        sections=[by_id[s.section_id] for s in current.sections],
    )


def responses_summary(responses: list[FindingResponse], round: int) -> dict[str, Any]:
    """The `responses` ref summary over the cumulative list."""
    decisions = [r.decision for r in responses]
    return {
        "n_responses": len(responses),
        "n_accepted": decisions.count("accept"),
        "n_disputed": decisions.count("dispute"),
        "n_sections_regenerated": len({s for r in responses for s in r.sections_regenerated}),
        "round": round,
    }


def _sections_summary(sections: DocumentSections, n_regenerated: int) -> dict[str, Any]:
    reports = [s.grounding for s in sections.sections if s.grounding is not None]
    return {
        "n_sections": len(sections.sections),
        "n_flagged_sections": sum(not r.ok for r in reports),
        "n_ungrounded_numbers": sum(len(r.ungrounded_numbers) for r in reports),
        "n_sections_regenerated": n_regenerated,
    }


def _answered(finding: Finding, response: FindingResponse) -> Finding:
    return finding.model_copy(
        update={
            "status": "accepted" if response.decision == "accept" else "disputed",
            "response": response.action,
            "response_evidence": response.evidence,
        }
    )


def _set_rows(deps: Deps, state: RunState, findings: list[Finding]) -> None:
    with deps.lineage.transaction() as conn:
        for finding in findings:
            set_response(
                conn,
                state.run_id,
                f"{state.run_id}/{finding.id}",
                status=finding.status,
                response=finding.response or "",
                response_evidence=finding.response_evidence,
            )


def _replay(step: Step, deps: Deps, state: RunState) -> dict[str, Any]:
    """The original run's responses, copied; the rows take the copied findings' final state."""
    original = original_manifest(state, deps).refs["responses"]
    copied = deps.store.get_model(original.id, ResponsesList)
    ref = step.store(copied, kind="responses", summary=original.summary)
    copied_findings = deps.store.get_model(state.refs["findings"].id, FindingsList).findings
    findings = {f.id: f for f in copied_findings}
    answered = dict.fromkeys(r.finding_id for r in copied.responses)
    _set_rows(deps, state, [findings[i] for i in answered])
    return {
        "refs": {"responses": ref},
        "counters": state.counters.model_copy(update={"remediation_rounds": 1}),
    }


def _round(step: Step, deps: Deps, state: RunState) -> dict[str, Any]:
    """One remediation round: respond, record, regenerate, re-render."""
    round = state.counters.remediation_rounds + 1
    findings = deps.store.get_model(state.refs["findings"].id, FindingsList)
    request = ListTemplateSectionsInput(template_id=TEMPLATE_ID)
    template: TemplateSections = step.tool("doc-renderer", "list_template_sections", request).output
    document_md = deps.store.get(state.refs["document_md"].id).decode("utf-8")
    sections = split_sections(document_md, template)
    restricted = restricted_terms(deps, state)
    new = {
        f.id: respond(step, deps, state, f, sections, template, restricted)
        for f in findings.findings
        if f.status == "open"
    }
    updated = [_answered(f, new[f.id]) if f.id in new else f for f in findings.findings]
    answered = [f for f in updated if f.id in new]
    _set_rows(deps, state, answered)

    refs: dict[RefName, ArtifactRef] = {}
    findings_by_section: dict[str, list[Finding]] = {}
    for finding in answered:
        for section_id in new[finding.id].sections_regenerated:
            findings_by_section.setdefault(section_id, []).append(finding)
    if findings_by_section:
        regenerated = regenerate(
            step, deps, state, template, set(findings_by_section), findings_by_section, restricted
        )
        sections_ref = step.store(
            regenerated,
            kind="document_sections",
            summary=_sections_summary(regenerated, len(findings_by_section)),
        )
        refs.update(render_and_check(step, state, sections_ref))

    previous = state.refs.get("responses")
    earlier = [] if previous is None else deps.store.get_model(previous.id, ResponsesList).responses
    cumulative = [*earlier, *new.values()]
    refs["responses"] = step.store(
        ResponsesList(round=round, responses=cumulative),
        kind="responses",
        summary=responses_summary(cumulative, round),
    )
    refs["findings"] = step.store(
        FindingsList(round=findings.round, findings=updated),
        kind="findings",
        summary=findings_summary(updated, findings.round),
    )
    return {
        "refs": refs,
        "counters": state.counters.model_copy(update={"remediation_rounds": round}),
    }


def remediate(state: RunState, runtime: Runtime[Deps]) -> Command[Literal["challenge"]]:
    deps = runtime.context
    with Step(deps, state, "remediate") as step:
        if state.replay_of is not None and not state.rerun_llm:
            update = _replay(step, deps, state)
        else:
            update = _round(step, deps, state)
        update["cost_usd"] = state.cost_usd + step.spent_usd
        step.complete(update)
    return Command(goto="challenge", update=update)
