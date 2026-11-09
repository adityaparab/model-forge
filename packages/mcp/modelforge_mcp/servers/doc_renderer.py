"""doc-renderer server: template sections, Markdown and HTML rendering, completeness checks."""

import html
import re

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markdown_it import MarkdownIt
from mcp.server.mcpserver import MCPServer
from modelforge_core.schemas import StrictModel
from modelforge_lineage.models import ArtifactRef
from modelforge_lineage.store import MEDIA_HTML, MEDIA_MARKDOWN
from pydantic import ValidationError

from modelforge_mcp.common import (
    ServerName,
    ServerSettings,
    ToolContext,
    ToolSpec,
    fail,
    load_model,
    make_context,
    make_server,
    producer,
    register_tool,
)
from modelforge_mcp.schemas import CompletenessReport, DocumentSections, TemplateSections

SERVER_NAME: ServerName = "doc-renderer"
SERVER_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"
INSTRUCTIONS = (
    "Renders model documents: lists a template's sections, renders stored document sections "
    "into Markdown and HTML artifacts, and checks a rendered document for missing sections, "
    "unresolved citation markers and uncited regulatory statements. Every tool takes and "
    "returns artifact ids; no document travels inline."
)

MARKER = re.compile(r"\[(A|K):([^\]\s]+)\]")
REGULATIONS = ("SR 11-7", "SR 26-2", "OCC Bulletin 2026-13", "CECL", "IFRS 9", "Basel")
BANNER = "> **REQUIRES AUTHOR ATTENTION**: this section was not supplied."

_TEMPLATE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ARTIFACT_ID = re.compile(r"^[0-9a-f]{64}$")
_SECTION_HEADING = re.compile(r"^## \d+\. ", re.MULTILINE)
_HEADING_LINE = re.compile(r"^#{1,6} ")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_DOCUMENT_FILE = "document.md.j2"
_STYLE = (
    "body{font-family:Georgia,'Times New Roman',serif;line-height:1.5;max-width:52rem;"
    "margin:2rem auto;padding:0 1rem;color:#1a1a1a}"
    "h1,h2,h3{font-family:Helvetica,Arial,sans-serif}"
    "table{border-collapse:collapse;margin:1rem 0}"
    "th,td{border:1px solid #888;padding:.3rem .6rem;text-align:left}"
    "code{font-family:Menlo,Consolas,monospace;font-size:.9em}"
    "blockquote{margin:1rem 0;padding:.5rem 1rem;border-left:4px solid #c0392b;"
    "background:#fdecea}"
)


class ListTemplateSectionsInput(StrictModel):
    template_id: str


class RenderDocumentInput(StrictModel):
    template_id: str
    sections_id: str
    title: str
    context: dict[str, str] = {}


class RenderOutput(StrictModel):
    markdown: ArtifactRef
    html: ArtifactRef


class CheckCompletenessInput(StrictModel):
    document_id: str
    template_id: str


def load_template(ctx: ToolContext, template_id: str) -> TemplateSections:
    if not _TEMPLATE_ID.match(template_id):
        fail("invalid_input", f"invalid template id {template_id!r}")
    path = ctx.settings.templates_dir / template_id / "sections.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail("invalid_input", f"unknown template {template_id!r}")
    try:
        return TemplateSections.model_validate({"template_id": template_id, "sections": raw})
    except ValidationError as exc:
        fail("invalid_input", f"malformed sections.yaml for template {template_id!r}: {exc}")


def _environment(ctx: ToolContext, template_id: str) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(ctx.settings.templates_dir / template_id)),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _to_html(title: str, markdown: str) -> str:
    body = MarkdownIt("commonmark").render(markdown)
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body>{body}</body></html>"
    )


def list_template_sections(ctx: ToolContext, req: ListTemplateSectionsInput) -> TemplateSections:
    return load_template(ctx, req.template_id)


def render_document(ctx: ToolContext, req: RenderDocumentInput) -> RenderOutput:
    template = load_template(ctx, req.template_id)
    supplied = load_model(ctx, req.sections_id, DocumentSections)
    bodies = {s.section_id: s.body_md for s in supplied.sections}
    sections = [
        {"index": n, "title": spec.title, "body": bodies.get(spec.id)}
        for n, spec in enumerate(template.sections, start=1)
    ]
    markdown = (
        _environment(ctx, req.template_id)
        .get_template(_DOCUMENT_FILE)
        .render(title=req.title, context=req.context, sections=sections, banner=BANNER)
    )
    rendered = sum(1 for s in sections if s["body"] is not None)
    summary = {"sections_rendered": rendered, "sections_missing": len(sections) - rendered}
    md_ref = ctx.store.put(
        markdown.encode(),
        kind="document_md",
        media_type=MEDIA_MARKDOWN,
        producer=producer(ctx, "render_document", TOOL_VERSION),
        summary=summary,
    )
    html_ref = ctx.store.put(
        _to_html(req.title, markdown).encode(),
        kind="document_html",
        media_type=MEDIA_HTML,
        producer=producer(ctx, "render_document", TOOL_VERSION),
        summary=summary,
    )
    return RenderOutput(markdown=md_ref, html=html_ref)


def _section_contents(markdown: str) -> dict[str, str]:
    """Map each `## n. Title` heading's title to its content up to the next such heading."""
    starts = list(_SECTION_HEADING.finditer(markdown))
    contents: dict[str, str] = {}
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(markdown)
        heading, _, content = markdown[match.end() : end].partition("\n")
        contents[heading.strip()] = content
    return contents


def _unresolved(markdown: str) -> list[str]:
    unresolved: list[str] = []
    for match in MARKER.finditer(markdown):
        kind, target = match.group(1), match.group(2)
        if kind == "A":
            ok = _ARTIFACT_ID.match(target.partition("#")[0]) is not None
        else:
            ok = bool(target)
        if not ok:
            unresolved.append(match.group(0))
    return unresolved


def _uncited(markdown: str) -> list[str]:
    lines = [
        line
        for line in markdown.splitlines()
        if not _HEADING_LINE.match(line) and line.strip() != BANNER
    ]
    sentences = _SENTENCE_BREAK.split("\n".join(lines).strip())
    return [
        sentence
        for sentence in sentences
        if any(name in sentence for name in REGULATIONS) and "[K:" not in sentence
    ]


def check_completeness(ctx: ToolContext, req: CheckCompletenessInput) -> ArtifactRef:
    template = load_template(ctx, req.template_id)
    markdown = ctx.store.get(req.document_id).decode("utf-8")
    contents = _section_contents(markdown)
    missing = [
        spec.id
        for spec in template.sections
        if spec.required and (spec.title not in contents or contents[spec.title].strip() == BANNER)
    ]
    unresolved = _unresolved(markdown)
    uncited = _uncited(markdown)
    complete = not missing and not unresolved and not uncited
    report = CompletenessReport(
        template_id=req.template_id,
        document_id=req.document_id,
        missing_sections=missing,
        unresolved_markers=unresolved,
        uncited_regulatory_sentences=uncited,
        complete=complete,
    )
    return ctx.store.put_model(
        report,
        kind="completeness",
        producer=producer(ctx, "check_completeness", TOOL_VERSION),
        summary={
            "complete": complete,
            "missing_sections": missing,
            "n_unresolved_markers": len(unresolved),
            "n_uncited": len(uncited),
        },
    )


_TOOLS = [
    (
        "list_template_sections",
        "List a document template's sections in order, with whether each is required, the "
        "artifact kinds it draws on and authoring guidance.",
        list_template_sections,
        ListTemplateSectionsInput,
        TemplateSections,
    ),
    (
        "render_document",
        "Render stored document sections through a template into Markdown and HTML artifacts; "
        "sections not supplied carry an author-attention banner.",
        render_document,
        RenderDocumentInput,
        RenderOutput,
    ),
    (
        "check_completeness",
        "Check a rendered Markdown document for missing required sections, malformed citation "
        "markers and regulatory sentences without a knowledge citation.",
        check_completeness,
        CheckCompletenessInput,
        ArtifactRef,
    ),
]

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        server=SERVER_NAME,
        tool=name,
        version=TOOL_VERSION,
        input_model=input_model,
        output_model=output_model,
    )
    for name, _, _, input_model, output_model in _TOOLS
]


def build(settings: ServerSettings) -> MCPServer:
    server = make_server(SERVER_NAME, SERVER_VERSION, INSTRUCTIONS)
    ctx = make_context(SERVER_NAME, settings)
    for name, description, fn, input_model, output_model in _TOOLS:
        register_tool(
            server,
            ctx,
            name=name,
            version=TOOL_VERSION,
            description=description,
            fn=fn,
            input_model=input_model,
            output_model=output_model,
        )
    return server
