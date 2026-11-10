import json
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from modelforge_api.fixtures import FIXTURE_FILES, capture
from modelforge_lineage.db import Database

from .conftest import REPO, make_settings

EXPECTED = [
    "config.json",
    "briefs.json",
    "runs.json",
    "run.json",
    "steps.json",
    "llm-calls.json",
    "artifacts.json",
    "document.json",
    "validation.json",
    "findings.json",
    "approvals.json",
    "manifest.json",
    "verify.json",
    "events.json",
    "replays.json",
    "inventory.json",
    "costs.json",
    "settings.json",
]


@pytest.fixture(scope="module")
def captured(
    tmp_path_factory: pytest.TempPathFactory, database: Database
) -> Iterator[tuple[Path, list[Path]]]:
    """One capture for the module's tests: the output directory and the written paths."""
    work = tmp_path_factory.mktemp("capture")
    out = work / "fixtures"
    yield out, capture(make_settings(work, database.url), out)


@pytest.mark.db
@pytest.mark.slow
def test_fixtures_command_writes_every_file(captured: tuple[Path, list[Path]]) -> None:
    out, written = captured
    assert list(FIXTURE_FILES) == EXPECTED
    assert sorted(p.name for p in written) == sorted([*EXPECTED, "run-id.txt"])
    assert sorted(p.name for p in out.iterdir()) == sorted([*EXPECTED, "run-id.txt"])
    loaded = {name: json.loads((out / name).read_text()) for name in EXPECTED}
    run_id = (out / "run-id.txt").read_text().strip()

    assert loaded["run.json"]["run"]["id"] == run_id
    assert loaded["run.json"]["run"]["status"] == "completed"
    assert len(loaded["document.json"]["sections"]) == 11
    assert [a["gate"] for a in loaded["approvals.json"]] == ["scope", "model", "signoff"]
    assert [a["approver"] for a in loaded["approvals.json"]] == ["alice", "bob", "bob"]
    replays = loaded["replays.json"]
    assert len(replays) == 1
    assert replays[0]["report"]["ok"] is True
    assert loaded["verify.json"]["ok"] is True
    assert {r["id"] for r in loaded["runs.json"]["runs"]} == {run_id, replays[0]["run"]["id"]}
    assert (out / "config.json").read_text().startswith('{\n  "')  # indent=2


@pytest.mark.db
@pytest.mark.slow
def test_fixtures_are_normalised(captured: tuple[Path, list[Path]]) -> None:
    out, written = captured
    config = json.loads((out / "config.json").read_text())
    assert set(config["directories"].values()) == {"<dir>"}
    settings = json.loads((out / "settings.json").read_text())
    assert set(settings["config"]["directories"].values()) == {"<dir>"}
    artifacts = json.loads((out / "artifacts.json").read_text())
    assert artifacts
    assert all(a["uri"] == f"artifact://{a['id']}" for a in artifacts)
    machine = {str(out.parent), tempfile.gettempdir(), str(REPO), "postgresql://"}
    for path in written:
        text = path.read_text()
        for value in machine:
            assert value not in text, (path.name, value)
