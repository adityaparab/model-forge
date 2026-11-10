"""BM25 retrieval over a KnowledgeIndex."""

import math
import re
from collections import Counter
from collections.abc import Sequence

from modelforge_core.schemas import StrictModel

from modelforge_knowledge.chunking import Chunk
from modelforge_knowledge.index import DocumentMeta, KnowledgeIndex

K1 = 1.5
B = 0.75

STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "about", "after", "all", "also", "an", "and", "any", "are", "as",
        "at", "be", "been", "but", "by", "can", "could", "do", "does", "for",
        "from", "had", "has", "have", "how", "if", "in", "into", "is", "it",
        "its", "may", "more", "must", "no", "not", "of", "on", "or", "other",
        "should", "so", "such", "than", "that", "the", "their", "them", "then",
        "there", "these", "they", "this", "those", "to", "was", "were", "what",
        "when", "which", "who", "will", "with", "would",
    }
)  # fmt: skip

_TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


class Hit(StrictModel):
    citation_id: str
    doc_id: str
    section: str
    text: str
    score: float


class Retriever:
    def __init__(self, index: KnowledgeIndex) -> None:
        self._index = index
        self._chunks: list[Chunk] = list(index.chunks)
        self._by_id: dict[str, Chunk] = {c.citation_id: c for c in self._chunks}
        self._doc_ids: frozenset[str] = frozenset(d.id for d in index.documents)
        # The section path is indexed with the body: headings such as "Four-eyes rule" often
        # carry the only occurrence of the term a reader asks about.
        self._tf: list[Counter[str]] = [
            Counter(tokenize(f"{c.section}\n{c.text}")) for c in self._chunks
        ]
        self._lengths: list[int] = [sum(tf.values()) for tf in self._tf]
        self._avg_len: float = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self._df: dict[str, int] = dict(df)

    @property
    def documents(self) -> list[DocumentMeta]:
        return self._index.documents

    def get(self, citation_id: str) -> Chunk | None:
        return self._by_id.get(citation_id)

    def _idf(self, term: str) -> float:
        n = self._df.get(term, 0)
        total = len(self._chunks)
        return math.log(1 + (total - n + 0.5) / (n + 0.5))

    def search(self, query: str, *, k: int = 5, sources: Sequence[str] | None = None) -> list[Hit]:
        allowed: set[str] | None = None
        if sources is not None:
            unknown = [s for s in sources if s not in self._doc_ids]
            if unknown:
                raise KeyError(f"unknown source(s): {', '.join(unknown)}")
            allowed = set(sources)
        terms = sorted(set(tokenize(query)))
        idf = {term: self._idf(term) for term in terms}
        scored: list[tuple[float, str, int]] = []
        for i, chunk in enumerate(self._chunks):
            if allowed is not None and chunk.doc_id not in allowed:
                continue
            tf = self._tf[i]
            norm = K1 * (1 - B + B * self._lengths[i] / self._avg_len) if self._avg_len else K1
            score = 0.0
            for term in terms:
                f = tf.get(term, 0)
                if f:
                    score += idf[term] * f * (K1 + 1) / (f + norm)
            if score > 0:
                scored.append((score, chunk.citation_id, i))
        scored.sort(key=lambda s: (-s[0], s[1]))
        hits: list[Hit] = []
        for score, _, i in scored[: max(k, 0)]:
            chunk = self._chunks[i]
            hits.append(
                Hit(
                    citation_id=chunk.citation_id,
                    doc_id=chunk.doc_id,
                    section=chunk.section,
                    text=chunk.text,
                    score=score,
                )
            )
        return hits
