from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from modelforge_api.app import create_app
from modelforge_api.identity import IDENTITY_HEADER
from modelforge_api.settings import ApiSettings
from modelforge_lineage.db import Database
from modelforge_orchestrator.config import Settings

from .conftest import REPO

SETTINGS = "/api/v1/settings"
COSTS = "/api/v1/costs"
PURPOSES = {"scope", "document", "challenge", "remediate", "judge"}
CAROL = {IDENTITY_HEADER: "carol"}


@pytest.mark.db
def test_settings_read_seeded_and_patch(db: Database, client: TestClient) -> None:
    body = client.get(SETTINGS).json()
    assert body["editable"] == {
        "default_profile": "fake",
        "default_cost_cap_usd": 10.0,
        "auto_replay": False,
    }
    assert body["audit"] == []
    assert body["config"]["profile_default"] == "fake"
    profiles = {profile["name"]: profile for profile in body["profiles"]}
    assert {"fake", "default", "ci-cheap"} <= set(profiles)
    fake = profiles["fake"]
    assert fake["error"] is None
    assert set(fake["routes"]) == PURPOSES
    assert set(fake["routes"]["scope"]) == {"provider", "model", "effort", "structured"}
    assert fake["routes"]["scope"]["provider"] == "fake"
    assert all(
        set(price) == {"input", "output", "cache_read", "cache_write"}
        for profile in profiles.values()
        for price in profile["prices"].values()
    )

    response = client.patch(
        SETTINGS, json={"auto_replay": True, "default_cost_cap_usd": 2.5}, headers=CAROL
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["editable"] == {
        "default_profile": "fake",
        "default_cost_cap_usd": 2.5,
        "auto_replay": True,
    }
    audit = body["audit"]
    assert len(audit) == 2
    assert {row["updated_by"] for row in audit} == {"carol"}
    assert {(row["key"], row["old_value"], row["new_value"]) for row in audit} == {
        ("auto_replay", False, True),
        ("default_cost_cap_usd", 10.0, 2.5),
    }
    assert client.get(SETTINGS).json() == body

    # an unchanged value writes no audit row
    response = client.patch(SETTINGS, json={"auto_replay": True}, headers=CAROL)
    assert response.status_code == 200
    assert len(response.json()["audit"]) == 2

    for patch in (
        {"default_profile": "nope"},
        {"default_profile": "../profiles/fake"},
        {"default_cost_cap_usd": 0},
        {"default_cost_cap_usd": -1.5},
        {"auto_replay": False, "default_cost_cap_usd": 0},
    ):
        response = client.patch(SETTINGS, json=patch, headers=CAROL)
        assert response.status_code == 400, patch
        assert response.json()["error"]["kind"] == "settings", patch
    for patch in (
        {"auto_replay": "yes"},
        {"default_cost_cap_usd": "cheap"},
        {"default_profile": 3},
        {"cost_cap": 1.0},
    ):
        response = client.patch(SETTINGS, json=patch, headers=CAROL)
        assert response.status_code == 422, patch
        assert response.json()["error"]["kind"] == "validation", patch
    # a refused patch writes nothing, not even its valid keys
    after = client.get(SETTINGS).json()
    assert after["editable"]["auto_replay"] is True
    assert len(after["audit"]) == 2

    response = client.patch(SETTINGS, json={"default_profile": "default"}, headers=CAROL)
    assert response.status_code == 200
    assert response.json()["editable"]["default_profile"] == "default"
    assert response.json()["config"]["profile_default"] == "default"
    assert response.json()["audit"][0]["key"] == "default_profile"

    assert client.patch(SETTINGS, json={"auto_replay": False}).status_code == 401


@pytest.mark.db
def test_settings_patch_needs_admin(db: Database, settings: Settings, tmp_path: Path) -> None:
    api_settings = ApiSettings(
        core=settings,
        console_dir=tmp_path / "console",
        briefs_dir=REPO / "examples/briefs",
        users={"carol": frozenset({"admin"}), "alice": frozenset({"approver"})},
    )
    with TestClient(create_app(api_settings)) as client:
        response = client.patch(
            SETTINGS, json={"auto_replay": True}, headers={IDENTITY_HEADER: "alice"}
        )
        assert response.status_code == 403
        assert response.json()["error"] == {
            "kind": "role",
            "message": "alice lacks the admin role",
            "role": "admin",
        }
        assert client.get(SETTINGS).json()["audit"] == []

        response = client.patch(SETTINGS, json={"auto_replay": True}, headers=CAROL)
        assert response.status_code == 200
        assert response.json()["editable"]["auto_replay"] is True
        assert [row["updated_by"] for row in response.json()["audit"]] == ["carol"]


@pytest.mark.db
def test_costs_window(db: Database, completed_run: str, client: TestClient) -> None:
    response = client.get(COSTS)
    assert response.status_code == 200, response.text
    body = response.json()
    since = datetime.fromisoformat(body["since"])
    until = datetime.fromisoformat(body["until"])
    assert 29.9 < (until - since).total_seconds() / 86400 < 30.1
    assert abs((datetime.now(UTC) - until).total_seconds()) < 60

    with db.connection() as conn:
        n_calls = conn.execute(
            "select count(*) as n from llm_calls lc join steps s on s.id = lc.step_id"
            " where s.run_id = %s",
            (completed_run,),
        ).fetchone()
    assert n_calls is not None and n_calls["n"] > 0

    assert body["total_usd"] == 0.0
    assert body["n_runs"] == 1
    assert body["n_calls"] == n_calls["n"]
    assert set(body["tokens"]) == {"input", "output", "cache_read", "cache_write"}

    [run] = body["by_run"]
    assert run["run_id"] == completed_run
    assert run["status"] == "completed"
    assert run["profile"] == "fake"
    assert run["requester"] == "alice"
    assert run["cost_usd"] == 0.0
    assert run["cost_cap_usd"] == 10.0
    assert run["n_calls"] == n_calls["n"]

    [profile] = body["by_profile"]
    assert profile["key"] == "fake"
    assert (profile["n_runs"], profile["n_calls"], profile["cost_usd"]) == (1, n_calls["n"], 0.0)
    assert {"scope", "document", "challenge", "remediate"} <= {
        group["key"] for group in body["by_purpose"]
    }
    assert sum(group["n_calls"] for group in body["by_purpose"]) == n_calls["n"]
    assert sum(group["n_calls"] for group in body["by_model"]) == n_calls["n"]
    assert sum(group["input_tokens"] for group in body["by_model"]) == body["tokens"]["input"]
    assert body["by_day"] == [
        {"day": datetime.now(UTC).date().isoformat(), "cost_usd": 0.0, "n_calls": n_calls["n"]}
    ]

    # a window before the run holds nothing
    empty = client.get(
        COSTS, params={"since": "2020-01-01T00:00:00Z", "until": "2020-02-01T00:00:00Z"}
    ).json()
    assert (empty["n_runs"], empty["n_calls"], empty["by_run"], empty["by_day"]) == (0, 0, [], [])

    response = client.get(
        COSTS, params={"since": "2030-01-01T00:00:00Z", "until": "2020-01-01T00:00:00Z"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "validation"


@pytest.mark.db
def test_briefs_list(client: TestClient) -> None:
    response = client.get("/api/v1/briefs")
    assert response.status_code == 200
    briefs = {brief["name"]: brief for brief in response.json()}
    assert {"polish-pd", "synthetic"} <= set(briefs)
    for name, brief in briefs.items():
        assert brief["text"].startswith("model_name")
        assert Path(brief["path"]).name == f"{name}.yaml"
        assert brief["text"] == Path(brief["path"]).read_text()
