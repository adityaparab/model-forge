"""BM25 retrieval over the knowledge index."""

from pathlib import Path

import pytest
import yaml
from modelforge_knowledge.chunking import Chunk, citation_id
from modelforge_knowledge.corpus import load_corpus
from modelforge_knowledge.index import DocumentMeta, KnowledgeIndex, build_index
from modelforge_knowledge.retrieval import STOPWORDS, Retriever, tokenize

GOLDEN = Path(__file__).resolve().parent / "golden_questions.yaml"


def _chunk(doc_id: str, section: str, text: str, position: int) -> Chunk:
    return Chunk(
        citation_id=citation_id(doc_id, section, text),
        doc_id=doc_id,
        section=section,
        text=text,
        position=position,
    )


def _meta(doc_id: str) -> DocumentMeta:
    return DocumentMeta(
        id=doc_id,
        title=doc_id,
        source="s",
        authority="a",
        date="2026-01-01",
        url="u",
        kind="summary",
    )


def _toy_index() -> KnowledgeIndex:
    chunks = [
        _chunk("alpha", "One", "Validation requires effective challenge by qualified staff.", 0),
        _chunk("alpha", "Two", "Challenge the assumptions of the model.", 1),
        _chunk("beta", "Three", "Effective governance needs a model inventory.", 0),
    ]
    return KnowledgeIndex(
        built_from={"alpha": "1" * 64, "beta": "2" * 64},
        documents=[_meta("alpha"), _meta("beta")],
        chunks=chunks,
    )


def test_tokenize() -> None:
    assert {"the", "a", "should", "must", "may", "which", "its"} <= STOPWORDS
    assert 50 <= len(STOPWORDS) <= 80
    assert tokenize("The AUC must be >= 0.70 for SR 11-7, per IFRS-9.") == [
        "auc",
        "0.70",
        "sr",
        "11-7",
        "per",
        "ifrs-9",
    ]


def test_tokenize_and_search_rank_by_relevance() -> None:
    index = _toy_index()
    retriever = Retriever(index)
    hits = retriever.search("effective challenge")
    assert [h.section for h in hits][0] == "One"
    assert {h.section for h in hits} == {"One", "Two", "Three"}
    assert all(h.score > 0 for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)

    assert [h.section for h in retriever.search("effective challenge", sources=["beta"])] == [
        "Three"
    ]
    with pytest.raises(KeyError):
        retriever.search("effective", sources=["gamma"])
    assert len(retriever.search("effective challenge", k=1)) == 1
    assert retriever.search("the of and") == []
    assert retriever.search("nonexistentword") == []

    assert retriever.get(index.chunks[2].citation_id) == index.chunks[2]
    assert retriever.get("k_missing") is None
    assert retriever.documents == index.documents


def test_ties_break_by_citation_id() -> None:
    chunks = [_chunk("d", f"S{i}", "identical tie text", i) for i in range(4)]
    index = KnowledgeIndex(built_from={"d": "0" * 64}, documents=[_meta("d")], chunks=chunks)
    hits = Retriever(index).search("tie")
    assert len({h.score for h in hits}) == 1
    assert [h.citation_id for h in hits] == sorted(c.citation_id for c in chunks)


def test_golden_questions_recall_at_5(knowledge_dir: Path) -> None:
    retriever = Retriever(build_index(load_corpus(knowledge_dir)))
    entries = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) == 8
    misses: list[str] = []
    for entry in entries:
        hits = retriever.search(entry["question"], k=5)
        needle = str(entry["must_contain"]).lower()
        if not any(h.doc_id == entry["doc_id"] and needle in h.text.lower() for h in hits):
            top = [(h.doc_id, h.section) for h in hits]
            misses.append(f"{entry['question']!r} -> wanted {entry['doc_id']}; got {top}")
    assert misses == [], "\n".join(misses)


def test_section_headings_are_searchable() -> None:
    chunks = [
        _chunk("d", "Governance > Four-eyes rule", "The approver differs from the requester.", 0),
        _chunk("d", "Other", "Unrelated words about binning.", 1),
    ]
    index = KnowledgeIndex(built_from={"d": "0" * 64}, documents=[_meta("d")], chunks=chunks)
    hits = Retriever(index).search("four-eyes")
    assert [h.section for h in hits] == ["Governance > Four-eyes rule"]
    assert hits[0].text == "The approver differs from the requester."
