from pathlib import Path

import pytest


@pytest.fixture
def repo_profiles_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "profiles"
