from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from modelforge_api.app import create_app
from modelforge_api.identity import IDENTITY_HEADER
from modelforge_api.settings import ApiSettings, load_api_settings, parse_users

SUBMIT = "/api/v1/runs"
PAUSE = "/api/v1/runs/run_missing/pause"
EMPTY_YAML = {"Content-Type": "application/yaml"}


def test_parse_users() -> None:
    assert parse_users("alice:requester bob:approver,requester") == {
        "alice": frozenset({"requester"}),
        "bob": frozenset({"approver", "requester"}),
    }
    assert parse_users("  carol:admin\n dave:requester,approver,admin ") == {
        "carol": frozenset({"admin"}),
        "dave": frozenset({"requester", "approver", "admin"}),
    }
    assert parse_users("") == {}
    for bad in ("bob", "x:owner", "alice:", ":requester", "alice:requester,", "a b:requester"):
        with pytest.raises(ValueError):
            parse_users(bad)


def test_parse_users_rejects_a_duplicate_name() -> None:
    with pytest.raises(ValueError, match="alice"):
        parse_users("alice:requester bob:approver alice:admin")


def test_load_api_settings_reads_the_variables() -> None:
    defaults = load_api_settings({}, dotenv=None)
    assert (defaults.host, defaults.port) == ("0.0.0.0", 8000)
    assert defaults.console_dir == Path("console/dist")
    assert defaults.console_dev_url is None
    assert defaults.briefs_dir == Path("examples/briefs")
    assert defaults.users is None
    assert defaults.jaeger_url == "http://localhost:16686"
    assert defaults.phoenix_url == "http://localhost:6006"
    assert defaults.core.profile == "default"

    loaded = load_api_settings(
        {
            "MODELFORGE_API_HOST": "127.0.0.1",
            "MODELFORGE_API_PORT": "9001",
            "MODELFORGE_CONSOLE_DIR": "/srv/console",
            "MODELFORGE_CONSOLE_DEV_URL": "http://localhost:5173",
            "MODELFORGE_BRIEFS_DIR": "/srv/briefs",
            "MODELFORGE_USERS": "alice:requester bob:approver",
            "MODELFORGE_JAEGER_URL": "",
            "MODELFORGE_PHOENIX_URL": "http://phoenix:6006",
            "MODELFORGE_PROFILE": "fake",
        },
        dotenv=None,
    )
    assert (loaded.host, loaded.port) == ("127.0.0.1", 9001)
    assert loaded.console_dir == Path("/srv/console")
    assert loaded.console_dev_url == "http://localhost:5173"
    assert loaded.briefs_dir == Path("/srv/briefs")
    assert loaded.users == {"alice": frozenset({"requester"}), "bob": frozenset({"approver"})}
    assert loaded.jaeger_url is None
    assert loaded.phoenix_url == "http://phoenix:6006"
    assert loaded.core.profile == "fake"

    assert (
        load_api_settings({"MODELFORGE_CONSOLE_DEV_URL": ""}, dotenv=None).console_dev_url is None
    )
    with pytest.raises(ValueError):
        load_api_settings({"MODELFORGE_USERS": "alice:root"}, dotenv=None)


def test_empty_users_variable_means_open_mode() -> None:
    assert load_api_settings({"MODELFORGE_USERS": ""}, dotenv=None).users is None


def test_load_api_settings_honours_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("MODELFORGE_USERS=alice:admin\nMODELFORGE_PROFILE=fake\n")
    loaded = load_api_settings({"MODELFORGE_PROFILE": "default"}, dotenv=dotenv)
    assert loaded.users == {"alice": frozenset({"admin"})}
    assert loaded.core.profile == "default"  # the environment wins over the file


@pytest.mark.db
def test_identity_header_rules(client: TestClient) -> None:
    for route in (SUBMIT, PAUSE):
        for headers in ({}, {IDENTITY_HEADER: ""}, {IDENTITY_HEADER: "  "}):
            response = client.post(route, headers=headers)
            assert response.status_code == 401, (route, headers)
            assert response.json()["error"]["kind"] == "identity"
    for name in ("a" * 65, "alice b", "alice/b", "aliceé"):
        response = client.post(SUBMIT, headers={IDENTITY_HEADER.encode(): name.encode()})
        assert response.status_code == 401, name
        body = response.json()
        assert set(body) == {"error"}
        assert body["error"]["kind"] == "identity"
        assert isinstance(body["error"]["message"], str)
    # accepted: the request reaches the brief check (an empty brief) and the run lookup
    for name in ("alice.b@example.org", "a" * 64):
        response = client.post(SUBMIT, headers={IDENTITY_HEADER: name, **EMPTY_YAML})
        assert response.status_code == 400, name
        assert response.json()["error"]["kind"] == "brief"
    response = client.post(PAUSE, headers={IDENTITY_HEADER: "alice.b@example.org"})
    assert response.status_code == 404
    assert response.json()["error"]["kind"] == "not_found"


@pytest.mark.db
def test_role_map_enforced(api_settings: ApiSettings) -> None:
    app = create_app(replace(api_settings, users={"alice": frozenset({"requester"})}))
    with TestClient(app) as client:
        # alice may submit: the request reaches the brief check, not the role check
        response = client.post(SUBMIT, headers={IDENTITY_HEADER: "alice", **EMPTY_YAML})
        assert response.status_code == 400
        assert response.json()["error"]["kind"] == "brief"

        # the role is checked before the run lookup: an unknown run is still a 403
        for route in ("pause", "resume", "abort", "replay", "approvals"):
            response = client.post(
                f"/api/v1/runs/run_missing/{route}", headers={IDENTITY_HEADER: "alice"}
            )
            assert response.status_code == 403, route
            error = response.json()["error"]
            assert error["kind"] == "role" and error["role"] == "approver"

        response = client.post(SUBMIT, headers={IDENTITY_HEADER: "mallory", **EMPTY_YAML})
        assert response.status_code == 403
        assert response.json()["error"] == {
            "kind": "role",
            "message": "mallory lacks the requester role",
            "role": "requester",
        }

        # a missing header is still an identity error under a role map
        response = client.post(SUBMIT)
        assert response.status_code == 401
        assert response.json()["error"]["kind"] == "identity"

        config = client.get("/api/v1/config").json()
        assert config["identity"] == {"mode": "roles", "roles": {"alice": ["requester"]}}
