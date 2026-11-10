"""Section helpers shared by the agents that read a rendered document back.

The doc-renderer heads each template section with ``## <index>. <title>``. `split_sections`
reverses that by title, ignoring the index, so a document whose numbering differs from the
template's still splits; `section_sentences` is the grounding check's sentence splitter.
"""

import re

from modelforge_mcp.schemas import TemplateSections

from modelforge_orchestrator.grounding import sentences

__all__ = ["SECTION_HEADING", "section_sentences", "split_sections"]

SECTION_HEADING = re.compile(r"^## (\d+)\. (.+?)\s*$", re.MULTILINE)


def split_sections(markdown: str, template: TemplateSections) -> dict[str, str]:
    """Each template section id whose title heads a ``## n. Title`` block, to that block's body.

    The body runs from the line after the heading to the next such heading (or the end) and is
    stripped; a banner the renderer put in place of a missing body is kept. Sections whose title
    heads no block are absent, and blocks whose title matches no template section are ignored.
    """
    id_of = {spec.title: spec.id for spec in template.sections}
    headings = list(SECTION_HEADING.finditer(markdown))
    bodies: dict[str, str] = {}
    for i, match in enumerate(headings):
        section_id = id_of.get(match.group(2))
        if section_id is None:
            continue
        end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
        bodies[section_id] = markdown[match.end() : end].strip()
    return bodies


def section_sentences(body: str) -> list[str]:
    """The body's sentences, split exactly as the grounding check splits them."""
    return sentences(body)
