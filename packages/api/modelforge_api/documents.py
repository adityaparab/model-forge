"""The document view: section bodies with their citations resolved (spec 3.3.2).

Pure functions over the artifact store and the knowledge index; `document_response` reads the
run's refs and artifact rows, everything else needs no database.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from modelforge_knowledge.index import KnowledgeIndex
from modelforge_lineage.citations import PathError, resolve_path
from modelforge_lineage.db import Database
from modelforge_lineage.models import ArtifactRow
from modelforge_lineage.steps import list_artifacts
from modelforge_lineage.store import MEDIA_JSON, ArtifactStore
from modelforge_mcp.schemas import DocumentSections, TemplateSections
from modelforge_mcp.servers.doc_renderer import MARKER
from modelforge_orchestrator.inspect import run_refs

from modelforge_api.errors import ApiError
from modelforge_api.schemas import DocumentResponse, PassageView, ResolvedCitation, SectionView

__all__ = ["MAX_VALUE_CHARS", "document_response", "load_template", "resolve_citations"]

MAX_VALUE_CHARS = 2000
"""A list or object value longer than this many characters of JSON is truncated."""


def _resolve_artifact(
    marker: str,
    target: str,
    artifacts_of_run: Mapping[str, ArtifactRow],
    store: ArtifactStore,
    cache: dict[str, Any],
) -> ResolvedCitation:
    artifact_id, sep, path = target.partition("#")

    def unresolved(note: str, **fields: Any) -> ResolvedCitation:
        return ResolvedCitation(
            marker=marker,
            kind="A",
            target=target,
            resolved=False,
            artifact_id=artifact_id,
            path=path if sep else None,
            note=note,
            **fields,
        )

    if not sep or not path:
        return unresolved("marker has no path")
    row = artifacts_of_run.get(artifact_id)
    if row is None:
        return unresolved("artifact not in this run")
    if row.media_type != MEDIA_JSON:
        return unresolved("not a JSON artifact", artifact_kind=row.kind)
    if artifact_id not in cache:
        cache[artifact_id] = json.loads(store.get(artifact_id))
    try:
        value = resolve_path(cache[artifact_id], path)
    except PathError:
        return unresolved("path not found in the artifact", artifact_kind=row.kind)
    note = None
    if isinstance(value, (list, dict)):
        text = json.dumps(value)
        if len(text) > MAX_VALUE_CHARS:
            value, note = text[:MAX_VALUE_CHARS], "truncated"
    return ResolvedCitation(
        marker=marker,
        kind="A",
        target=target,
        resolved=True,
        artifact_id=artifact_id,
        artifact_kind=row.kind,
        path=path,
        value=value,
        note=note,
    )


def _resolve_passage(marker: str, target: str, index: KnowledgeIndex | None) -> ResolvedCitation:
    if index is None:
        return ResolvedCitation(
            marker=marker, kind="K", target=target, resolved=False, note="no knowledge index"
        )
    chunk = next((c for c in index.chunks if c.citation_id == target), None)
    if chunk is None:
        return ResolvedCitation(
            marker=marker, kind="K", target=target, resolved=False, note="unknown passage"
        )
    meta = next(d for d in index.documents if d.id == chunk.doc_id)
    return ResolvedCitation(
        marker=marker,
        kind="K",
        target=target,
        resolved=True,
        passage=PassageView(
            citation_id=chunk.citation_id,
            doc_id=chunk.doc_id,
            title=meta.title,
            section=chunk.section,
            text=chunk.text,
            url=meta.url,
        ),
    )


def resolve_citations(
    body_md: str,
    *,
    artifacts_of_run: Mapping[str, ArtifactRow],
    store: ArtifactStore,
    index: KnowledgeIndex | None,
    cache: dict[str, Any],
) -> list[ResolvedCitation]:
    """Each distinct marker of ``body_md``, in order of first occurrence, resolved.

    ``cache`` maps artifact id to its parsed JSON and is filled as artifacts are read, so a
    caller resolving several bodies reads each artifact once.
    """
    resolved: dict[str, ResolvedCitation] = {}
    for match in MARKER.finditer(body_md):
        marker, kind, target = match.group(0), match.group(1), match.group(2)
        if marker in resolved:
            continue
        resolved[marker] = (
            _resolve_artifact(marker, target, artifacts_of_run, store, cache)
            if kind == "A"
            else _resolve_passage(marker, target, index)
        )
    return list(resolved.values())


def load_template(templates_dir: Path, template_id: str) -> TemplateSections:
    """The template's sections from ``<templates_dir>/<template_id>/sections.yaml``."""
    raw = yaml.safe_load((templates_dir / template_id / "sections.yaml").read_text("utf-8"))
    return TemplateSections.model_validate({"template_id": template_id, "sections": raw})


def document_response(
    db: Database, store: ArtifactStore, run_id: str, templates_dir: Path
) -> DocumentResponse:
    """The run's current ``document_sections`` with titles from the template and resolved
    citations; ``409`` kind ``not_ready`` before the run has a document."""
    refs = run_refs(db, run_id)
    sections_ref, markdown_ref = refs.get("document_sections"), refs.get("document_md")
    if sections_ref is None or markdown_ref is None:
        raise ApiError(409, "not_ready", f"run {run_id} has no document yet", run_id=run_id)
    document = store.get_model(sections_ref.id, DocumentSections)
    specs = {s.id: s for s in load_template(templates_dir, document.template_id).sections}
    index_ref = refs.get("knowledge_index")
    index = store.get_model(index_ref.id, KnowledgeIndex) if index_ref is not None else None
    with db.connection() as conn:
        rows = {row.id: row for row in list_artifacts(conn, run_id)}
    cache: dict[str, Any] = {}
    views: list[SectionView] = []
    for section in document.sections:
        spec = specs.get(section.section_id)
        views.append(
            SectionView(
                section_id=section.section_id,
                title=spec.title if spec is not None else section.section_id,
                required=spec.required if spec is not None else False,
                body_md=section.body_md,
                grounding=section.grounding,
                citations=resolve_citations(
                    section.body_md,
                    artifacts_of_run=rows,
                    store=store,
                    index=index,
                    cache=cache,
                ),
            )
        )
    return DocumentResponse(
        template_id=document.template_id, document_id=markdown_ref.id, sections=views
    )
