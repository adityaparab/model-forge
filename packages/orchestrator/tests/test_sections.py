"""Splitting a rendered document back into its template sections."""

import pytest
from langgraph.runtime import Runtime
from modelforge_mcp.schemas import DocumentSections, SectionSpec, TemplateSections
from modelforge_mcp.servers.doc_renderer import ListTemplateSectionsInput
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.document import TEMPLATE_ID, document
from modelforge_orchestrator.schemas import RunState
from modelforge_orchestrator.sections import section_sentences, split_sections


def test_split_sections_ignores_numbering_and_reports_missing() -> None:
    template = TemplateSections(
        template_id="t",
        sections=[
            SectionSpec(id="data", title="Data", required=True, artifact_kinds=[], guidance="g"),
            SectionSpec(
                id="monitoring",
                title="Ongoing monitoring plan",
                required=True,
                artifact_kinds=[],
                guidance="g",
            ),
        ],
    )
    md = (
        "# Title\n\n## 7. Data\n\nBody one.\nLine two.\n\n## 99. Ongoing monitoring plan\n\n"
        "> **REQUIRES AUTHOR ATTENTION**: this section was not supplied.\n\n## 3. Unknown\n\nx"
    )
    assert split_sections(md, template) == {
        "data": "Body one.\nLine two.",
        "monitoring": "> **REQUIRES AUTHOR ATTENTION**: this section was not supplied.",
    }
    assert split_sections("# Title only", template) == {}


def test_section_sentences_splits_paragraphs_and_sentences() -> None:
    assert section_sentences("One. Two.\n\n- Three") == ["One.", "Two.", "Three"]


@pytest.mark.db
def test_split_sections_round_trips_the_rendered_fake_document(
    validated_state: RunState, deps: Deps, runtime: Runtime[Deps]
) -> None:
    refs = document(validated_state, runtime)["refs"]
    markdown = deps.store.get(refs["document_md"].id).decode()
    sections = deps.store.get_model(refs["document_sections"].id, DocumentSections)
    template: TemplateSections = deps.tools.call(
        "doc-renderer",
        "list_template_sections",
        ListTemplateSectionsInput(template_id=TEMPLATE_ID),
    ).output
    split = split_sections(markdown, template)
    assert set(split) == {s.id for s in template.sections}
    for section in sections.sections:
        assert split[section.section_id] == section.body_md.strip()
