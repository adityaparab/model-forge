"""knowledge server: BM25 search over the regulatory and methodology corpus, passage lookup."""

from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from modelforge_core.schemas import StrictModel
from modelforge_knowledge.corpus import CorpusError, load_corpus
from modelforge_knowledge.index import build_index
from modelforge_knowledge.retrieval import Retriever
from modelforge_lineage.models import ArtifactRef
from pydantic import BaseModel, Field

from modelforge_mcp.common import (
    ServerName,
    ServerSettings,
    ToolContext,
    ToolSpec,
    fail,
    make_context,
    make_server,
    producer,
    register_tool,
)
from modelforge_mcp.schemas import Passage, Passages

SERVER_NAME: ServerName = "knowledge"
SERVER_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"
INSTRUCTIONS = (
    "Searches the regulatory and methodology knowledge base (SR 11-7, SR 26-2, OCC, CECL, "
    "IFRS 9, Basel IRB and ModelForge standards). search_regulation returns ranked passages "
    "with stable citation ids to cite as [K:<citation_id>]; get_passage returns one passage by "
    "its id; get_index returns the stored index artifact the passages come from."
)


class SearchRegulationInput(StrictModel):
    query: str = Field(min_length=1)
    sources: list[str] | None = None
    k: int = Field(default=5, ge=1, le=20)


class GetPassageInput(StrictModel):
    citation_id: str


class GetIndexInput(StrictModel):
    pass


class KnowledgeState:
    """The index built once at server start: its stored artifact and a retriever over it."""

    def __init__(self, ref: ArtifactRef, retriever: Retriever) -> None:
        self.ref = ref
        self.retriever = retriever
        self.documents = {d.id: d for d in retriever.documents}

    def passage(
        self, *, citation_id: str, doc_id: str, section: str, text: str, score: float
    ) -> Passage:
        doc = self.documents[doc_id]
        return Passage(
            citation_id=citation_id,
            doc_id=doc_id,
            title=doc.title,
            section=section,
            text=text,
            score=score,
            url=doc.url,
        )


def load_index(ctx: ToolContext) -> KnowledgeState:
    """Load the corpus from ``knowledge_dir``, build the index and store it as an artifact."""
    knowledge_dir = ctx.settings.knowledge_dir
    try:
        documents = load_corpus(knowledge_dir)
    except CorpusError as exc:
        raise RuntimeError(f"knowledge corpus at {knowledge_dir} cannot be loaded: {exc}") from exc
    index = build_index(documents)
    ref = ctx.store.put_model(
        index,
        kind="knowledge_index",
        producer=producer(ctx, "get_index", TOOL_VERSION),
        summary={
            "n_documents": len(index.documents),
            "n_chunks": len(index.chunks),
            "index_version": index.index_version,
        },
    )
    return KnowledgeState(ref, Retriever(index))


def search_regulation(state: KnowledgeState, req: SearchRegulationInput) -> Passages:
    try:
        hits = state.retriever.search(req.query, k=req.k, sources=req.sources)
    except KeyError as exc:
        fail("invalid_input", str(exc.args[0]))
    return Passages(
        index_id=state.ref.id,
        hits=[
            state.passage(
                citation_id=h.citation_id,
                doc_id=h.doc_id,
                section=h.section,
                text=h.text,
                score=h.score,
            )
            for h in hits
        ],
    )


def get_passage(state: KnowledgeState, req: GetPassageInput) -> Passage:
    chunk = state.retriever.get(req.citation_id)
    if chunk is None:
        fail("invalid_input", f"unknown citation id {req.citation_id!r}")
    return state.passage(
        citation_id=chunk.citation_id,
        doc_id=chunk.doc_id,
        section=chunk.section,
        text=chunk.text,
        score=0.0,
    )


def get_index(state: KnowledgeState, req: GetIndexInput) -> ArtifactRef:
    return state.ref


_TOOLS = [
    (
        "search_regulation",
        "Search the knowledge base (BM25) and return up to k ranked passages, each with a "
        "stable citation id, its document title and url; sources restricts the search to the "
        "given document ids.",
        search_regulation,
        SearchRegulationInput,
        Passages,
    ),
    (
        "get_passage",
        "Return one knowledge-base passage by its citation id.",
        get_passage,
        GetPassageInput,
        Passage,
    ),
    (
        "get_index",
        "Return the stored knowledge index artifact, with its document and chunk counts and "
        "index version.",
        get_index,
        GetIndexInput,
        ArtifactRef,
    ),
]

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        server=SERVER_NAME,
        tool=name,
        version=TOOL_VERSION,
        input_model=input_model,
        output_model=output_model,
    )
    for name, _, _, input_model, output_model in _TOOLS
]


def _bind[I: BaseModel, O: BaseModel](
    fn: Callable[[KnowledgeState, I], O], state: KnowledgeState
) -> Callable[[ToolContext, I], O]:
    return lambda _ctx, req: fn(state, req)


def build(settings: ServerSettings) -> MCPServer:
    server = make_server(SERVER_NAME, SERVER_VERSION, INSTRUCTIONS)
    ctx = make_context(SERVER_NAME, settings)
    state = load_index(ctx)
    for name, description, fn, input_model, output_model in _TOOLS:
        register_tool(
            server,
            ctx,
            name=name,
            version=TOOL_VERSION,
            description=description,
            fn=_bind(fn, state),
            input_model=input_model,
            output_model=output_model,
        )
    return server
