from typing import Any

import pytest
from fastapi.testclient import TestClient
from modelforge_lineage.store import ArtifactStore
from modelforge_orchestrator.config import Settings

from .conftest import POLISH_BRIEF, Drive, Submit

API = "/api/v1"


def _get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(f"{API}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.db
def test_run_list_items_and_filters(
    client: TestClient, auto_replay: None, completed_run: str
) -> None:
    listing = _get(client, "/runs")
    assert listing["limit"] == 50 and listing["offset"] == 0
    assert listing["total"] == 2  # the run and its auto replay
    (item,) = [r for r in listing["runs"] if r["id"] == completed_run]
    assert item["status"] == "completed"
    assert item["model_name"] == "synthetic_pd"
    detail = _get(client, f"/runs/{completed_run}")
    assert item["findings"]["n"] == detail["findings_summary"]["n_findings"]
    assert item["findings"]["max_open_severity"] == detail["findings_summary"]["max_open_severity"]
    (replay,) = [r for r in listing["runs"] if r["id"] != completed_run]
    assert replay["replay_of"] == completed_run
    assert replay["findings"] == {"n": 0, "max_open_severity": None}

    assert _get(client, "/runs", status="queued")["total"] == 1  # the replay
    assert _get(client, "/runs", status="completed")["total"] == 1
    assert [r["id"] for r in _get(client, "/runs", requester="alice")["runs"]] == [completed_run]
    assert _get(client, "/runs", requester="replay")["total"] == 1
    assert _get(client, "/runs", profile="fake")["total"] == 2
    assert _get(client, "/runs", requester="nobody") == {
        "runs": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
    }
    excluded = _get(client, "/runs", replays="exclude")
    assert excluded["total"] == 1 and [r["id"] for r in excluded["runs"]] == [completed_run]
    page = _get(client, "/runs", limit=1, offset=1)
    assert page["total"] == 2 and len(page["runs"]) == 1 and page["offset"] == 1
    for bad in ({"limit": 0}, {"limit": 201}, {"offset": -1}, {"replays": "only"}):
        response = client.get(f"{API}/runs", params=bad)
        assert response.status_code == 422, bad
        assert response.json()["error"]["kind"] == "validation"


@pytest.mark.db
def test_run_detail_shape(client: TestClient, completed_run: str) -> None:
    detail = _get(client, f"/runs/{completed_run}")
    run = detail["run"]
    assert run["id"] == completed_run and run["status"] == "completed"
    assert "manifest" in detail["refs"] and detail["refs"]["manifest"]["id"] == run["manifest_hash"]
    assert detail["counters"]["challenge_rounds"] >= 1
    assert detail["max_remediation_rounds"] >= 0
    assert detail["gate"] is None
    assert [a["gate"] for a in detail["approvals"]] == ["scope", "model", "signoff"]
    calls = _get(client, f"/runs/{completed_run}/llm-calls")
    cost = detail["cost"]
    assert cost == calls["cost"]
    assert cost["n_calls"] == len(calls["calls"]) > 0
    assert set(cost["by_purpose"]) == {"scope", "document", "challenge", "remediate"}
    assert sum(b["n_calls"] for b in cost["by_model"].values()) == cost["n_calls"]
    assert cost["spent_usd"] == run["cost_usd"] and cost["cap_usd"] == run["cost_cap_usd"]
    assert cost["n_schema_retries"] == sum(1 for c in calls["calls"] if not c["schema_valid"])
    assert detail["findings_summary"]["n_findings"] >= 0
    assert detail["responses_summary"] is not None
    assert detail["inventory"]["status"] == "approved"
    assert detail["inventory"]["run_id"] == completed_run
    trace = detail["trace"]
    assert trace["trace_id"] == run["trace_id"]
    assert trace["jaeger_url"] == f"http://localhost:16686/trace/{run['trace_id']}"
    assert trace["phoenix_url"] == "http://localhost:6006"
    assert detail["replays"] == []


@pytest.mark.db
def test_run_detail_before_first_step(client: TestClient, submit: Submit) -> None:
    run_id = submit()
    detail = _get(client, f"/runs/{run_id}")
    assert detail["run"]["status"] == "queued"
    assert detail["refs"] == {}
    assert detail["counters"] is None and detail["max_remediation_rounds"] is None
    assert detail["gate"] is None
    assert detail["approvals"] == []
    assert detail["findings_summary"] is None and detail["responses_summary"] is None
    assert detail["cost"]["n_calls"] == 0 and detail["cost"]["by_purpose"] == {}
    assert detail["inventory"] is None


@pytest.mark.db
def test_run_detail_gate_while_awaiting(client: TestClient, submit: Submit, drive: Drive) -> None:
    run_id = submit()
    drive(run_id)
    detail = _get(client, f"/runs/{run_id}")
    assert detail["run"]["status"] == "awaiting_scope"
    assert detail["gate"]["gate"] == "scope"
    assert isinstance(detail["gate"]["summary"], dict) and detail["gate"]["summary"]
    assert detail["counters"] is not None and detail["refs"]
    for path in ("manifest", "verify"):
        response = client.get(f"{API}/runs/{run_id}/{path}")
        assert response.status_code == 409
        assert response.json()["error"]["kind"] == "not_ready"


@pytest.mark.db
def test_steps_group_calls(client: TestClient, completed_run: str) -> None:
    steps = _get(client, f"/runs/{completed_run}/steps")
    assert len({s["node"] for s in steps}) == 14
    assert [s["started_at"] for s in steps] == sorted(s["started_at"] for s in steps)
    scope = next(s for s in steps if s["node"] == "scope")
    assert len(scope["llm_calls"]) >= 1
    assert all(c["step_id"] == scope["id"] for c in scope["llm_calls"])
    profile = next(s for s in steps if s["node"] == "profile")
    assert len(profile["tool_calls"]) >= 1
    assert all(c["step_id"] == profile["id"] for c in profile["tool_calls"])
    assert all(s["duration_ms"] is not None and s["duration_ms"] >= 0 for s in steps)
    calls = _get(client, f"/runs/{completed_run}/llm-calls")["calls"]
    assert sum(len(s["llm_calls"]) for s in steps) == len(calls)


@pytest.mark.db
def test_artifacts_list_and_content(client: TestClient, completed_run: str, submit: Submit) -> None:
    detail = _get(client, f"/runs/{completed_run}")
    refs = detail["refs"]
    items = {a["id"]: a for a in _get(client, f"/runs/{completed_run}/artifacts")}
    validation = items[refs["validation"]["id"]]
    assert "validation" in validation["ref_names"]
    assert validation["summary"] == refs["validation"]["summary"]
    brief = items[detail["run"]["brief_ref"]]
    assert brief["kind"] == "brief"
    unreferenced = [a for a in items.values() if not a["ref_names"]]
    assert all(a["summary"] is None for a in unreferenced)

    base = f"{API}/runs/{completed_run}/artifacts"
    response = client.get(f"{base}/{validation['id']}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert len(response.json()["tests"]) == 13
    response = client.get(f"{base}/{brief['id']}")
    assert response.headers["content-type"].startswith("application/yaml")
    assert "model_name: synthetic_pd" in response.text
    html = refs["document_html"]["id"]
    response = client.get(f"{base}/{html}")
    assert response.headers["content-type"].startswith("text/html")
    markdown = refs["document_md"]["id"]
    response = client.get(f"{base}/{markdown}")
    assert response.headers["content-type"].startswith("text/markdown")
    scores = refs["champion_scores"]["id"]
    response = client.get(f"{base}/{scores}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.apache.parquet"
    assert response.headers["content-disposition"] == f"attachment; filename={scores}.parquet"
    assert response.content[:4] == b"PAR1"

    other = submit(brief=POLISH_BRIEF)
    other_brief = _get(client, f"/runs/{other}")["run"]["brief_ref"]
    assert other_brief not in items
    for missing in (other_brief, "0" * 64, "not-an-id"):
        response = client.get(f"{base}/{missing}")
        assert response.status_code == 404
        assert response.json()["error"]["kind"] == "not_found"


_HARDENING = {"content-security-policy": "sandbox", "x-content-type-options": "nosniff"}


@pytest.mark.db
def test_artifact_and_document_content_carry_hardening_headers(
    client: TestClient, completed_run: str
) -> None:
    refs = _get(client, f"/runs/{completed_run}")["refs"]
    base = f"{API}/runs/{completed_run}"
    paths = [
        f"{base}/artifacts/{refs[name]['id']}"
        for name in ("validation", "document_html", "document_md", "champion_scores")
    ]
    paths += [f"{base}/document?format=html", f"{base}/document?format=md"]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        for header, value in _HARDENING.items():
            assert response.headers.get(header) == value, (path, header)


@pytest.mark.db
def test_json_artifact_is_served_verbatim(
    client: TestClient, completed_run: str, settings: Settings
) -> None:
    validation = _get(client, f"/runs/{completed_run}")["refs"]["validation"]["id"]
    path = ArtifactStore(settings.artifact_dir).path(validation)
    stored = path.read_bytes()
    response = client.get(f"{API}/runs/{completed_run}/artifacts/{validation}")
    assert response.status_code == 200 and response.content == stored
    # a stored NaN (valid for Python's json, not for strict JSON) is served, not a 500
    path.write_bytes(b'{"auc": NaN}')
    response = client.get(f"{API}/runs/{completed_run}/artifacts/{validation}")
    assert response.status_code == 200 and response.content == b'{"auc": NaN}'
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.db
def test_missing_manifest_is_404(
    client: TestClient, completed_run: str, settings: Settings
) -> None:
    store = ArtifactStore(settings.artifact_dir)
    manifest = _get(client, f"/runs/{completed_run}")["run"]["manifest_hash"]
    store.path(manifest).unlink()
    response = client.get(f"{API}/runs/{completed_run}/manifest")
    assert response.status_code == 404 and response.json()["error"]["kind"] == "not_found"


@pytest.mark.db
def test_missing_replay_report_is_flagged_not_404(
    client: TestClient, auto_replay: None, completed_run: str, drive: Drive, settings: Settings
) -> None:
    (replay,) = _get(client, f"/runs/{completed_run}/replays")
    assert replay["report_missing"] is False  # not sealed yet: no report, none missing
    drive(replay["run"]["id"])
    report = _get(client, f"/runs/{replay['run']['id']}")["refs"]["replay_report"]["id"]
    ArtifactStore(settings.artifact_dir).path(report).unlink()
    (replay,) = _get(client, f"/runs/{completed_run}/replays")
    assert replay["run"]["status"] == "completed"
    assert replay["report"] is None and replay["report_missing"] is True
    assert _get(client, f"/runs/{completed_run}")["replays"] == [replay]


@pytest.mark.db
def test_manifest_verify_events_replays(
    client: TestClient, auto_replay: None, completed_run: str, drive: Drive
) -> None:
    manifest = _get(client, f"/runs/{completed_run}/manifest")
    assert manifest["run_id"] == completed_run and manifest["outcome"] == "completed"
    verify = _get(client, f"/runs/{completed_run}/verify")
    assert verify["run_id"] == completed_run and verify["ok"] is True

    events = _get(client, f"/runs/{completed_run}/events")
    assert events["events"] and events["next_after"] == events["events"][-1]["id"]
    assert all(e["run_id"] == completed_run for e in events["events"])
    after = _get(client, f"/runs/{completed_run}/events", after=events["next_after"])
    assert after == {"events": [], "next_after": events["next_after"]}
    first = _get(client, f"/runs/{completed_run}/events", limit=1)
    assert [e["id"] for e in first["events"]] == [events["events"][0]["id"]]
    assert first["next_after"] == events["events"][0]["id"]
    for bad in ({"limit": 0}, {"limit": 1001}, {"after": -1}):
        assert client.get(f"{API}/runs/{completed_run}/events", params=bad).status_code == 422

    (replay,) = _get(client, f"/runs/{completed_run}/replays")
    assert replay["run"]["replay_of"] == completed_run and replay["report"] is None
    assert _get(client, f"/runs/{completed_run}")["replays"] == [replay]
    drive(replay["run"]["id"])
    (replay,) = _get(client, f"/runs/{completed_run}/replays")
    assert replay["run"]["status"] == "completed"
    assert replay["report"]["original_run_id"] == completed_run
    assert replay["report"]["ok"] is True


@pytest.mark.db
def test_inventory(client: TestClient, completed_run: str) -> None:
    entries = _get(client, "/inventory")
    (entry,) = [e for e in entries if e["run_id"] == completed_run]
    assert entry["name"] == "synthetic_pd" and entry["status"] == "approved"
    assert _get(client, f"/inventory/{entry['model_id']}") == entry
    response = client.get(f"{API}/inventory/model_000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["kind"] == "not_found"


@pytest.mark.db
def test_unknown_run_is_404(client: TestClient) -> None:
    for path in (
        "",
        "/steps",
        "/llm-calls",
        "/artifacts",
        f"/artifacts/{'0' * 64}",
        "/approvals",
        "/manifest",
        "/verify",
        "/events",
        "/replays",
    ):
        response = client.get(f"{API}/runs/run_000000000000{path}")
        assert response.status_code == 404, path
        assert response.json()["error"]["kind"] == "not_found", path


@pytest.mark.db
def test_approvals(client: TestClient, completed_run: str) -> None:
    approvals = _get(client, f"/runs/{completed_run}/approvals")
    assert [(a["gate"], a["decision"], a["approver"]) for a in approvals] == [
        ("scope", "approve", "alice"),
        ("model", "accept", "bob"),
        ("signoff", "approve", "bob"),
    ]
