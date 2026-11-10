"""The knowledge index: document metadata, chunks and the corpus hashes they came from."""

from modelforge_core.schemas import StrictModel

from modelforge_knowledge.chunking import Chunk, chunk_document
from modelforge_knowledge.corpus import Document, DocumentKind


class DocumentMeta(StrictModel):
    id: str
    title: str
    source: str
    authority: str
    date: str
    url: str
    kind: DocumentKind


class KnowledgeIndex(StrictModel):
    index_version: str = "1"
    built_from: dict[str, str]
    documents: list[DocumentMeta]
    chunks: list[Chunk]


def build_index(documents: list[Document]) -> KnowledgeIndex:
    """Deterministic: documents and chunks keep corpus order; ``built_from`` maps id to sha256."""
    return KnowledgeIndex(
        built_from={doc.id: doc.sha256 for doc in documents},
        documents=[
            DocumentMeta(
                id=doc.id,
                title=doc.title,
                source=doc.source,
                authority=doc.authority,
                date=doc.date,
                url=doc.url,
                kind=doc.kind,
            )
            for doc in documents
        ],
        chunks=[chunk for doc in documents for chunk in chunk_document(doc)],
    )
