"""Split documents into heading-scoped chunks with stable citation ids."""

import hashlib
import re

from modelforge_core.schemas import StrictModel

from modelforge_knowledge.corpus import Document

_HEADING = re.compile(r"^(#{1,3})\s+(.+)$")


class Chunk(StrictModel):
    citation_id: str
    doc_id: str
    section: str
    text: str
    position: int


def citation_id(doc_id: str, section: str, text: str) -> str:
    """Content-derived id: changes only when this chunk's own document, section or text change."""
    digest = hashlib.sha256(f"{doc_id}\x00{section}\x00{text}".encode()).hexdigest()
    return "k_" + digest[:12]


def _sections(doc: Document) -> list[tuple[str, list[str]]]:
    """(section path, words) in document order; the preamble is titled by the document."""
    sections: list[tuple[str, list[str]]] = []
    chain: list[str] = []
    current = doc.title
    words: list[str] = []
    for line in doc.text.splitlines():
        match = _HEADING.match(line)
        if match is None:
            words.extend(line.split())
            continue
        sections.append((current, words))
        level = len(match.group(1))
        chain = chain[: level - 1] + [match.group(2).strip()]
        current = " > ".join(chain)
        words = []
    sections.append((current, words))
    return sections


def _windows(words: list[str], window_words: int, overlap_words: int) -> list[list[str]]:
    if len(words) <= window_words + overlap_words:
        return [words]
    step = window_words - overlap_words
    starts = list(range(0, len(words) - window_words, step))
    starts.append(len(words) - window_words)
    return [words[s : s + window_words] for s in starts]


def chunk_document(
    doc: Document, *, window_words: int = 400, overlap_words: int = 40
) -> list[Chunk]:
    if window_words <= 0 or not 0 <= overlap_words < window_words:
        raise ValueError("need window_words > 0 and 0 <= overlap_words < window_words")
    chunks: list[Chunk] = []
    for section, words in _sections(doc):
        if not words:
            continue
        for window in _windows(words, window_words, overlap_words):
            text = " ".join(window)
            chunks.append(
                Chunk(
                    citation_id=citation_id(doc.id, section, text),
                    doc_id=doc.id,
                    section=section,
                    text=text,
                    position=len(chunks),
                )
            )
    return chunks
