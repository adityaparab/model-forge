"""Redaction of sensitive values before prompts leave the process.

Results name the pattern and the message role only; matched text is never returned.
"""

import re
from collections.abc import Sequence
from typing import Literal

from modelforge_core.schemas import StrictModel

from modelforge_gateway.schemas import Message

RedactionPattern = Literal["email", "iban", "digit_run", "restricted_term"]

_PATTERNS: tuple[tuple[RedactionPattern, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    # Not inside a hex id, a decimal fraction (``0.123456789012``) or a ``k_`` citation id.
    ("digit_run", re.compile(r"(?<![0-9A-Fa-f.])(?<!k_)\d{9,}(?![0-9A-Fa-f])")),
)


class RedactionHit(StrictModel):
    pattern: RedactionPattern
    role: str


def _restricted_pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    alternatives = [re.escape(term) for term in terms if term.strip()]
    if not alternatives:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)


def _scan(text: str, restricted: re.Pattern[str] | None) -> RedactionPattern | None:
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return name
    if restricted is not None and restricted.search(text):
        return "restricted_term"
    return None


def scan(text: str, restricted_terms: Sequence[str] = ()) -> RedactionPattern | None:
    """Name the first sensitive pattern found in ``text``, or ``None``."""
    return _scan(text, _restricted_pattern(restricted_terms))


def scan_messages(
    system: str, messages: Sequence[Message], restricted_terms: Sequence[str]
) -> RedactionHit | None:
    """Scan the system text, then each message in order; report the first hit."""
    restricted = _restricted_pattern(restricted_terms)
    texts = [("system", system), *((message.role, message.content) for message in messages)]
    for role, text in texts:
        pattern = _scan(text, restricted)
        if pattern is not None:
            return RedactionHit(pattern=pattern, role=role)
    return None
