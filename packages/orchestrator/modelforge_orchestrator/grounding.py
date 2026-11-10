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
from modelforge_mcp.servers.doc_renderer import MARKER, REGULATION_WORDS

NUMBER = re.compile(
    r"(?<![\w.])(-?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?([eE][-+]?\d+)?(\s*(?:%|percent))?(?![\w.])"
)
"""A decimal number with optional thousands separators, exponent and percent."""
REGULATION_NAME = re.compile(
    r"\b(?:SR\s+\d+-\d+|OCC\s+(?:Bulletin\s+)?\d{4}-\d+|ASC\s+\d+|IFRS\s+\d+|Basel\s+(?:I{1,3}|\d))\b"
)
_MONTH = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
)
DATE = re.compile(
    rf"\b(?:\d{{1,2}}\s+{_MONTH}(?:,?\s+\d{{4}})?"
    rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
    rf"|{_MONTH},?\s+\d{{4}})\b"
)
"""A date with a month name and a day, a year or both (``April 17, 2026``, ``17 April 2026``,
``April 2011``)."""
YEAR_IN_CONTEXT = re.compile(
    rf"(?P<cue>(?:\b(?:(?i:in|since|of|by|from|until|before|after|during|year)|FY|{_MONTH})\b"
    r"|\()[^\w.!?]*)(?P<year>(?:19|20)\d{2})\b(?!\d|\.\d)"
)
"""A standalone year after a month name, a cue word or an opening parenthesis (``in 2011``,
``(2026)``, ``FY 2024``), with only spaces and punctuation between; elsewhere (``2045
defaults``) a year-like integer is a claim. A run inside a decimal (``0.2015``) is not a year."""
_UNICODE_MINUS = re.compile(r"\u2212(?=\d)")
_SPACED_THOUSANDS = re.compile(r"(?<=\d)[\u00a0\u2009\u202f](?=\d{3}(?!\d))")
UNIT_SUFFIXED = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![eE][-+]?\d)[A-Za-z]+(?![\w])")
"""A number with a letter suffix (``50k``, ``3x``, ``9pp``): a claim that never grounds.

An exponent (``1e-5``, ``4.262e-84``) is not a suffix; it falls through to ``NUMBER``."""
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")

_ARTIFACT_ID = re.compile(r"^[0-9a-f]{64}$")
_CODE_SPAN = re.compile(r"`[^`]*`")
_TERMINATOR = re.compile(r"[.!?](?=\s|$)")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_LINE_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)]|#{1,6})\s+")


class NumberToken(StrictModel):
    """A number as written in prose; ``is_integer`` only without a decimal part, exponent or
    percent. ``sig_figs`` counts the mantissa's digits (``4.262e-84`` has 4)."""

    text: str
    value: float
    percent: bool
    sig_figs: int
    is_integer: bool
    suffixed: bool = False


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
    """``sentence`` without markers, inline code, regulation names, dates and dated years."""
    for pattern in (MARKER, _CODE_SPAN, REGULATION_NAME, DATE):
        sentence = pattern.sub(" ", sentence)
    return YEAR_IN_CONTEXT.sub(lambda m: m.group("cue") + " ", sentence)


def _normalise(text: str) -> str:
    """``text`` with a Unicode minus as ``-`` and thin or no-break space thousands removed."""
    return _SPACED_THOUSANDS.sub("", _UNICODE_MINUS.sub("-", text))


def _sig_figs(digits: str) -> int:
    return max(len(digits.replace(".", "").lstrip("0")), 1)


def numbers_in(text: str) -> list[NumberToken]:
    """The numbers written in ``text``, in order; a sentence-ending stop does not hide one.

    A number with a letter suffix is returned as a ``suffixed`` token, which never grounds.
    """
    text = _TERMINATOR.sub(" ", _normalise(text))
    found: list[tuple[int, NumberToken]] = []
    for match in UNIT_SUFFIXED.finditer(text):
        number = re.match(r"-?[\d,]*(?:\.\d+)?", match.group(0))
        digits = number.group(0).replace(",", "") if number else ""
        found.append(
            (
                match.start(),
                NumberToken(
                    text=match.group(0),
                    value=float(digits) if digits.strip("-") else 0.0,
                    percent=False,
                    sig_figs=_sig_figs(digits.lstrip("-")),
                    is_integer=False,
                    suffixed=True,
                ),
            )
        )
    plain = UNIT_SUFFIXED.sub(lambda m: " " * len(m.group(0)), text)
    for match in NUMBER.finditer(plain):
        sign, whole, fraction, exponent, percent = match.groups()
        integer_digits = whole.replace(",", "")
        found.append(
            (
                match.start(),
                NumberToken(
                    text=match.group(0),
                    value=float(f"{sign}{integer_digits}{fraction or ''}{exponent or ''}"),
                    percent=percent is not None,
                    sig_figs=_sig_figs(integer_digits + (fraction or "")),
                    is_integer=fraction is None and exponent is None and percent is None,
                ),
            )
        )
    return [token for _, token in sorted(found, key=lambda item: item[0])]


def round_sig(x: float, p: int) -> float:
    """``x`` rounded to ``p`` significant figures."""
    if x == 0 or not math.isfinite(x):
        return x
    return float(round(x, p - 1 - math.floor(math.log10(abs(x)))))


def _matches_scalar(token: NumberToken, value: Any) -> bool:
    if token.suffixed or value is None or isinstance(value, bool):
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
        return any(
            _matches_scalar(token, number.value)
            for number in numbers_in(value)
            if not number.suffixed
        )
    return False


def matches(token: NumberToken, value: Any) -> bool:
    """Whether ``token`` states ``value`` (spec 6.5).

    Integer tokens compare exactly, others at ``min(sig_figs, 4)`` significant figures, percent
    tokens against ``value * 100``. A string matches when one of its own numbers does, so an
    identifier (``f_12``) or a digest grounds nothing; a list matches when any scalar element
    does. Suffixed tokens, booleans, None and dicts never match.
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
        if REGULATION_WORDS.search(sentence) is not None and "[K:" not in sentence:
            uncited.append(sentence)
    return GroundingReport(
        ungrounded_numbers=ungrounded,
        unresolved_markers=unresolved,
        uncited_regulatory_sentences=uncited,
        foreign_passages=foreign,
        ok=not (ungrounded or unresolved or uncited or foreign),
    )
