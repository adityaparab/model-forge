"""The run's challenger findings with the developer's responses (spec 3.3)."""

from fastapi import APIRouter
from modelforge_lineage.findings import list_findings
from modelforge_orchestrator.inspect import run_state
from modelforge_orchestrator.schemas import FindingResponse, ResponsesList

from modelforge_api.errors import ApiError
from modelforge_api.routers.runs import Db, Run, Store
from modelforge_api.schemas import FindingRow, FindingsResponse, FindingsSummary

__all__ = ["router"]

router = APIRouter(prefix="/runs", tags=["findings"])


@router.get("/{run_id}/findings", response_model=FindingsResponse)
def findings(run: Run, db: Db, store: Store) -> FindingsResponse:
    state = run_state(db, run.id)
    findings_ref = state.refs.get("findings") if state is not None else None
    if state is None or findings_ref is None:
        raise ApiError(409, "not_ready", f"run {run.id} has no findings yet", run_id=run.id)
    with db.connection() as conn:
        rows = list_findings(conn, run.id)
    responses_ref = state.refs.get("responses")
    responses: list[FindingResponse] = (
        store.get_model(responses_ref.id, ResponsesList).responses
        if responses_ref is not None
        else []
    )
    counts = findings_ref.summary
    summary = FindingsSummary(
        n_findings=counts["n_findings"],
        by_severity=counts["by_severity"],
        by_status=counts["by_status"],
        by_source=counts["by_source"],
        max_severity=counts["max_severity"],
        max_open_severity=counts["max_open_severity"],
        challenge_rounds=state.counters.challenge_rounds,
        remediation_rounds=state.counters.remediation_rounds,
        sections_regenerated=list(
            dict.fromkeys(s for r in responses for s in r.sections_regenerated)
        ),
    )
    return FindingsResponse(
        findings=[
            FindingRow(**{**row, "id": row["id"].rpartition("/")[2], "row_id": row["id"]})
            for row in rows
        ],
        responses=responses,
        summary=summary,
    )
