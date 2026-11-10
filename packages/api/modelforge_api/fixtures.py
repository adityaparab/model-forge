"""``modelforge-api fixtures``: the read endpoints' responses for one completed synthetic run,
written as the console tests' data (spec 6.4).

The run is driven on a throwaway database and artifact directory through the API itself:
submitted as alice, the three gates decided as alice, bob and bob, one plain replay
requested as bob, with the worker run in-process between the writes. The files carry no
machine-specific value: directories are ``"<dir>"``, artifact URIs ``"artifact://<id>"``,
and the database URL is never written.
"""

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from modelforge_lineage.db import Database
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.tools import InProcessToolClient
from modelforge_orchestrator.worker import run_once

from modelforge_api.app import API_PREFIX, create_app
from modelforge_api.identity import IDENTITY_HEADER
from modelforge_api.settings import ApiSettings

__all__ = ["FIXTURE_FILES", "RUN_ID_FILE", "capture"]

FIXTURE_FILES: dict[str, str] = {
    "config.json": "/config",
    "briefs.json": "/briefs",
    "runs.json": "/runs",
    "run.json": "/runs/{run_id}",
    "steps.json": "/runs/{run_id}/steps",
    "llm-calls.json": "/runs/{run_id}/llm-calls",
    "artifacts.json": "/runs/{run_id}/artifacts",
    "document.json": "/runs/{run_id}/document",
    "validation.json": "/runs/{run_id}/validation",
    "findings.json": "/runs/{run_id}/findings",
    "approvals.json": "/runs/{run_id}/approvals",
    "manifest.json": "/runs/{run_id}/manifest",
    "verify.json": "/runs/{run_id}/verify",
    "events.json": "/runs/{run_id}/events",
    "replays.json": "/runs/{run_id}/replays",
    "inventory.json": "/inventory",
    "costs.json": "/costs",
    "settings.json": "/settings",
}
"""Each fixture file and the endpoint (under ``/api/v1``) whose JSON it holds."""

RUN_ID_FILE = "run-id.txt"

_GATES = (("scope", "approve", "alice"), ("model", "accept", "bob"), ("signoff", "approve", "bob"))
_DIR = "<dir>"


def _as(name: str) -> dict[str, str]:
    return {IDENTITY_HEADER: name}


def _json(client: TestClient, method: str, path: str, expected: int, **kwargs: Any) -> Any:
    response = client.request(method, f"{API_PREFIX}{path}", **kwargs)
    if response.status_code != expected:
        raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
    return response.json()


def _normalise(name: str, data: Any) -> Any:
    """Replace the values that name this machine: directories and artifact URIs."""
    if name == "config.json":
        data["directories"] = dict.fromkeys(data["directories"], _DIR)
    elif name == "settings.json":
        config = data["config"]
        config["directories"] = dict.fromkeys(config["directories"], _DIR)
    elif name == "artifacts.json":
        for item in data:
            item["uri"] = f"artifact://{item['id']}"
    return data


def capture(settings: Settings, out: Path) -> list[Path]:
    """Drive one synthetic ``fake`` run and its replay on a temporary database and artifact
    directory, then write every read endpoint's JSON into ``out``; the written paths.

    ``settings.database_url`` only names the server the temporary database is created on.
    The synthetic brief is read from the default briefs directory (``examples/briefs``).
    """
    # testing imports pytest, a development dependency: only this command needs it
    from modelforge_lineage.testing import temporary_database

    with temporary_database(settings.database_url) as url, tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        core = replace(
            settings,
            database_url=url,
            artifact_dir=work / "artifacts",
            profile="fake",
            auto_replay=False,
        )
        api = ApiSettings(core=core, console_dir=work / "console")
        brief = (api.briefs_dir / "synthetic.yaml").read_text()
        db = Database(url)
        store = ArtifactStore(core.artifact_dir)
        try:
            with TestClient(create_app(api)) as client:

                def drive(run_id: str) -> None:
                    run_once(
                        core, db, store, run_id=run_id, tool_client_factory=InProcessToolClient
                    )

                run_id = _json(
                    client, "POST", "/runs", 201, json={"brief": brief}, headers=_as("alice")
                )["id"]
                drive(run_id)
                for gate, decision, approver in _GATES:
                    body = {"gate": gate, "decision": decision}
                    _json(
                        client,
                        "POST",
                        f"/runs/{run_id}/approvals",
                        202,
                        json=body,
                        headers=_as(approver),
                    )
                    drive(run_id)
                replay_id = _json(
                    client, "POST", f"/runs/{run_id}/replay", 202, headers=_as("bob")
                )["id"]
                drive(replay_id)
                responses = {
                    name: _json(client, "GET", path.format(run_id=run_id), 200)
                    for name, path in FIXTURE_FILES.items()
                }
        finally:
            db.close()

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, data in responses.items():
        path = out / name
        text = json.dumps(_normalise(name, data), indent=2, ensure_ascii=False)
        path.write_text(text + "\n")
        written.append(path)
    run_id_path = out / RUN_ID_FILE
    run_id_path.write_text(run_id + "\n")
    written.append(run_id_path)
    return written
