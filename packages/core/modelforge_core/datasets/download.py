"""Checksummed file download. The only module in modelforge_core allowed to import httpx."""

import hashlib
from pathlib import Path

import httpx

_CHUNK_SIZE = 1024 * 1024


class ChecksumMismatchError(RuntimeError):
    """Raised when a downloaded file's SHA-256 does not match the expected digest."""

    def __init__(self, url: str, expected: str, actual: str) -> None:
        super().__init__(f"checksum mismatch for {url}: expected sha256 {expected}, got {actual}")
        self.expected = expected
        self.actual = actual


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stream_to(url: str, tmp_path: Path, timeout: float) -> None:
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
        response.raise_for_status()
        with tmp_path.open("wb") as fh:
            for chunk in response.iter_bytes():
                fh.write(chunk)


def fetch(url: str, sha256: str, dest: Path, *, timeout: float = 120.0) -> Path:
    """Download url to dest unless a file with the expected SHA-256 is already there."""
    expected = sha256.lower()
    if dest.exists() and sha256_file(dest) == expected:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(dest.suffix + ".part")
    _stream_to(url, tmp_path, timeout)
    actual = sha256_file(tmp_path)
    if actual != expected:
        tmp_path.unlink(missing_ok=True)
        raise ChecksumMismatchError(url, sha256, actual)
    tmp_path.replace(dest)
    return dest
