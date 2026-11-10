"""Run creation from a brief, shared by the ``modelforge run`` command, the API and the evals."""

from pathlib import Path

from modelforge_core.datasets.registry import ADAPTERS
from modelforge_gateway.gateway import check_credentials
from modelforge_gateway.profiles import load_profile
from modelforge_gateway.settings import GatewaySettings
from modelforge_lineage import runs
from modelforge_lineage.db import Database
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import RunRecord
from modelforge_lineage.queue import enqueue
from modelforge_lineage.steps import insert_artifact
from modelforge_lineage.store import MEDIA_YAML, ArtifactStore
from modelforge_lineage.tracing import new_trace_id

from modelforge_orchestrator.briefs import BriefError, brief_hash, parse_brief, plan_from_brief

__all__ = ["UnknownAdapterError", "create_run", "create_run_from_brief"]


class UnknownAdapterError(KeyError):
    """The brief names a dataset adapter the registry does not know."""

    def __init__(self, adapter: str) -> None:
        super().__init__(adapter)
        self.adapter = adapter

    def __str__(self) -> str:
        return f"unknown dataset adapter {self.adapter!r}; known: {sorted(ADAPTERS)}"


def create_run_from_brief(
    db: Database,
    store: ArtifactStore,
    brief_path: Path,
    *,
    requester: str,
    profile: str,
    cost_cap_usd: float,
    gateway: GatewaySettings,
) -> RunRecord:
    """`create_run` from the brief at ``brief_path``; `OSError` when it cannot be read."""
    return create_run(
        db,
        store,
        brief_path.read_bytes(),
        requester=requester,
        profile=profile,
        cost_cap_usd=cost_cap_usd,
        gateway=gateway,
    )


def create_run(
    db: Database,
    store: ArtifactStore,
    data: bytes,
    *,
    requester: str,
    profile: str,
    cost_cap_usd: float,
    gateway: GatewaySettings,
) -> RunRecord:
    """Validate the brief and the profile, store the brief, create the run and enqueue a start.

    Raises `BriefError` for a brief that does not parse or yields no valid plan,
    `UnknownAdapterError` (a `KeyError`) for an unknown dataset adapter and `GatewayError` for
    a profile that ``gateway`` cannot load or lacks credentials for; nothing is written before
    every check passes. The run gets a fresh trace id.
    """
    brief = parse_brief(data)
    if brief.dataset.adapter not in ADAPTERS:
        raise UnknownAdapterError(brief.dataset.adapter)
    try:
        plan_from_brief(brief)
    except ValueError as exc:
        raise BriefError(str(exc)) from exc
    check_credentials(load_profile(gateway.profiles_dir, profile), gateway)
    ref = store.put(data, kind="brief", media_type=MEDIA_YAML)
    run_id = new_id("run")
    with db.transaction() as conn:
        record = runs.create_run(
            conn,
            run_id=run_id,
            requester=requester,
            profile=profile,
            brief_hash=brief_hash(data),
            brief_ref=ref.id,
            dataset_ref=brief.dataset.as_string(),
            seed=brief.dataset.seed,
            cost_cap_usd=cost_cap_usd,
            trace_id=new_trace_id(),
        )
        insert_artifact(conn, run_id=run_id, ref=ref, uri=store.uri(ref.id), producer_step_id=None)
        enqueue(conn, run_id, "start")
    return record
