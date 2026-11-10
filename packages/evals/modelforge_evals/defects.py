"""The planted-defect suite (spec 10.4): mutations, expected signatures, recall and precision.

Each case mutates one section of a completed run's rendered document. The markdown is rebuilt
by replacing that section's body in place, so every ``## n. Title`` heading stays intact.
Sentences are split per line with the grounding check's sentence-break rule; the renderer writes
each paragraph on one line, so a sentence is a verbatim substring of its line.
"""

import re
from collections.abc import Callable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

import yaml
from modelforge_core.schemas import StrictModel
from modelforge_knowledge.index import KnowledgeIndex
from modelforge_mcp.schemas import TemplateSections
from modelforge_mcp.servers.doc_renderer import MARKER
from modelforge_orchestrator.grounding import (
    DATE,
    NUMBER,
    REGULATION_NAME,
    SENTENCE_BREAK,
    UNIT_SUFFIXED,
    YEAR_IN_CONTEXT,
)
from modelforge_orchestrator.schemas import Finding
from modelforge_orchestrator.sections import SECTION_HEADING
from pydantic import Field, TypeAdapter, model_validator

__all__ = [
    "DefectCase",
    "Mutation",
    "MutationContext",
    "Signature",
    "apply_mutation",
    "defect_metrics",
    "load_defects",
    "matches",
]

MutationKind = Literal[
    "remove_section",
    "swap_digit",
    "append_sentence",
    "swap_citation",
    "remove_test_sentences",
    "remove_sentences_containing",
]


class Mutation(StrictModel):
    kind: MutationKind
    section: str
    """The template section id the mutation edits."""
    text: str | None = None
    """``append_sentence``: the sentence; ``remove_sentences_containing``: the needle."""
    doc_id: str | None = None
    """``swap_citation``: the knowledge document whose first chunk replaces the citation."""
    statuses: list[str] = Field(default_factory=lambda: ["fail", "warn"])
    """``remove_test_sentences``: the validation test statuses whose sentences are removed."""

    @model_validator(mode="after")
    def _requires_its_field(self) -> "Mutation":
        if self.kind in ("append_sentence", "remove_sentences_containing") and not self.text:
            raise ValueError(f"a {self.kind} mutation requires text")
        if self.kind == "swap_citation" and not self.doc_id:
            raise ValueError("a swap_citation mutation requires doc_id")
        return self


class Signature(StrictModel):
    """The finding a case expects: by ``check_key`` glob, else by category and evidenced section."""

    check_key: str | None = None
    category: str | None = None
    section: str | None = None


class DefectCase(StrictModel):
    id: str
    mutation: Mutation
    expect: Signature


class MutationContext(StrictModel):
    validation: dict[str, Any]
    """The base run's validation report JSON."""
    index: KnowledgeIndex


def load_defects(path: Path) -> list[DefectCase]:
    return TypeAdapter(list[DefectCase]).validate_python(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


_NEXT_HEADING = re.compile(r"^## ", re.MULTILINE)
_LINE_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_CODE_SPAN = re.compile(r"`[^`]*`")
_TERMINATOR = re.compile(r"[.!?](?=\s|$)")
_CITATION = re.compile(r"\[K:([^\[\]\s]+)\]")


def _section_span(
    markdown: str, template: TemplateSections, section: str
) -> tuple[int, int] | None:
    """The ``(heading start, body end)`` of ``section``'s block; `None` when it has none."""
    title = next((s.title for s in template.sections if s.id == section), None)
    for match in SECTION_HEADING.finditer(markdown):
        if match.group(2) == title:
            following = _NEXT_HEADING.search(markdown, match.end())
            return match.start(), following.start() if following else len(markdown)
    return None


def _replace_body(
    markdown: str, template: TemplateSections, section: str, edit: Callable[[str], str | None]
) -> str | None:
    """``markdown`` with ``section``'s stripped body replaced by ``edit(body)``.

    `None` when the section is absent, or ``edit`` returns `None` or leaves the body unchanged.
    The whitespace around the body is kept, so the heading and the next block stay in place.
    """
    span = _section_span(markdown, template, section)
    if span is None:
        return None
    start, end = span
    heading_end = markdown.index("\n", start) + 1 if "\n" in markdown[start:end] else end
    raw = markdown[heading_end:end]
    body = raw.strip()
    edited = edit(body)
    if edited is None or edited == body:
        return None
    lead = raw[: len(raw) - len(raw.lstrip())]
    trail = raw[len(raw.rstrip()) :]
    return markdown[:heading_end] + lead + edited + trail + markdown[end:]


def _edit_sentences(body: str, edit: Callable[[str], str | None]) -> str:
    """``body`` with each sentence replaced by ``edit(sentence)``; `None` drops the sentence.

    A line left with no sentence is dropped with it.
    """
    lines: list[str] = []
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            lines.append(line)
            continue
        prefix = _LINE_PREFIX.match(line)
        head = prefix.group(0) if prefix else ""
        kept = [s for s in (edit(p) for p in SENTENCE_BREAK.split(line[len(head) :])) if s]
        if kept:
            lines.append(head + " ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _blank(pattern: re.Pattern[str], text: str, group: int | str = 0) -> str:
    def blank(match: re.Match[str]) -> str:
        start, end = match.span(group)
        whole = match.group(0)
        offset = match.start()
        return whole[: start - offset] + " " * (end - start) + whole[end - offset :]

    return pattern.sub(blank, text)


def _swap_last_digit(sentence: str) -> str | None:
    """``sentence`` with the last digit of its first claimed number changed (``9 -> 0``).

    Markers, code spans, regulation names, dates, dated years and unit-suffixed numbers are
    blanked first (length-preserving), as `grounding.numbers_in` ignores them.
    """
    masked = sentence
    for pattern in (MARKER, _CODE_SPAN, REGULATION_NAME, DATE, UNIT_SUFFIXED, _TERMINATOR):
        masked = _blank(pattern, masked)
    masked = _blank(YEAR_IN_CONTEXT, masked, "year")
    match = NUMBER.search(masked)
    if match is None:
        return None
    at = (match.end(3) if match.group(3) else match.end(2)) - 1
    digit = int(sentence[at])
    return sentence[:at] + str(0 if digit == 9 else digit + 1) + sentence[at + 1 :]


def _swap_digit(body: str) -> str | None:
    """``body`` with a digit swapped in its first ``[A:``-marked sentence that claims a number.

    `None` (via an unchanged body) when no marked sentence carries a number token.
    """
    done = False

    def edit(sentence: str) -> str | None:
        nonlocal done
        if done or "[A:" not in sentence:
            return sentence
        swapped = _swap_last_digit(sentence)
        if swapped is None:
            return sentence
        done = True
        return swapped

    return _edit_sentences(body, edit)


def _swap_citation(body: str, mutation: Mutation, ctx: MutationContext) -> str | None:
    replacement = next(
        (c.citation_id for c in ctx.index.chunks if c.doc_id == mutation.doc_id), None
    )
    match = _CITATION.search(body)
    if replacement is None or match is None:
        return None
    return body[: match.start()] + f"[K:{replacement}]" + body[match.end() :]


def _failing_test_needles(mutation: Mutation, ctx: MutationContext) -> list[str]:
    tests: list[dict[str, Any]] = ctx.validation.get("tests") or []
    return [f"tests[{t['test_id']}]" for t in tests if t.get("status") in mutation.statuses]


def _without(body: str, drop: Callable[[str], bool]) -> str:
    return _edit_sentences(body, lambda s: None if drop(s) else s)


def _remove_section(markdown: str, template: TemplateSections, section: str) -> str | None:
    span = _section_span(markdown, template, section)
    return None if span is None else markdown[: span[0]] + markdown[span[1] :]


def apply_mutation(
    markdown: str, template: TemplateSections, mutation: Mutation, ctx: MutationContext
) -> str | None:
    """``markdown`` with ``mutation`` applied; `None` when the mutation changes nothing.

    A mutation of a section the document does not carry changes nothing.
    """
    if mutation.kind == "remove_section":
        return _remove_section(markdown, template, mutation.section)

    def edit(body: str) -> str | None:
        if mutation.kind == "swap_digit":
            return _swap_digit(body)
        if mutation.kind == "append_sentence":
            return f"{body} {mutation.text}".strip()
        if mutation.kind == "swap_citation":
            return _swap_citation(body, mutation, ctx)
        if mutation.kind == "remove_test_sentences":
            needles = _failing_test_needles(mutation, ctx)
            if not needles:
                return None
            return _without(
                body,
                lambda s: any(
                    m.group(1) == "A" and any(n in m.group(2) for n in needles)
                    for m in MARKER.finditer(s)
                ),
            )
        needle = (mutation.text or "").lower()
        return _without(body, lambda s: needle in s.lower())

    return _replace_body(markdown, template, mutation.section, edit)


def matches(finding: Finding, expect: Signature) -> bool:
    """Whether ``finding`` has the signature ``expect``.

    With a ``check_key`` glob the finding's own key decides; otherwise the categories must be
    equal and the expected section id must be among the finding's evidence.
    """
    if expect.check_key is not None:
        return fnmatchcase(finding.check_key or "", expect.check_key)
    return expect.category == finding.category and expect.section in finding.evidence


def _key(finding: Finding) -> str | tuple[str, str]:
    return finding.check_key if finding.check_key is not None else (finding.category, finding.title)


def defect_metrics(
    cases: list[DefectCase],
    outcomes: dict[str, tuple[bool | None, list[Finding]]],
    baseline: list[Finding],
) -> tuple[dict[str, float | None], float | None, float | None]:
    """Recall per case (`None` when inapplicable), mean recall over applicable cases, precision.

    ``outcomes`` maps a case id to ``(applicable, findings)``. On applicable probes, a finding
    matching neither its case's signature nor a ``baseline`` finding (keyed by ``check_key``,
    else by category and title) is a false positive; precision is the matched findings over
    matched plus false positives, `None` when there is neither.
    """
    known = {_key(f) for f in baseline}
    recall: dict[str, float | None] = {}
    matched = false_positives = 0
    for case in cases:
        applicable, findings = outcomes[case.id]
        if not applicable:
            recall[case.id] = None
            continue
        hits = [f for f in findings if matches(f, case.expect)]
        recall[case.id] = 1.0 if hits else 0.0
        matched += len(hits)
        false_positives += sum(
            not matches(f, case.expect) and _key(f) not in known for f in findings
        )
    scored = [r for r in recall.values() if r is not None]
    mean = sum(scored) / len(scored) if scored else None
    total = matched + false_positives
    return recall, mean, matched / total if total else None
