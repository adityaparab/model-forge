"""Index building: determinism and pinning to the corpus content."""

import hashlib
import shutil
from pathlib import Path

from modelforge_knowledge.corpus import load_corpus
from modelforge_knowledge.index import KnowledgeIndex, build_index


def _digest(index: KnowledgeIndex) -> str:
    return hashlib.sha256(index.model_dump_json().encode()).hexdigest()


def test_build_index_is_deterministic_and_pins_corpus(knowledge_dir: Path) -> None:
    docs = load_corpus(knowledge_dir)
    a, b = build_index(docs), build_index(docs)
    assert a == b and set(a.built_from) == {d.id for d in docs}
    assert a.model_dump_json() == b.model_dump_json()
    assert a.built_from == {d.id: d.sha256 for d in docs}
    assert [m.id for m in a.documents] == [d.id for d in docs]
    order = {d.id: i for i, d in enumerate(docs)}
    assert [order[c.doc_id] for c in a.chunks] == sorted(order[c.doc_id] for c in a.chunks)
    assert len({c.citation_id for c in a.chunks}) == len(a.chunks)
    assert KnowledgeIndex.model_validate_json(a.model_dump_json()) == a


def test_index_id_changes_with_corpus(tmp_path: Path, knowledge_dir: Path) -> None:
    corpus = tmp_path / "kb"
    shutil.copytree(knowledge_dir, corpus)
    first = build_index(load_corpus(corpus))

    target = corpus / "modelforge-governance.md"
    with target.open("a", encoding="utf-8") as fh:
        fh.write("\nAn appended paragraph about retention of approvals records.\n")
    second = build_index(load_corpus(corpus))

    assert _digest(first) != _digest(second)
    assert first.built_from["modelforge-governance"] != second.built_from["modelforge-governance"]

    def ids(index: KnowledgeIndex, *, changed: bool) -> list[str]:
        return [
            c.citation_id for c in index.chunks if (c.doc_id == "modelforge-governance") == changed
        ]

    assert ids(first, changed=False) == ids(second, changed=False)
    assert ids(first, changed=True) != ids(second, changed=True)
