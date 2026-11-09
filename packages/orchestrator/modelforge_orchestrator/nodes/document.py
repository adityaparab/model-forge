"""`document`: a deterministic stand-in for the documentation agent (SP3).

The section bodies come from ``fixtures/sections.yaml`` (in template order), filled from the
summaries on the run's refs, and every number carries the citation marker of the artifact it
was read from.
"""

import math
from typing import Any

from langgraph.runtime import Runtime
from modelforge_lineage.store import ArtifactStore
from modelforge_mcp.schemas import DocumentSection, DocumentSections
from modelforge_mcp.servers.doc_renderer import CheckCompletenessInput, RenderDocumentInput

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import load_fixture, original_manifest, plan_of
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.steps import Step

__all__ = ["TEMPLATE_ID", "build_sections", "document", "format_number"]

SERVER = "doc-renderer"
TEMPLATE_ID = "sr11-7-dev-doc"


def format_number(x: float | int | None) -> str:
    """4 significant figures for floats, plain digits for ints, ``n/a`` if absent or non-finite."""
    if x is None:
        return "n/a"
    if isinstance(x, int):
        return str(x)
    if not math.isfinite(x):
        return "n/a"
    return f"{x:.4g}"


def build_sections(state: RunState, store: ArtifactStore) -> DocumentSections:
    del store  # every value is read from the refs' own summaries
    plan = plan_of(state)
    refs = state.refs
    profile = refs["profile"].summary
    selected: list[str] = refs["selection"].summary["selected"]
    champion = refs["champion"].summary
    auc: dict[str, float | None] = refs["discrimination"].summary["auc"]
    values = {
        "model_name": plan.model_name,
        "portfolio": plan.portfolio,
        "intended_use": plan.intended_use,
        "dataset": state.dataset_ref.as_string(),
        "n_rows": format_number(profile["n_rows"]),
        "target_rate": format_number(profile["target_rate"]),
        "n_features_selected": format_number(len(selected)),
        "features": ", ".join(champion["features"]),
        "overall": refs["validation"].summary["overall"],
        "auc_oot": format_number(auc.get("oot")),
        "auc_val": format_number(auc.get("val")),
        "fit_method": champion["fit_method"],
        "regulatory_scope": ", ".join(plan.regulatory_scope),
        "profile_id": refs["profile"].id,
        "selection_id": refs["selection"].id,
        "champion_id": refs["champion"].id,
        "discrimination_id": refs["discrimination"].id,
        "validation_id": refs["validation"].id,
    }
    bodies = load_fixture("sections.yaml")
    return DocumentSections(
        template_id=TEMPLATE_ID,
        sections=[
            DocumentSection(section_id=section_id, body_md=body.format(**values))
            for section_id, body in bodies.items()
        ],
    )


def document(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "document") as step:
        plan = plan_of(state)
        if state.replay_of is not None:
            original = original_manifest(state, deps).refs["document_sections"]
            sections = deps.store.get_model(original.id, DocumentSections)
            sections_ref = step.store(sections, kind="document_sections", summary=original.summary)
        else:
            sections = build_sections(state, deps.store)
            sections_ref = step.store(
                sections,
                kind="document_sections",
                summary={"n_sections": len(sections.sections)},
            )
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
        return step.complete(
            {
                "refs": {
                    "document_sections": sections_ref,
                    "document_md": rendered.markdown,
                    "document_html": rendered.html,
                    "completeness": completeness,
                }
            }
        )
