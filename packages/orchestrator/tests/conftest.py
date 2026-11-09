import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from modelforge_lineage.db import Database
from modelforge_lineage.testing import database, database_url, db
from modelforge_orchestrator.config import Settings

__all__ = ["REPO", "checkpointer", "database", "database_url", "db", "settings"]

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def settings(tmp_path: Path, database: Database) -> Settings:
    return Settings(
        database_url=database.url,
        artifact_dir=tmp_path / "artifacts",
        data_dir=Path(os.environ.get("MODELFORGE_DATA_DIR", REPO / "data")),
        templates_dir=REPO / "templates",
        profile="default",
        mcp_command=None,
        tool_timeout_s=600.0,
        log_level="INFO",
    )


@pytest.fixture(scope="session")
def checkpointer(database: Database) -> Iterator[PostgresSaver]:
    saver = PostgresSaver(database.pool)
    saver.setup()
    yield saver
