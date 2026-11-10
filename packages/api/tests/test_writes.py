import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from langgraph.runtime import Runtime
from modelforge_lineage import settings as server_settings
from modelforge_lineage.db import Database
from modelforge_lineage.findings import insert_findings
from modelforge_lineage.queue import claim
from modelforge_lineage.runs import get_run
from modelforge_orchestrator import graph
from modelforge_orchestrator.briefs import brief_hash
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.schemas import RunState

from .conftest import REPO, SYNTHETIC_BRIEF, Drive, submit_via_api

API = "/api/v1"
YAML = {"Content-Type": "application/yaml"}


@pytest.fixture
def settings(settings: Settings, tmp_path: Path) -> Settings:
    """The conftest settings over a copy of the repo's profiles plus ``fake-priced``: the fake
    routes at 4/20 USD per Mtok, so the gateway's cost-cap projection is not zero."""
    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)
    fake = yaml.safe_load((profiles / "fake.yaml").read_text())
    priced = {
        "name": "fake-priced",
        "routes": fake["routes"],
        "prices": {
            "scripted-v1": {"input": 4.0, "output": 20.0, "cache_read": 0.0, "cache_write": 0.0}
        },
    }
    (profiles / "fake-priced.yaml").write_text(yaml.safe_dump(priced, sort_keys=False))
    return replace(settings, gateway=replace(settings.gateway, profiles_dir=profiles))


def _as(name: str) -> dict[str, str]:
    return {"X-ModelForge-User": name}


def _error(response: Any, status: int, kind: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error: dict[str, Any] = response.json()["error"]
    assert error["kind"] == kind, error
    return error


def _approve(
    client: TestClient, run_id: str, user: str, gate: str, decision: str, **fields: Any
) -> Any:
    return client.post(
        f"{API}/runs/{run_id}/approvals",
        json={"gate": gate, "decision": decision, **fields},
        headers=_as(user),
    )


def _to_signoff(client: TestClient, drive: Drive) -> str:
    """A synthetic run submitted by alice and approved through scope and model via the API."""
    run_id = submit_via_api(client)["id"]
    assert (run := drive(run_id)) is not None and run.status == "awaiting_scope"
    assert _approve(client, run_id, "alice", "scope", "approve").status_code == 202
    assert (run := drive(run_id)) is not None and run.status == "awaiting_model"
    assert _approve(client, run_id, "bob", "model", "accept").status_code == 202
    assert (run := drive(run_id)) is not None and run.status == "awaiting_signoff"
    return run_id


def _queued(database: Database, run_id: str) -> bool:
    with database.transaction() as conn:
        return claim(conn, "probe", run_id=run_id) is not None


@pytest.mark.db
def test_submit_json_and_yaml(db: Database, client: TestClient, drive: Drive) -> None:
    text = SYNTHETIC_BRIEF.read_text()
    run = submit_via_api(client)
    assert run["status"] == "queued" and run["requester"] == "alice"
    assert run["profile"] == "fake" and run["cost_cap_usd"] == 10.0

    data = SYNTHETIC_BRIEF.read_bytes()
    response = client.post(
        f"{API}/runs",
        content=data,
        params={"profile": "fake", "cost_cap_usd": 3},
        headers={**_as("carol"), "Content-Type": "text/yaml; charset=utf-8"},
    )
    assert response.status_code == 201, response.text
    yaml_run = response.json()
    assert yaml_run["requester"] == "carol" and yaml_run["cost_cap_usd"] == 3.0
    assert yaml_run["brief_hash"] == brief_hash(data)  # the bytes passed through unchanged
    for media in ("application/yaml", "application/x-yaml"):
        response = client.post(
            f"{API}/runs", content=data, headers={**_as("carol"), "Content-Type": media}
        )
        assert response.status_code == 201, (media, response.text)
    assert (driven := drive(yaml_run["id"])) is not None and driven.status == "awaiting_scope"

    unknown = text.replace("synthetic_credit", "no_such_adapter")
    error = _error(
        client.post(f"{API}/runs", json={"brief": unknown}, headers=_as("alice")), 400, "brief"
    )
    assert "no_such_adapter" in error["message"]
    _error(client.post(f"{API}/runs", json={"brief": "a: [b"}, headers=_as("alice")), 400, "brief")
    _error(client.post(f"{API}/runs", content=b"", headers={**_as("alice"), **YAML}), 400, "brief")
    error = _error(
        client.post(f"{API}/runs", json={"brief": text, "profile": "nope"}, headers=_as("alice")),
        400,
        "profile",
    )
    assert "nope" in error["message"]

    # Review Focus 2: too large or not UTF-8 is a brief error, never a 500
    big = b"# padding\n" * (2 * 1024 * 1024 // 10)
    error = _error(
        client.post(f"{API}/runs", content=big, headers={**_as("alice"), **YAML}), 400, "brief"
    )
    assert error["message"] == "brief larger than 1 MiB"
    big_json = {"brief": big.decode()}
    _error(client.post(f"{API}/runs", json=big_json, headers=_as("alice")), 400, "brief")
    _error(
        client.post(f"{API}/runs", content=b"\xff\xfe", headers={**_as("alice"), **YAML}),
        400,
        "brief",
    )

    for body in ({"brief": text, "cost_cap_usd": 0}, {"brief": text, "approver": "x"}, {}):
        _error(client.post(f"{API}/runs", json=body, headers=_as("alice")), 422, "validation")
    for raw in (b"{", b"\xff\xfe"):  # bad JSON, and bytes the error detail must not echo
        _error(
            client.post(
                f"{API}/runs",
                content=raw,
                headers={**_as("alice"), "Content-Type": "application/json"},
            ),
            422,
            "validation",
        )
    _error(
        client.post(
            f"{API}/runs",
            content=data,
            params={"cost_cap_usd": -1},
            headers={**_as("alice"), **YAML},
        ),
        422,
        "validation",
    )
    assert client.get(f"{API}/runs").json()["total"] == 4  # no refused submit wrote a run


@pytest.mark.db
def test_submit_defaults_from_settings(db: Database, client: TestClient) -> None:
    with db.transaction() as conn:
        server_settings.set_value(conn, "default_cost_cap_usd", 4.5, updated_by="test")
    run = submit_via_api(client)
    assert run["cost_cap_usd"] == 4.5 and run["profile"] == "fake"
    assert submit_via_api(client, cost_cap_usd=7)["cost_cap_usd"] == 7.0


@pytest.mark.db
def test_gates_through_the_api(db: Database, client: TestClient, drive: Drive) -> None:
    run_id = submit_via_api(client)["id"]
    assert (run := drive(run_id)) is not None and run.status == "awaiting_scope"

    _error(_approve(client, run_id, "bob", "model", "accept"), 409, "approval")  # wrong gate
    _error(_approve(client, run_id, "bob", "scope", "accept"), 409, "approval")  # invalid
    _error(_approve(client, run_id, "bob", "scope", "approve", plan={"nope": 1}), 422, "validation")
    _error(_approve(client, run_id, "bob", "scope", "approve", approver="x"), 422, "validation")
    _error(_approve(client, "run_missing", "bob", "scope", "approve"), 404, "not_found")

    response = _approve(client, run_id, "alice", "scope", "approve", comment="looks right")
    assert response.status_code == 202, response.text
    record = response.json()
    assert (record["gate"], record["decision"], record["approver"]) == ("scope", "approve", "alice")
    assert record["comment"] == "looks right"
    assert (run := drive(run_id)) is not None and run.status == "awaiting_model"

    error = _error(_approve(client, run_id, "alice", "model", "accept"), 409, "approval")
    assert "alice" in error["message"]  # four eyes: the requester cannot decide
    assert _approve(client, run_id, "bob", "model", "accept").status_code == 202
    assert (run := drive(run_id)) is not None and run.status == "awaiting_signoff"
    assert _approve(client, run_id, "bob", "signoff", "approve").status_code == 202
    assert (run := drive(run_id)) is not None and run.status == "completed"
    approvals = client.get(f"{API}/runs/{run_id}/approvals").json()
    assert [(a["gate"], a["approver"]) for a in approvals] == [
        ("scope", "alice"),
        ("model", "bob"),
        ("signoff", "bob"),
    ]


@pytest.mark.db
def test_blocking_findings_refusal(db: Database, client: TestClient, drive: Drive) -> None:
    run_id = _to_signoff(client, drive)
    row = {
        "id": f"{run_id}/F-99",
        "round": 1,
        "severity": "high",
        "category": "validation",
        "title": "Planted blocking finding",
        "description": "An open high finding planted by the test.",
        "evidence": [],
        "required_action": "Resolve it.",
        "status": "open",
        "response": None,
        "response_evidence": None,
        "source": "review",
        "check_key": None,
        "verdict": None,
        "closed_round": None,
    }
    with db.transaction() as conn:
        insert_findings(conn, run_id, [row])

    error = _error(_approve(client, run_id, "bob", "signoff", "approve"), 409, "approval")
    assert "F-99" in error["message"]
    assert error["findings"] == [
        {"id": "F-99", "severity": "high", "title": "Planted blocking finding"}
    ]
    assert client.get(f"{API}/runs/{run_id}/approvals").json()[-1]["gate"] == "model"

    response = _approve(
        client, run_id, "bob", "signoff", "approve_with_conditions", conditions=["Close F-99."]
    )
    assert response.status_code == 202, response.text
    assert response.json()["conditions"] == ["Close F-99."]


@pytest.mark.db
def test_resume_from_cost_cap_park(db: Database, client: TestClient, drive: Drive) -> None:
    run_id = submit_via_api(client, profile="fake-priced", cost_cap_usd=0.000001)["id"]
    _error(client.post(f"{API}/runs/{run_id}/resume", headers=_as("bob")), 409, "state")  # queued
    run = drive(run_id)
    assert run is not None and run.status == "needs_attention"
    assert run.error is not None and run.error["kind"] == "llm"
    assert run.error["error"]["kind"] == "cost_cap"

    response = client.post(
        f"{API}/runs/{run_id}/resume", json={"cost_cap_usd": 5}, headers=_as("bob")
    )
    assert response.status_code == 202, response.text
    resumed = response.json()
    assert resumed["cost_cap_usd"] == 5.0 and resumed["error"] is None
    # a second resume before the worker runs: refused, and its cap is rolled back
    second = client.post(
        f"{API}/runs/{run_id}/resume", json={"cost_cap_usd": 7}, headers=_as("bob")
    )
    assert _error(second, 409, "state")["message"] == f"run {run_id} is already queued"
    assert client.get(f"{API}/runs/{run_id}").json()["run"]["cost_cap_usd"] == 5.0
    run = drive(run_id)
    assert run is not None and run.status == "awaiting_scope"
    _error(client.post(f"{API}/runs/{run_id}/resume", headers=_as("bob")), 409, "state")
    _error(
        client.post(f"{API}/runs/{run_id}/resume", json={"cost_cap_usd": 0}, headers=_as("bob")),
        422,
        "validation",
    )


@pytest.mark.db
def test_pause_and_resume(
    db: Database, client: TestClient, drive: Drive, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = submit_via_api(client)["id"]
    error = _error(client.post(f"{API}/runs/{run_id}/pause", headers=_as("bob")), 409, "state")
    assert run_id in error["message"]
    _error(client.post(f"{API}/runs/run_missing/pause", headers=_as("bob")), 404, "not_found")

    real_intake = graph.NODES["intake"]
    answers: list[Any] = []

    def intake_then_pause(state: RunState, runtime: Runtime[Deps]) -> Any:
        update = real_intake(state, runtime)
        answers.append(client.post(f"{API}/runs/{run_id}/abort", headers=_as("bob")))
        answers.append(client.post(f"{API}/runs/{run_id}/pause", headers=_as("bob")))
        return update

    monkeypatch.setitem(graph.NODES, "intake", intake_then_pause)
    run = drive(run_id)
    monkeypatch.setitem(graph.NODES, "intake", real_intake)
    abort, pause = answers
    assert "pause it first" in _error(abort, 409, "state")["message"]
    assert pause.status_code == 202, pause.text
    assert pause.json()["status"] == "running" and pause.json()["pause_requested"] is True
    assert run is not None and run.status == "paused"

    response = client.post(f"{API}/runs/{run_id}/resume", headers=_as("bob"))
    assert response.status_code == 202, response.text
    assert (run := drive(run_id)) is not None and run.status == "awaiting_scope"
    _error(client.post(f"{API}/runs/{run_id}/pause", headers=_as("bob")), 409, "state")


@pytest.mark.db
def test_abort_rules(db: Database, client: TestClient, drive: Drive, completed_run: str) -> None:
    run_id = submit_via_api(client)["id"]
    response = client.post(f"{API}/runs/{run_id}/abort", headers=_as("bob"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "aborted"
    assert not _queued(db, run_id)  # the queue row was released
    assert drive(run_id) is None

    error = _error(
        client.post(f"{API}/runs/{completed_run}/abort", headers=_as("bob")), 409, "state"
    )
    assert "completed" in error["message"]
    _error(client.post(f"{API}/runs/run_missing/abort", headers=_as("bob")), 404, "not_found")

    waiting = submit_via_api(client)["id"]
    assert (run := drive(waiting)) is not None and run.status == "awaiting_scope"
    response = client.post(f"{API}/runs/{waiting}/abort", headers=_as("bob"))
    assert response.status_code == 200 and response.json()["status"] == "aborted"
    with db.connection() as conn:
        assert get_run(conn, waiting).status == "aborted"
    error = _error(client.post(f"{API}/runs/{waiting}/abort", headers=_as("bob")), 409, "state")
    assert "aborted" in error["message"]

    # abort drops a pending resume: a parked run resumed, then aborted before the worker runs
    parked = submit_via_api(client, profile="fake-priced", cost_cap_usd=0.000001)["id"]
    assert (run := drive(parked)) is not None and run.status == "needs_attention"
    assert client.post(f"{API}/runs/{parked}/resume", headers=_as("bob")).status_code == 202
    response = client.post(f"{API}/runs/{parked}/abort", headers=_as("bob"))
    assert response.status_code == 200 and response.json()["status"] == "aborted"
    assert not _queued(db, parked)
    assert drive(parked) is None


@pytest.mark.db
def test_replay_endpoint(completed_run: str, client: TestClient, drive: Drive) -> None:
    response = client.post(
        f"{API}/runs/{completed_run}/replay", json={"rerun_llm": True}, headers=_as("bob")
    )
    assert response.status_code == 202, response.text
    replay = response.json()
    assert replay["replay_of"] == completed_run and replay["rerun_llm"] is True
    assert replay["status"] == "queued" and replay["requester"] == "replay"
    assert (run := drive(replay["id"])) is not None and run.status == "completed"
    (item,) = client.get(f"{API}/runs/{completed_run}/replays").json()
    assert item["run"]["id"] == replay["id"]
    assert item["report"] is not None and item["report"]["regenerated"]

    plain = client.post(f"{API}/runs/{completed_run}/replay", headers=_as("bob"))
    assert plain.status_code == 202 and plain.json()["rerun_llm"] is False
    error = _error(
        client.post(f"{API}/runs/{plain.json()['id']}/replay", headers=_as("bob")), 409, "state"
    )
    assert "queued" in error["message"]
    _error(client.post(f"{API}/runs/run_missing/replay", headers=_as("bob")), 404, "not_found")
