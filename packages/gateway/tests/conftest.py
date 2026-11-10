from pathlib import Path

import pytest
from modelforge_lineage.testing import database, database_url, db, spans

__all__ = ["database", "database_url", "db", "spans"]


@pytest.fixture
def repo_profiles_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "profiles"
