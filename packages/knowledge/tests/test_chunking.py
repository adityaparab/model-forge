"""Heading-aware chunking with windowing and content-derived citation ids."""

import hashlib
import math

from modelforge_knowledge.chunking import chunk_document, citation_id
from modelforge_knowledge.corpus import Document


def _words(prefix: str, n: int) -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def _doc(text: str) -> Document:
    return Document(
        id="toy",
        title="Toy Document",
        source="s",
        authority="a",
        date="2026-01-01",
        url="u",
        kind="internal",
        text=text,
        sha256="0" * 64,
    )


def test_citation_id_formula() -> None:
    expected = "k_" + hashlib.sha256(b"d\x00s\x00t").hexdigest()[:12]
    assert citation_id("d", "s", "t") == expected


def test_chunk_document_sections_and_windows() -> None:
    text = (
        f"# T\n\n{_words('t', 30)}\n\n## A\n\n{_words('a', 1000)}\n\n### B\n\n{_words('b', 10)}\n"
    )
    chunks = chunk_document(_doc(text))

    windows = math.ceil((1000 - 400) / 360) + 1
    assert [c.section for c in chunks] == ["T"] + ["T > A"] * windows + ["T > A > B"]
    assert [c.position for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.doc_id == "toy"
        assert chunk.citation_id == citation_id(chunk.doc_id, chunk.section, chunk.text)

    a_chunks = [c.text.split() for c in chunks if c.section == "T > A"]
    assert all(len(w) == 400 for w in a_chunks)
    assert a_chunks[0][0] == "a0" and a_chunks[1][0] == "a360"
    assert a_chunks[-1][-1] == "a999"
    assert chunks[0].text == _words("t", 30)


def test_preamble_uses_title_and_empty_sections_are_skipped() -> None:
    text = "intro words here\n\n# Empty\n\n## Sub\n\nsub words\n\n## Later\n\nlater words\n"
    chunks = chunk_document(_doc(text))
    assert [(c.section, c.text) for c in chunks] == [
        ("Toy Document", "intro words here"),
        ("Empty > Sub", "sub words"),
        ("Empty > Later", "later words"),
    ]


def test_section_at_the_window_limit_stays_one_chunk() -> None:
    chunks = chunk_document(_doc(f"# S\n{_words('w', 440)}\n"))
    assert len(chunks) == 1
    assert len(chunks[0].text.split()) == 440


def test_citation_ids_are_stable_under_edits_elsewhere() -> None:
    before = chunk_document(_doc(f"# T\n## A\n{_words('a', 50)}\n## B\n{_words('b', 20)}\n"))
    after = chunk_document(_doc(f"# T\n## A\n{_words('a', 50)}\n## B\n{_words('x', 900)}\n"))
    ids_before = [c.citation_id for c in before if c.section == "T > A"]
    ids_after = [c.citation_id for c in after if c.section == "T > A"]
    assert ids_before and ids_before == ids_after
    assert {c.citation_id for c in before if c.section == "T > B"}.isdisjoint(
        {c.citation_id for c in after if c.section == "T > B"}
    )
