"""The run's document and validation report (spec 3.3)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from modelforge_core.schemas import ValidationReport
from modelforge_orchestrator.inspect import run_refs

from modelforge_api.deps import get_settings
from modelforge_api.documents import document_response
from modelforge_api.errors import ApiError
from modelforge_api.routers.runs import Db, Run, Store
from modelforge_api.schemas import DocumentResponse
from modelforge_api.settings import ApiSettings

__all__ = ["router"]

router = APIRouter(prefix="/runs", tags=["documents"])

_RENDERED = {"md": "document_md", "html": "document_html"}


@router.get(
    "/{run_id}/document",
    response_model=DocumentResponse,
    responses={200: {"description": "The sections as JSON, or the stored Markdown or HTML."}},
)
def document(
    run: Run,
    db: Db,
    store: Store,
    settings: Annotated[ApiSettings, Depends(get_settings)],
    format: Literal["sections", "html", "md"] = "sections",
) -> DocumentResponse | Response:
    if format == "sections":
        return document_response(db, store, run.id, settings.core.templates_dir)
    ref = run_refs(db, run.id).get(_RENDERED[format])
    if ref is None:
        raise ApiError(409, "not_ready", f"run {run.id} has no document yet", run_id=run.id)
    return Response(content=store.get(ref.id), media_type=ref.media_type)


@router.get("/{run_id}/validation", response_model=ValidationReport)
def validation(run: Run, db: Db, store: Store) -> ValidationReport:
    ref = run_refs(db, run.id).get("validation")
    if ref is None:
        raise ApiError(
            409, "not_ready", f"run {run.id} has no validation report yet", run_id=run.id
        )
    return store.get_model(ref.id, ValidationReport)
