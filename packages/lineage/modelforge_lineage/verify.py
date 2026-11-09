"""Tamper check for one run: the event chain, every stored artifact, and the manifest."""

import json
from typing import Any

from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_lineage.canonical import sha256_hex
from modelforge_lineage.events import list_events, verify_chain
from modelforge_lineage.models import VerifyReport
from modelforge_lineage.runs import get_run
from modelforge_lineage.steps import list_artifacts
from modelforge_lineage.store import ArtifactStore


def _load_manifest(store: ArtifactStore, manifest_hash: str | None) -> dict[str, Any] | None:
    if manifest_hash is None or not store.exists(manifest_hash):
        return None
    try:
        manifest = json.loads(store.get(manifest_hash))
    except ValueError:
        return None
    return manifest if isinstance(manifest, dict) else None


def _refs_ok(manifest: dict[str, Any] | None, artifact_ids: set[str]) -> bool:
    if manifest is None or not isinstance(manifest.get("refs"), dict):
        return False
    for ref in manifest["refs"].values():
        if not isinstance(ref, dict) or ref.get("id") not in artifact_ids:
            return False
    return True


def _head_anchored(manifest: dict[str, Any] | None, event_hashes: set[str]) -> bool:
    """The manifest's ``events_head_hash``, when it names one, is an event of the run's chain."""
    if manifest is None:
        return False
    head = manifest.get("events_head_hash")
    return head is None or head in event_hashes


def verify_run(conn: Connection[DictRow], store: ArtifactStore, run_id: str) -> VerifyReport:
    run = get_run(conn, run_id)
    chain = verify_chain(conn, run_id)
    rows = list_artifacts(conn, run_id)
    missing: list[str] = []
    corrupt: list[str] = []
    for row in rows:
        if not store.exists(row.id):
            missing.append(row.id)
        elif sha256_hex(store.get(row.id)) != row.id:
            corrupt.append(row.id)
    manifest = _load_manifest(store, run.manifest_hash)
    anchored = _head_anchored(manifest, {e.hash for e in list_events(conn, run_id)})
    manifest_ok = _refs_ok(manifest, {r.id for r in rows}) and anchored
    return VerifyReport(
        run_id=run_id,
        chain=chain,
        artifacts_checked=len(rows),
        artifacts_missing=missing,
        artifacts_corrupt=corrupt,
        manifest_ok=manifest_ok,
        events_head_anchored=anchored,
        ok=chain.ok and not missing and not corrupt and manifest_ok,
    )
