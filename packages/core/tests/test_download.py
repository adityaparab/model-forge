import hashlib
from pathlib import Path

import pytest
from modelforge_core.datasets.download import ChecksumMismatchError, fetch


def test_fetch_returns_cached_file_when_hash_matches(tmp_path: Path) -> None:
    dest = tmp_path / "x.bin"
    dest.write_bytes(b"hello")
    expected = hashlib.sha256(b"hello").hexdigest()
    assert fetch("http://unused.invalid/x", expected, dest) == dest


def _fake_stream(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    from modelforge_core.datasets import download

    monkeypatch.setattr(
        download, "_stream_to", lambda url, tmp_path, timeout: tmp_path.write_bytes(payload)
    )


def test_fetch_downloads_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stream(monkeypatch, b"payload")
    out = fetch("http://x.invalid/p", hashlib.sha256(b"payload").hexdigest(), tmp_path / "p")
    assert out.read_bytes() == b"payload" and not (tmp_path / "p.part").exists()


def test_fetch_rejects_bad_hash_and_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_stream(monkeypatch, b"payload")
    with pytest.raises(ChecksumMismatchError) as exc:
        fetch("http://x.invalid/p", "00" * 32, tmp_path / "p")
    assert exc.value.expected == "00" * 32
    assert exc.value.actual == hashlib.sha256(b"payload").hexdigest()
    assert not (tmp_path / "p").exists() and not (tmp_path / "p.part").exists()


def test_fetch_redownloads_when_cached_file_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "p"
    dest.write_bytes(b"truncated")
    _fake_stream(monkeypatch, b"payload")
    out = fetch("http://x.invalid/p", hashlib.sha256(b"payload").hexdigest(), dest)
    assert out.read_bytes() == b"payload"
