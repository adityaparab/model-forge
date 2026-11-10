import asyncio
from collections.abc import Callable

from mcp import Client
from mcp.types import CallToolResult
from modelforge_core.schemas import StrictModel
from modelforge_lineage.models import ArtifactRef
from modelforge_lineage.store import ArtifactStore
from modelforge_mcp.common import ServerName, ServerSettings
from modelforge_mcp.registry import build_server
from modelforge_mcp.schemas import (
    CompletenessReport,
    DocumentSection,
    DocumentSections,
    TemplateSections,
)
from modelforge_mcp.servers.doc_renderer import (
    BANNER,
    CheckCompletenessInput,
    ListTemplateSectionsInput,
    RenderDocumentInput,
    RenderOutput,
)

CallFn = Callable[[ServerName, str, StrictModel], CallToolResult]
TEMPLATE = "sr11-7-dev-doc"


def test_list_template_sections(call: CallFn) -> None:
    out = TemplateSections.model_validate(
        call(
            "doc-renderer",
            "list_template_sections",
            ListTemplateSectionsInput(template_id=TEMPLATE),
        ).structured_content
    )
    assert out.template_id == TEMPLATE
    assert [s.id for s in out.sections][:3] == [
        "executive_summary",
        "purpose_and_use",
        "regulatory_context",
    ]
    assert len(out.sections) == 11
    assert not out.sections[-1].required
    assert all(s.required for s in out.sections[:-1])
    performance = next(s for s in out.sections if s.id == "performance")
    assert "validation_report" in performance.artifact_kinds


def test_render_is_deterministic_and_flags_missing(call: CallFn, store: ArtifactStore) -> None:
    sections = DocumentSections(
        template_id=TEMPLATE,
        sections=[
            DocumentSection(
                section_id="executive_summary",
                body_md="AUC 0.81 [A:"
                + "a" * 64
                + "#by_split.oot.auc]. SR 11-7 requires effective challenge [K:sr117-s4].",
            )
        ],
    )
    sid = store.put_model(sections, kind="document_sections").id
    req = RenderDocumentInput(
        template_id=TEMPLATE,
        sections_id=sid,
        title="PD model",
        context={"model": "m", "dataset": "synthetic_credit"},
    )
    a = RenderOutput.model_validate(call("doc-renderer", "render_document", req).structured_content)
    b = RenderOutput.model_validate(call("doc-renderer", "render_document", req).structured_content)
    md = store.get(a.markdown.id).decode()
    assert a.markdown.id == b.markdown.id and a.html.id == b.html.id
    assert a.markdown.kind == "document_md" and a.html.kind == "document_html"
    assert md.startswith("# PD model\n")
    assert "**model**: m  **dataset**: synthetic_credit" in md
    assert md.count("REQUIRES AUTHOR ATTENTION") == 10 and "## 1. Executive summary" in md
    assert "## 11. Appendices" in md
    html = store.get(a.html.id).decode()
    assert html.startswith("<!doctype html>") and "<h1>" in html
    assert "<title>PD model</title>" in html
    assert a.markdown.summary == {"sections_rendered": 1, "sections_missing": 10}
    assert a.html.summary == a.markdown.summary


def test_render_without_context_has_no_context_line(call: CallFn, store: ArtifactStore) -> None:
    sid = store.put_model(
        DocumentSections(template_id=TEMPLATE, sections=[]), kind="document_sections"
    ).id
    out = RenderOutput.model_validate(
        call(
            "doc-renderer",
            "render_document",
            RenderDocumentInput(template_id=TEMPLATE, sections_id=sid, title="<T>"),
        ).structured_content
    )
    md = store.get(out.markdown.id).decode()
    assert md.startswith("# <T>\n\n## 1. Executive summary")
    assert md.count(BANNER) == 11
    assert "<title>&lt;T&gt;</title>" in store.get(out.html.id).decode()


def test_check_completeness(call: CallFn, store: ArtifactStore) -> None:
    body = (
        "Fine [A:"
        + "b" * 64
        + "]. Bad marker [A:not-a-hash]. CECL requires lifetime losses. "
        + "IFRS 9 is cited [K:ifrs9-1]."
    )
    sid = store.put_model(
        DocumentSections(
            template_id=TEMPLATE,
            sections=[
                DocumentSection(section_id=s, body_md=body) for s in ("executive_summary", "data")
            ],
        ),
        kind="document_sections",
    ).id
    doc = RenderOutput.model_validate(
        call(
            "doc-renderer",
            "render_document",
            RenderDocumentInput(template_id=TEMPLATE, sections_id=sid, title="t"),
        ).structured_content
    )
    ref = ArtifactRef.model_validate(
        call(
            "doc-renderer",
            "check_completeness",
            CheckCompletenessInput(document_id=doc.markdown.id, template_id=TEMPLATE),
        ).structured_content
    )
    report = store.get_model(ref.id, CompletenessReport)
    assert ref.kind == "completeness"
    assert report.document_id == doc.markdown.id and report.template_id == TEMPLATE
    assert report.complete is False
    assert "purpose_and_use" in report.missing_sections
    assert "appendices" not in report.missing_sections
    assert "executive_summary" not in report.missing_sections
    assert report.unresolved_markers == ["[A:not-a-hash]"] * 2
    assert report.uncited_regulatory_sentences == ["CECL requires lifetime losses."] * 2
    assert ref.summary == {
        "complete": False,
        "missing_sections": report.missing_sections,
        "n_unresolved_markers": 2,
        "n_uncited": 2,
    }


def test_complete_document(call: CallFn, store: ArtifactStore) -> None:
    template = TemplateSections.model_validate(
        call(
            "doc-renderer",
            "list_template_sections",
            ListTemplateSectionsInput(template_id=TEMPLATE),
        ).structured_content
    )
    sid = store.put_model(
        DocumentSections(
            template_id=TEMPLATE,
            sections=[
                DocumentSection(section_id=s.id, body_md="Basel is cited [K:basel-1].")
                for s in template.sections
                if s.required
            ]
            + [DocumentSection(section_id="not_in_template", body_md="ignored")],
        ),
        kind="document_sections",
    ).id
    doc = RenderOutput.model_validate(
        call(
            "doc-renderer",
            "render_document",
            RenderDocumentInput(template_id=TEMPLATE, sections_id=sid, title="t"),
        ).structured_content
    )
    assert doc.markdown.summary == {"sections_rendered": 10, "sections_missing": 1}
    assert "ignored" not in store.get(doc.markdown.id).decode()
    ref = ArtifactRef.model_validate(
        call(
            "doc-renderer",
            "check_completeness",
            CheckCompletenessInput(document_id=doc.markdown.id, template_id=TEMPLATE),
        ).structured_content
    )
    assert store.get_model(ref.id, CompletenessReport).complete is True


def test_unknown_template_is_invalid_input(call: CallFn) -> None:
    for template_id in ("nope", "../templates", "Bad_Id"):
        result = call(
            "doc-renderer",
            "list_template_sections",
            ListTemplateSectionsInput(template_id=template_id),
        )
        assert result.is_error
        assert "invalid_input" in str(result.content)


def test_unknown_sections_artifact_is_not_found(call: CallFn) -> None:
    result = call(
        "doc-renderer",
        "render_document",
        RenderDocumentInput(template_id=TEMPLATE, sections_id="0" * 64, title="t"),
    )
    assert result.is_error and "artifact_not_found" in str(result.content)


def test_tool_versions_are_published(settings: ServerSettings) -> None:
    async def _list():
        async with Client(build_server("doc-renderer", settings)) as c:
            return (await c.list_tools()).tools

    tools = asyncio.run(_list())
    assert {t.name for t in tools} == {
        "list_template_sections",
        "render_document",
        "check_completeness",
    }
    assert all(t.meta == {"version": "1.0.0"} and t.output_schema is not None for t in tools)


def test_empty_marker_targets_are_unresolved(call: CallFn, store: ArtifactStore) -> None:
    body = "Empty knowledge [K:] and empty artifact [A:] markers."
    sid = store.put_model(
        DocumentSections(
            template_id=TEMPLATE,
            sections=[DocumentSection(section_id="executive_summary", body_md=body)],
        ),
        kind="document_sections",
    ).id
    doc = RenderOutput.model_validate(
        call(
            "doc-renderer",
            "render_document",
            RenderDocumentInput(template_id=TEMPLATE, sections_id=sid, title="t"),
        ).structured_content
    )
    ref = ArtifactRef.model_validate(
        call(
            "doc-renderer",
            "check_completeness",
            CheckCompletenessInput(document_id=doc.markdown.id, template_id=TEMPLATE),
        ).structured_content
    )
    report = store.get_model(ref.id, CompletenessReport)
    assert report.unresolved_markers == ["[K:]", "[A:]"]


def test_marker_targets_may_nest_one_bracket_level(call: CallFn, store: ArtifactStore) -> None:
    artifact_id = "a" * 64
    body = (
        f"Calibration passed [A:{artifact_id}#tests[cal.binomial].status] and [K:k_abc]. "
        f"Unterminated [A:{artifact_id}#tests[cal.binomial].status and empty [K:] markers."
    )
    sid = store.put_model(
        DocumentSections(
            template_id=TEMPLATE,
            sections=[DocumentSection(section_id="executive_summary", body_md=body)],
        ),
        kind="document_sections",
    ).id
    doc = RenderOutput.model_validate(
        call(
            "doc-renderer",
            "render_document",
            RenderDocumentInput(template_id=TEMPLATE, sections_id=sid, title="t"),
        ).structured_content
    )
    ref = ArtifactRef.model_validate(
        call(
            "doc-renderer",
            "check_completeness",
            CheckCompletenessInput(document_id=doc.markdown.id, template_id=TEMPLATE),
        ).structured_content
    )
    report = store.get_model(ref.id, CompletenessReport)
    # The unterminated marker is not a marker at all, so it is neither resolved nor reported.
    assert report.unresolved_markers == ["[K:]"]


def test_raw_html_in_sections_and_title_is_escaped(call: CallFn, store: ArtifactStore) -> None:
    sections = DocumentSections(
        template_id=TEMPLATE,
        sections=[
            DocumentSection(
                section_id="executive_summary",
                body_md='Hello <script>alert(1)</script> <a href="#" onclick="x()">link</a>',
            )
        ],
    )
    sid = store.put_model(sections, kind="document_sections").id
    out = RenderOutput.model_validate(
        call(
            "doc-renderer",
            "render_document",
            RenderDocumentInput(
                template_id=TEMPLATE, sections_id=sid, title="<script>alert(2)</script>"
            ),
        ).structured_content
    )
    html = store.get(out.html.id).decode()
    assert "<script>" not in html and "<a href" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html


def _completeness(call: CallFn, store: ArtifactStore, body: str) -> CompletenessReport:
    sid = store.put_model(
        DocumentSections(
            template_id=TEMPLATE,
            sections=[DocumentSection(section_id="executive_summary", body_md=body)],
        ),
        kind="document_sections",
    ).id
    doc = RenderOutput.model_validate(
        call(
            "doc-renderer",
            "render_document",
            RenderDocumentInput(template_id=TEMPLATE, sections_id=sid, title="t"),
        ).structured_content
    )
    ref = ArtifactRef.model_validate(
        call(
            "doc-renderer",
            "check_completeness",
            CheckCompletenessInput(document_id=doc.markdown.id, template_id=TEMPLATE),
        ).structured_content
    )
    return store.get_model(ref.id, CompletenessReport)


def test_inline_code_is_not_checked(call: CallFn, store: ArtifactStore) -> None:
    body = (
        "- unresolved_markers: `[A:not-a-hash]`\n"
        "- uncited_regulatory_sentences: `SR 11-7 requires a review.`\n\n"
        "The body follows. Outside code, CECL requires lifetime losses. Bad marker [A:x]."
    )
    report = _completeness(call, store, body)
    assert report.unresolved_markers == ["[A:x]"]
    assert report.uncited_regulatory_sentences == ["Outside code, CECL requires lifetime losses."]


def test_regulation_names_match_whole_words(call: CallFn, store: ArtifactStore) -> None:
    body = "The Baseline score is stable. Basel requires capital."
    report = _completeness(call, store, body)
    assert report.uncited_regulatory_sentences == ["Basel requires capital."]
