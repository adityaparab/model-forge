import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from modelforge_api.documents import resolve_citations
from modelforge_knowledge.chunking import Chunk
from modelforge_knowledge.index import DocumentMeta, KnowledgeIndex
from modelforge_lineage.citations import resolve_path
from modelforge_lineage.models import ArtifactRef, ArtifactRow
from modelforge_lineage.store import MEDIA_JSON, MEDIA_MARKDOWN, ArtifactStore

from .conftest import Submit

API = "/api/v1"

VALIDATION_TEST_IDS = {
    "disc.auc.oot",
    "disc.auc.val",
    "disc.ks.oot",
    "disc.gap.train_oot",
    "cal.binomial",
    "cal.hl",
    "cal.eo",
    "stab.psi.score",
    "stab.csi.max",
    "sens.signs",
    "sens.monotonic",
    "bench.gap",
    "feat.iv_range",
}


def _get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(f"{API}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _row(ref: ArtifactRef) -> ArtifactRow:
    return ArtifactRow(
        id=ref.id,
        run_id="run-1",
        kind=ref.kind,
        media_type=ref.media_type,
        size_bytes=ref.size_bytes,
        uri=f"artifacts/{ref.id}",
        producer_step_id=None,
        schema_version="1",
    )


def _index() -> KnowledgeIndex:
    return KnowledgeIndex(
        built_from={"doc-1": "0" * 64},
        documents=[
            DocumentMeta(
                id="doc-1",
                title="Guidance",
                source="supervisor",
                authority="regulator",
                date="2026-01-01",
                url="https://example.org/guidance",
                kind="primary",
            )
        ],
        chunks=[
            Chunk(
                citation_id="k_aaaaaaaaaaaa",
                doc_id="doc-1",
                section="Scope",
                text="Models are validated.",
                position=0,
            )
        ],
    )


def test_resolve_citations_over_in_memory_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    data = {"by_split": [{"split": "oot", "auc": 0.874}]}
    ref = store.put(json.dumps(data).encode(), kind="discrimination", media_type=MEDIA_JSON)
    body = (
        f"AUC was 0.874 [A:{ref.id}#by_split.oot.auc] and [A:{ref.id}#nope] and [K:k_000000000000]"
    )

    cache: dict[str, Any] = {}
    value, missing_path, passage = resolve_citations(
        body, artifacts_of_run={ref.id: _row(ref)}, store=store, index=_index(), cache=cache
    )

    assert value.marker == f"[A:{ref.id}#by_split.oot.auc]"
    assert value.kind == "A" and value.target == f"{ref.id}#by_split.oot.auc"
    assert value.resolved and value.value == 0.874 and value.note is None
    assert value.artifact_id == ref.id and value.artifact_kind == "discrimination"
    assert value.path == "by_split.oot.auc"
    assert not missing_path.resolved and missing_path.value is None
    assert missing_path.artifact_id == ref.id and missing_path.path == "nope"
    assert missing_path.note is not None
    assert passage.kind == "K" and not passage.resolved and passage.passage is None
    assert passage.note is not None
    assert ref.id in cache


def test_resolve_citations_notes(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    big = store.put(
        json.dumps({"rows": list(range(1000))}).encode(), kind="profile", media_type=MEDIA_JSON
    )
    md = store.put(b"# Title", kind="document_md", media_type=MEDIA_MARKDOWN)
    outside = store.put(b'{"a": 1}', kind="psi", media_type=MEDIA_JSON)
    body = (
        f"[A:{big.id}#rows] [A:{md.id}#a] [A:{outside.id}#a] [K:k_aaaaaaaaaaaa]"
        f" [A:{big.id}#rows.count] [K:k_aaaaaaaaaaaa]"
    )

    truncated, not_json, not_in_run, passage, count = resolve_citations(
        body,
        artifacts_of_run={big.id: _row(big), md.id: _row(md)},
        store=store,
        index=_index(),
        cache={},
    )

    assert truncated.resolved and truncated.note == "truncated"
    assert isinstance(truncated.value, str) and len(truncated.value) == 2000
    assert not not_json.resolved and not_json.note == "not a JSON artifact"
    assert not_json.artifact_kind == "document_md"
    assert not not_in_run.resolved and not_in_run.note == "artifact not in this run"
    assert passage.resolved and passage.passage is not None
    assert passage.passage.model_dump() == {
        "citation_id": "k_aaaaaaaaaaaa",
        "doc_id": "doc-1",
        "title": "Guidance",
        "section": "Scope",
        "text": "Models are validated.",
        "url": "https://example.org/guidance",
    }
    assert count.resolved and count.value == 1000
    # without an index a passage cannot resolve
    (no_index,) = resolve_citations(
        "[K:k_aaaaaaaaaaaa]", artifacts_of_run={}, store=store, index=None, cache={}
    )
    assert not no_index.resolved and no_index.note is not None


@pytest.mark.db
def test_document_sections_resolve(client: TestClient, completed_run: str, submit: Submit) -> None:
    document = _get(client, f"/runs/{completed_run}/document")
    detail = _get(client, f"/runs/{completed_run}")
    refs = detail["refs"]
    assert document["template_id"] == "sr11-7-dev-doc"
    assert document["document_id"] == refs["document_md"]["id"]
    sections = document["sections"]
    assert len(sections) == 11
    by_id = {s["section_id"]: s for s in sections}
    assert by_id["executive_summary"]["title"] == "Executive summary"
    assert by_id["executive_summary"]["required"] is True
    assert all(s["title"] and s["grounding"]["ok"] for s in sections)

    citations = [c for s in sections for c in s["citations"]]
    resolved_a = [c for c in citations if c["kind"] == "A" and c["resolved"] and not c["note"]]
    assert resolved_a
    for citation in resolved_a:
        content = _get(client, f"/runs/{completed_run}/artifacts/{citation['artifact_id']}")
        assert citation["value"] == resolve_path(content, citation["path"])
    index = _get(client, f"/runs/{completed_run}/artifacts/{refs['knowledge_index']['id']}")
    doc_ids = {d["id"] for d in index["documents"]}
    resolved_k = [c for c in citations if c["kind"] == "K" and c["resolved"]]
    assert resolved_k
    assert all(c["passage"]["doc_id"] in doc_ids for c in resolved_k)

    md = client.get(f"{API}/runs/{completed_run}/document", params={"format": "md"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    html = client.get(f"{API}/runs/{completed_run}/document", params={"format": "html"})
    assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
    bad = client.get(f"{API}/runs/{completed_run}/document", params={"format": "pdf"})
    assert bad.status_code == 422

    queued = submit()
    for fmt in ("sections", "md", "html"):
        response = client.get(f"{API}/runs/{queued}/document", params={"format": fmt})
        assert response.status_code == 409
        assert response.json()["error"]["kind"] == "not_ready"
    missing = client.get(f"{API}/runs/nope/document")
    assert missing.status_code == 404 and missing.json()["error"]["kind"] == "not_found"


@pytest.mark.db
def test_validation_report(client: TestClient, completed_run: str, submit: Submit) -> None:
    report = _get(client, f"/runs/{completed_run}/validation")
    assert report["model_name"] == "synthetic_pd"
    assert {t["test_id"] for t in report["tests"]} == VALIDATION_TEST_IDS
    assert len(report["tests"]) == 13
    assert report["overall"] in {"pass", "warn", "fail"}
    assert "thresholds" in report

    queued = submit()
    response = client.get(f"{API}/runs/{queued}/validation")
    assert response.status_code == 409 and response.json()["error"]["kind"] == "not_ready"
