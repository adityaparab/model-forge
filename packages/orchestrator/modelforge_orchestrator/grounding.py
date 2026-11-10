"""Grounding checks for a documentation section (spec 6.5).

Every number in a section must match a value its sentence cites with an ``[A:<id>#<path>]``
marker; every regulatory statement must cite a retrieved knowledge passage with ``[K:<id>]``.
"""

import math
import re
from collections.abc import Callable, Collection, Mapping
from typing import Any, cast

from modelforge_core.schemas import StrictModel
from modelforge_lineage.citations import PathError, resolve_path
from modelforge_mcp.schemas import GroundingReport
from modelforge_mcp.servers.doc_renderer import MARKER, REGULATIONS

NUMBER = re.compile(r"(?<![\w.])(-?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s*(?:%|percent))?(?![\w.])")
REGULATION_NAME = re.compile(
    r"\b(?:SR\s+\d+-\d+|OCC\s+(?:Bulletin\s+)?\d{4}-\d+|ASC\s+\d+|IFRS\s+\d+|Basel\s+(?:I{1,3}|\d))\b"
)
DATE = re.compile(
    r"\b(?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+\d{4}\b|\b(?:19|20)\d{2}\b"
)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")

_ARTIFACT_ID = re.compile(r"^[0-9a-f]{64}$")
_CODE_SPAN = re.compile(r"`[^`]*`")
_TERMINATOR = re.compile(r"[.!?](?=\s|$)")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_LINE_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)]|#{1,6})\s+")


class NumberToken(StrictModel):
    """A number as written in prose; ``is_integer`` only without a decimal part or percent."""

    text: str
    value: float
    percent: bool
    sig_figs: int
    is_integer: bool


def expand_aliases(body: str, aliases: Mapping[str, str]) -> tuple[str, list[str]]:
    """``body`` with ``[A:<alias>#<path>]`` rewritten to ``[A:<artifact_id>#<path>]``.

    Also returns the ``A`` markers whose alias is unknown; they are left untouched, as are
    markers that already carry an artifact id.
    """
    unknown: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        kind, target = match.group(1), match.group(2)
        if kind != "A":
            return match.group(0)
        alias, sep, path = target.partition("#")
        if alias in aliases:
            return f"[A:{aliases[alias]}{sep}{path}]"
        if not _ARTIFACT_ID.match(alias):
            unknown.append(match.group(0))
        return match.group(0)

    return MARKER.sub(rewrite, body), unknown


def strip_non_claims(sentence: str) -> str:
    """``sentence`` without markers, inline code, regulation names and dates."""
    for pattern in (MARKER, _CODE_SPAN, REGULATION_NAME, DATE):
        sentence = pattern.sub(" ", sentence)
    return sentence


def numbers_in(text: str) -> list[NumberToken]:
    """The numbers written in ``text``, in order; a sentence-ending stop does not hide one."""
    tokens: list[NumberToken] = []
    for match in NUMBER.finditer(_TERMINATOR.sub(" ", text)):
        sign, whole, fraction, percent = match.groups()
        integer_digits = whole.replace(",", "")
        fraction_digits = fraction[1:] if fraction else ""
        mantissa = (integer_digits + fraction_digits).lstrip("0")
        tokens.append(
            NumberToken(
                text=match.group(0),
                value=float(f"{sign}{integer_digits}{fraction or ''}"),
                percent=percent is not None,
                sig_figs=max(len(mantissa), 1),
                is_integer=fraction is None and percent is None,
            )
        )
    return tokens


def round_sig(x: float, p: int) -> float:
    """``x`` rounded to ``p`` significant figures."""
    if x == 0 or not math.isfinite(x):
        return x
    return float(round(x, p - 1 - math.floor(math.log10(abs(x)))))


def _digits(token: NumberToken) -> str:
    return re.sub(r"[^\d.]", "", token.text)


def _matches_scalar(token: NumberToken, value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return False
        v = number * 100 if token.percent else number
        if token.is_integer:
            return v.is_integer() and v == token.value
        p = min(token.sig_figs, 4)
        return math.isclose(round_sig(token.value, p), round_sig(v, p), rel_tol=1e-9)
    if isinstance(value, str):
        return re.search(rf"(?<!\d){re.escape(_digits(token))}(?!\d)", value) is not None
    return False


def matches(token: NumberToken, value: Any) -> bool:
    """Whether ``token`` states ``value`` (spec 6.5).

    Integer tokens compare exactly, others at ``min(sig_figs, 4)`` significant figures, percent
    tokens against ``value * 100``. A string matches when the token's digits appear in it as a
    whole number; a list when any scalar element does. Booleans, None and dicts never match.
    """
    if isinstance(value, list):
        return any(
            _matches_scalar(token, element)
            for element in cast("list[Any]", value)
            if not isinstance(element, (dict, list))
        )
    return _matches_scalar(token, value)


def _sentences(body: str) -> list[str]:
    """The body's sentences: blank lines and list items or headings start a new paragraph."""
    paragraphs: list[str] = []
    for block in _PARAGRAPH_BREAK.split(body):
        current: list[str] = []
        for line in block.splitlines():
            if _LINE_PREFIX.match(line):
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                paragraphs.append(_LINE_PREFIX.sub("", line, count=1))
            elif line.strip():
                current.append(line.strip())
        if current:
            paragraphs.append(" ".join(current))
    return [s for p in paragraphs for s in SENTENCE_BREAK.split(p.strip()) if s]


def check_section(
    body: str, *, load_artifact: Callable[[str], Any], retrieved: Collection[str]
) -> GroundingReport:
    """Check ``body`` (aliases already expanded) against its cited artifacts and passages."""
    loaded: dict[str, Any] = {}
    failed: set[str] = set()

    def load(artifact_id: str) -> tuple[bool, Any]:
        if artifact_id in failed:
            return False, None
        if artifact_id not in loaded:
            try:
                loaded[artifact_id] = load_artifact(artifact_id)
            except Exception:
                failed.add(artifact_id)
                return False, None
        return True, loaded[artifact_id]

    ungrounded: list[str] = []
    unresolved: list[str] = []
    uncited: list[str] = []
    foreign: list[str] = []
    for sentence in _sentences(body):
        values: list[Any] = []
        for match in MARKER.finditer(sentence):
            kind, target = match.group(1), match.group(2)
            if kind == "K":
                if not target:
                    unresolved.append(match.group(0))
                elif target not in retrieved:
                    foreign.append(match.group(0))
                continue
            artifact_id, _, path = target.partition("#")
            ok, artifact = (False, None)
            if _ARTIFACT_ID.match(artifact_id) and path:
                ok, artifact = load(artifact_id)
            if ok:
                try:
                    values.append(resolve_path(artifact, path))
                except PathError:
                    ok = False
            if not ok:
                unresolved.append(match.group(0))
        for token in numbers_in(strip_non_claims(sentence)):
            if not any(matches(token, value) for value in values):
                ungrounded.append(token.text)
        if any(name in sentence for name in REGULATIONS) and "[K:" not in sentence:
            uncited.append(sentence)
    return GroundingReport(
        ungrounded_numbers=ungrounded,
        unresolved_markers=unresolved,
        uncited_regulatory_sentences=uncited,
        foreign_passages=foreign,
        ok=not (ungrounded or unresolved or uncited or foreign),
    )
