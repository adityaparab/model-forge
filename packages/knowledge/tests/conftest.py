"""Shared fixtures for the knowledge package tests."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def knowledge_dir() -> Path:
    return REPO / "knowledge-base"
