"""Tests for scripts/fresh_clone_test.sh: dry-run steps, busy-port refusal, bash syntax."""

import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "fresh_clone_test.sh"
API_PORT = 8000


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=60)


def _port_answers(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture
def busy_api_port() -> Iterator[None]:
    """Make sure something answers on port 8000: bind one if free, else reuse the one there."""
    if _port_answers(API_PORT):
        yield
        return
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", API_PORT))
    except OSError:
        listener.close()
        pytest.skip("port 8000 is neither free to bind nor answering")
    listener.listen(8)
    try:
        yield
    finally:
        listener.close()


def test_dry_run_lists_the_steps() -> None:
    result = _run("https://example.invalid/model-forge.git", "--dry-run")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    expected = [
        "git clone --depth 1",
        "COMPOSE_PROJECT_NAME=modelforge-fresh",
        "MODELFORGE_POSTGRES_PORT=5434",
        "make demo",
        "docker compose down -v",
    ]
    positions = [out.find(needle) for needle in expected]
    assert all(pos >= 0 for pos in positions), out
    assert positions == sorted(positions), out
    assert "https://example.invalid/model-forge.git" in out


def test_fresh_clone_refuses_busy_port(busy_api_port: None) -> None:
    result = _run("--port-check-only")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "fresh-clone: something already answers on port 8000" in result.stdout + result.stderr


def test_bash_syntax() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
