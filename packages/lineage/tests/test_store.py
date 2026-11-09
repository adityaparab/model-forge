import hashlib
import os
from pathlib import Path

import pytest
from modelforge_core.schemas import Thresholds
from modelforge_core.testing import make_synthetic_credit_frame
from modelforge_lineage.models import Producer
from modelforge_lineage.store import ArtifactNotFound, ArtifactStore


def test_put_get_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.put(b"hello", kind="brief", media_type="text/plain", summary={"n": 1})
    assert ref.id == hashlib.sha256(b"hello").hexdigest() and ref.size_bytes == 5
    assert store.path(ref.id) == tmp_path / ref.id[:2] / ref.id[2:4] / ref.id
    assert store.get(ref.id) == b"hello" and store.uri(ref.id).startswith("file://")
    assert store.put(b"hello", kind="other", media_type="text/plain").id == ref.id
    assert not list((tmp_path / "tmp").iterdir())


def test_get_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ArtifactNotFound):
        ArtifactStore(tmp_path).get("00" * 32)


def test_model_round_trip(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.put_model(
        Thresholds(), kind="thresholds", producer=Producer(server="s", tool="t", version="1.0.0")
    )
    assert ref.media_type == "application/json"
    assert ref.producer is not None and ref.producer.tool == "t"
    assert store.get_model(ref.id, Thresholds) == Thresholds()


def test_frame_round_trip_is_byte_deterministic(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    frame = make_synthetic_credit_frame(300, seed=1)
    a = store.put_frame(frame, kind="dataset")
    b = store.put_frame(frame.copy(), kind="dataset")
    assert a.id == b.id and a.media_type == "application/vnd.apache.parquet"
    back = store.get_frame(a.id)
    assert back.equals(frame) and list(back.dtypes) == list(frame.dtypes)


def test_partial_write_never_lands_under_a_valid_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(tmp_path)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.put(b"data", kind="k", media_type="text/plain")
    assert not store.exists(hashlib.sha256(b"data").hexdigest())
