from pathlib import Path

import pytest
from modelforge_core.schemas import StrictModel
from modelforge_lineage.db import Database
from modelforge_lineage.events import list_events
from modelforge_lineage.models import ArtifactRef
from modelforge_lineage.runs import set_manifest_hash
from modelforge_lineage.steps import insert_artifact
from modelforge_lineage.store import ArtifactStore
from modelforge_lineage.testing import create_test_run as _run
from modelforge_lineage.verify import verify_run


class _Manifest(StrictModel):
    refs: dict[str, ArtifactRef]


@pytest.mark.db
def test_verify_run_reports_tampered_artifact_and_event(db: Database, tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    with db.transaction() as conn:
        _run(conn)
        good = store.put(b"good", kind="profile", media_type="text/plain")
        insert_artifact(
            conn, run_id="run_a", ref=good, uri=store.uri(good.id), producer_step_id=None
        )
        manifest = store.put_model(_Manifest(refs={"profile": good}), kind="manifest")
        insert_artifact(
            conn, run_id="run_a", ref=manifest, uri=store.uri(manifest.id), producer_step_id=None
        )
        set_manifest_hash(conn, "run_a", manifest.id)
    with db.connection() as conn:
        assert verify_run(conn, store, "run_a").ok is True
    store.path(good.id).write_bytes(b"evil")
    with db.transaction() as conn:
        conn.execute("update events set payload = '{}' where id = (select min(id) from events)")
    with db.connection() as conn:
        report = verify_run(conn, store, "run_a")
    assert report.ok is False and report.artifacts_corrupt == [good.id]
    assert report.chain.first_bad_id is not None
    store.path(good.id).unlink()
    with db.connection() as conn:
        assert verify_run(conn, store, "run_a").artifacts_missing == [good.id]


class _AnchoredManifest(StrictModel):
    refs: dict[str, ArtifactRef]
    events_head_hash: str


@pytest.mark.db
def test_verify_run_requires_manifest_head_hash_in_chain(db: Database, tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    with db.transaction() as conn:
        _run(conn)
        good = store.put(b"good", kind="profile", media_type="text/plain")
        insert_artifact(
            conn, run_id="run_a", ref=good, uri=store.uri(good.id), producer_step_id=None
        )
        head = list_events(conn, "run_a")[-1].hash
        anchored = store.put_model(
            _AnchoredManifest(refs={"profile": good}, events_head_hash=head), kind="manifest"
        )
        unanchored = store.put_model(
            _AnchoredManifest(refs={"profile": good}, events_head_hash="f" * 64), kind="manifest"
        )
        for manifest in (anchored, unanchored):
            uri = store.uri(manifest.id)
            insert_artifact(conn, run_id="run_a", ref=manifest, uri=uri, producer_step_id=None)
        set_manifest_hash(conn, "run_a", anchored.id)
    with db.connection() as conn:
        report = verify_run(conn, store, "run_a")
    assert report.ok is True and report.events_head_anchored is True
    with db.transaction() as conn:
        set_manifest_hash(conn, "run_a", unanchored.id)
    with db.connection() as conn:
        report = verify_run(conn, store, "run_a")
    assert report.chain.ok is True
    assert report.ok is False and report.manifest_ok is False
    assert report.events_head_anchored is False
