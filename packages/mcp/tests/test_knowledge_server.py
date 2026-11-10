from collections.abc import Callable
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent
from modelforge_core.schemas import StrictModel
from modelforge_lineage.models import ArtifactRef
from modelforge_lineage.store import ArtifactStore
from modelforge_mcp.common import ServerName, ServerSettings, ToolErrorPayload
from modelforge_mcp.registry import build_server
from modelforge_mcp.schemas import Passage, Passages
from modelforge_mcp.servers.knowledge import (
    GetIndexInput,
    GetPassageInput,
    SearchRegulationInput,
)

CallFn = Callable[[ServerName, str, StrictModel], CallToolResult]


def _error_code(result: CallToolResult) -> str:
    assert result.is_error
    content = result.content[0]
    assert isinstance(content, TextContent)
    return ToolErrorPayload.model_validate_json(content.text.split(": ", 1)[1]).code


def _search(call: CallFn, **kwargs: object) -> Passages:
    result = call("knowledge", "search_regulation", SearchRegulationInput.model_validate(kwargs))
    assert not result.is_error, result.content
    return Passages.model_validate(result.structured_content)


def test_search_returns_passages_with_index_id(call: CallFn) -> None:
    passages = _search(call, query="effective challenge", k=3)
    assert 1 <= len(passages.hits) <= 3
    assert passages.hits[0].doc_id == "sr11-7"
    assert len(passages.index_id) == 64
    top = passages.hits[0]
    assert top.title and top.url.startswith("http") and top.score > 0


def test_search_reports_the_index_artifact_id(call: CallFn) -> None:
    index = ArtifactRef.model_validate(
        call("knowledge", "get_index", GetIndexInput()).structured_content
    )
    assert _search(call, query="effective challenge").index_id == index.id


def test_get_passage_round_trip_and_unknown(call: CallFn) -> None:
    hit = _search(call, query="effective challenge", k=1).hits[0]
    result = call("knowledge", "get_passage", GetPassageInput(citation_id=hit.citation_id))
    assert not result.is_error, result.content
    passage = Passage.model_validate(result.structured_content)
    assert passage.model_dump(exclude={"score"}) == hit.model_dump(exclude={"score"})
    assert passage.score == 0.0
    unknown = call("knowledge", "get_passage", GetPassageInput(citation_id="k_nope"))
    assert _error_code(unknown) == "invalid_input"


def test_get_index_is_an_artifact(call: CallFn, settings: ServerSettings) -> None:
    first = call("knowledge", "get_index", GetIndexInput())
    assert not first.is_error, first.content
    ref = ArtifactRef.model_validate(first.structured_content)
    assert ref.kind == "knowledge_index"
    assert ArtifactStore(settings.artifact_dir).exists(ref.id)
    assert ref.summary["n_chunks"] > 20
    assert ref.summary["n_documents"] == 8
    assert ref.summary["index_version"] == "1"
    again = ArtifactRef.model_validate(
        call("knowledge", "get_index", GetIndexInput()).structured_content
    )
    assert again.id == ref.id


def test_unknown_source_is_invalid_input(call: CallFn) -> None:
    result = call(
        "knowledge",
        "search_regulation",
        SearchRegulationInput(query="effective challenge", sources=["no-such-doc"]),
    )
    assert _error_code(result) == "invalid_input"


def test_sources_restrict_the_search(call: CallFn) -> None:
    passages = _search(call, query="effective challenge", sources=["sr26-2"])
    assert passages.hits and {h.doc_id for h in passages.hits} == {"sr26-2"}


def test_build_without_corpus_raises(tmp_path: Path, settings: ServerSettings) -> None:
    empty = tmp_path / "empty-kb"
    empty.mkdir()
    bad = settings.model_copy(update={"knowledge_dir": empty})
    with pytest.raises(RuntimeError, match="empty-kb"):
        build_server("knowledge", bad)
