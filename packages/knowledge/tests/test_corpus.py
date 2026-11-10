"""Corpus loading: front-matter parsing and rejection of malformed files."""

import hashlib
from pathlib import Path

import pytest
from modelforge_knowledge.corpus import CorpusError, load_corpus

GOOD = """---
id: good-doc
title: "A Good Document"
source: "Somewhere"
authority: "Someone"
date: "2026-01-02"
url: "https://example.org/good"
kind: summary
status: "active"
note: "ignored extra key"
---

# Heading

Body text.
"""


def test_load_corpus_reads_front_matter_and_rejects_bad_files(tmp_path: Path) -> None:
    good = tmp_path / "good-doc.md"
    good.write_text(GOOD, encoding="utf-8")
    (doc,) = load_corpus(tmp_path)
    assert doc.id == "good-doc"
    assert doc.title == "A Good Document"
    assert doc.source == "Somewhere"
    assert doc.authority == "Someone"
    assert doc.date == "2026-01-02"
    assert doc.url == "https://example.org/good"
    assert doc.kind == "summary"
    assert doc.text == "# Heading\n\nBody text.\n"
    assert doc.sha256 == hashlib.sha256(good.read_bytes()).hexdigest()

    wrong_id = tmp_path / "other-name.md"
    wrong_id.write_text(GOOD, encoding="utf-8")
    with pytest.raises(CorpusError, match="other-name.md"):
        load_corpus(tmp_path)
    wrong_id.unlink()

    no_url = tmp_path / "no-url.md"
    no_url.write_text(
        GOOD.replace("id: good-doc", "id: no-url").replace('url: "https://example.org/good"\n', ""),
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="no-url.md"):
        load_corpus(tmp_path)
    no_url.unlink()

    bad_kind = tmp_path / "bad-kind.md"
    bad_kind.write_text(
        GOOD.replace("id: good-doc", "id: bad-kind").replace("kind: summary", "kind: memo"),
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="bad-kind.md"):
        load_corpus(tmp_path)
    bad_kind.unlink()

    no_front = tmp_path / "no-front.md"
    no_front.write_text("# Just a heading\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="no-front.md"):
        load_corpus(tmp_path)


def test_load_corpus_rejects_an_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(CorpusError):
        load_corpus(tmp_path)


def test_shipped_corpus_loads(knowledge_dir: Path) -> None:
    docs = load_corpus(knowledge_dir)
    assert {d.id for d in docs} >= {
        "sr11-7",
        "sr26-2",
        "modeling-standards",
        "modelforge-governance",
    }
    assert [d.id for d in docs] == sorted(d.id for d in docs)
