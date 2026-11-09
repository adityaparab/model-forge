"""Tamper check for one run: the event chain, every stored artifact, and the manifest."""

import json

from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_lineage.canonical import sha256_hex
from modelforge_lineage.events import verify_chain
from modelforge_lineage.models import VerifyReport
from modelforge_lineage.runs import get_run
from modelforge_lineage.steps import list_artifacts
from modelforge_lineage.store import ArtifactStore


def _manifest_ok(store: ArtifactStore, manifest_hash: str | None, artifact_ids: set[str]) -> bool:
    if manifest_hash is None or not store.exists(manifest_hash):
        return False
    try:
        manifest = json.loads(store.get(manifest_hash))
    except ValueError:
        return False
    if not isinstance(manifest, dict) or not isinstance(manifest.get("refs"), dict):
        return False
    for ref in manifest["refs"].values():
        if not isinstance(ref, dict) or ref.get("id") not in artifact_ids:
            return False
    return True


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
    manifest_ok = _manifest_ok(store, run.manifest_hash, {r.id for r in rows})
    return VerifyReport(
        run_id=run_id,
        chain=chain,
        artifacts_checked=len(rows),
        artifacts_missing=missing,
        artifacts_corrupt=corrupt,
        manifest_ok=manifest_ok,
        ok=chain.ok and not missing and not corrupt and manifest_ok,
    )
