"""`scope`: the scope agent proposes the initial modeling plan; on replay, the original's.

One structured gateway call carries the brief, the standards defaults and the knowledge passages
retrieved for planning; `scope_invariants` rejects a proposal that departs from the brief or
breaks the threshold bounds, and the gateway retries with the message before giving up.
"""

import json
import re
from collections.abc import Collection
from typing import Any

import yaml
from langgraph.runtime import Runtime
from modelforge_core.schemas import FeaturePolicy, Methodology, ModelingPlan, Thresholds
from modelforge_gateway.schemas import CompletionRequest, Message, RedactionContext
from modelforge_lineage import runs
from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.schemas import Passage, Passages, TemplateSections
from modelforge_mcp.servers.doc_renderer import ListTemplateSectionsInput
from modelforge_mcp.servers.knowledge import GetIndexInput, SearchRegulationInput
from pydantic import BaseModel

from modelforge_orchestrator.agent_schemas import ScopeNotes, ScopeProposal
from modelforge_orchestrator.briefs import Brief
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import brief_of, original_manifest
from modelforge_orchestrator.schemas import Manifest, RefName, RunState
from modelforge_orchestrator.steps import Step

__all__ = [
    "SCOPE_QUERIES",
    "SCOPE_SOURCES",
    "brief_block",
    "scope",
    "scope_invariants",
    "scope_request",
]

SCOPE_QUERIES: tuple[str, str] = (
    "required validation tests and thresholds for a PD scorecard",
    "required sections of a model development document",
)
SCOPE_SOURCES = ["modeling-standards"]
TEMPLATE_ID = "sr11-7-dev-doc"
PROMPT_NAME = "scope/propose_plan"
SYSTEM_PROMPT_NAME = "scope/system"
PROMPT_VERSION = 1

IDENTITY_FIELDS = (
    "model_name",
    "portfolio",
    "target",
    "intended_use",
    "regulatory_scope",
    "tier",
    "vintage_policy",
)
UNBOUNDED_THRESHOLDS = frozenset({"eo_low", "eo_high"})
CITATION = re.compile(r"\[K:([^\]]+)\]")


def scope_invariants(
    proposal: ScopeProposal, brief: Brief, retrieved: Collection[str]
) -> str | None:
    """``None`` when ``proposal`` keeps every invariant, else one message naming each violation."""
    plan = proposal.plan
    violations = [
        f"plan.{name} must equal the brief's {name}"
        for name in IDENTITY_FIELDS
        if getattr(plan, name) != getattr(brief, name)
    ]
    policy, bound = plan.feature_policy, brief.feature_policy
    if policy.max_features > bound.max_features:
        violations.append(
            f"feature_policy.max_features {policy.max_features} exceeds the brief's "
            f"{bound.max_features}"
        )
    missing = sorted(set(bound.exclusions) - set(policy.exclusions))
    if missing:
        violations.append(f"feature_policy.exclusions drops the brief's {', '.join(missing)}")
    t = plan.thresholds
    violations.extend(
        f"thresholds.{name} {getattr(t, name)} is outside [0, 1]"
        for name in Thresholds.model_fields
        if name not in UNBOUNDED_THRESHOLDS and not 0 <= getattr(t, name) <= 1
    )
    for ok, rule in (
        (t.auc_warn <= t.auc_min, "thresholds.auc_warn must be <= auc_min"),
        (t.psi_warn <= t.psi_fail, "thresholds.psi_warn must be <= psi_fail"),
        (t.gap_max <= t.gap_fail, "thresholds.gap_max must be <= gap_fail"),
        (t.iv_min < t.iv_max, "thresholds.iv_min must be < iv_max"),
        (t.eo_low < 1 < t.eo_high, "thresholds.eo_low < 1 < eo_high must hold"),
    ):
        if not ok:
            violations.append(rule)
    unknown = [cid for cid in CITATION.findall(proposal.rationale) if cid not in retrieved]
    if unknown:
        violations.append(f"rationale cites unknown passages {', '.join(unknown)}")
    return "; ".join(violations) or None


def _original_plan(deps: Deps, manifest: Manifest) -> ModelingPlan:
    return deps.store.get_model(manifest.initial_plan_ref, ModelingPlan)


def _passages(step: Step) -> list[Passage]:
    """The hits of every scope query, deduplicated by citation id in retrieval order."""
    seen: dict[str, Passage] = {}
    for query in SCOPE_QUERIES:
        request = SearchRegulationInput(query=query, sources=SCOPE_SOURCES, k=3)
        found: Passages = step.tool("knowledge", "search_regulation", request).output
        for hit in found.hits:
            seen.setdefault(hit.citation_id, hit)
    return list(seen.values())


def _defaults(step: Step) -> dict[str, Any]:
    request = ListTemplateSectionsInput(template_id=TEMPLATE_ID)
    template: TemplateSections = step.tool("doc-renderer", "list_template_sections", request).output
    return {
        "thresholds": Thresholds().model_dump(),
        "feature_policy": FeaturePolicy().model_dump(),
        "methodology": Methodology().model_dump(),
        "sections": [section.title for section in template.sections],
    }


def brief_block(brief_yaml: str) -> str:
    """The brief as the scope prompt shows it: ``brief_yaml`` without its ``restricted_terms``.

    The terms are the redaction scan's own list, so a prompt that carried them would always
    trip the scan. The mapping is re-dumped in its original key order; comments are dropped.
    """
    loaded = yaml.safe_load(brief_yaml)
    if not isinstance(loaded, dict):
        raise ValueError("the brief is not a YAML mapping")
    shown = {key: value for key, value in loaded.items() if key != "restricted_terms"}
    return yaml.safe_dump(shown, sort_keys=False, allow_unicode=True)


def scope_request(
    step: Step,
    brief: Brief,
    brief_yaml: str,
    defaults: dict[str, Any],
    passages: list[Passage],
) -> CompletionRequest:
    """The `ScopeProposal` request rendered from the scope prompts and `brief_block`."""
    registry = step.deps.gateway.registry
    system = registry.render(registry.get(SYSTEM_PROMPT_NAME, PROMPT_VERSION))
    passages_json = [
        {"citation_id": p.citation_id, "title": p.title, "section": p.section, "text": p.text}
        for p in passages
    ]
    user = registry.render(
        registry.get(PROMPT_NAME, PROMPT_VERSION),
        brief_yaml=brief_block(brief_yaml).rstrip("\n"),
        defaults_json=json.dumps(defaults, indent=2),
        passages_json=json.dumps(passages_json, indent=2),
    )
    return CompletionRequest(
        purpose="scope",
        prompt_name=PROMPT_NAME,
        prompt_version=PROMPT_VERSION,
        system=system,
        messages=[Message(role="user", content=user)],
        output_schema=ScopeProposal,
        redaction=RedactionContext(restricted_terms=brief.restricted_terms),
        metadata=step.call_metadata(),
    )


def scope(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "scope") as step:
        index = step.tool("knowledge", "get_index", GetIndexInput()).refs[0]
        refs: dict[RefName, ArtifactRef] = {"knowledge_index": index}
        if state.replay_of is not None and not state.rerun_llm:
            manifest = original_manifest(state, deps)
            plan = _original_plan(deps, manifest)
            original = manifest.refs.get("scope_notes")
            if original is not None:
                notes = deps.store.get_model(original.id, ScopeNotes)
                refs["scope_notes"] = step.store(
                    notes, kind="scope_notes", summary=original.summary
                )
        else:
            brief = brief_of(state, deps.store)
            passages = _passages(step)
            retrieved = [p.citation_id for p in passages]
            request = scope_request(
                step,
                brief,
                deps.store.get(state.brief_ref).decode("utf-8"),
                _defaults(step),
                passages,
            )

            def check(parsed: BaseModel) -> str | None:
                if not isinstance(parsed, ScopeProposal):
                    return f"expected a ScopeProposal, got {type(parsed).__name__}"
                return scope_invariants(parsed, brief, retrieved)

            proposal = step.llm(request, validate=check).parsed
            if not isinstance(proposal, ScopeProposal):
                raise RuntimeError(f"the scope call returned {type(proposal).__name__}")
            plan = proposal.plan
            notes = ScopeNotes(
                rationale=proposal.rationale,
                assumptions=proposal.assumptions,
                open_questions=proposal.open_questions,
                passages=retrieved,
            )
            refs["scope_notes"] = step.store(
                notes,
                kind="scope_notes",
                summary={"rationale": notes.rationale, "assumptions": notes.assumptions},
            )
        ref = step.store(plan, kind="modeling_plan")
        with deps.lineage.transaction() as conn:
            runs.set_plan_ref(conn, state.run_id, ref.id)
        return step.complete(
            {"plan": plan, "refs": refs, "cost_usd": state.cost_usd + step.spent_usd}
        )
