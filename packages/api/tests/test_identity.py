from dataclasses import replace
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from modelforge_api.app import create_app
from modelforge_api.identity import IDENTITY_HEADER, Identity, require
from modelforge_api.settings import ApiSettings, load_api_settings, parse_users

# Throwaway write routes standing in for the real ones (Task 12 moves these tests onto them).
APPROVER_ROUTE = "/api/v1/_test/approver"
REQUESTER_ROUTE = "/api/v1/_test/requester"


def add_test_routes(app: FastAPI) -> None:
    @app.post(APPROVER_ROUTE)
    def approver(who: Annotated[Identity, Depends(require("approver"))]) -> dict[str, str]:
        return {"name": who.name}

    @app.post(REQUESTER_ROUTE)
    def requester(who: Annotated[Identity, Depends(require("requester"))]) -> dict[str, object]:
        return {"name": who.name, "roles": sorted(who.roles)}


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


def test_load_api_settings_honours_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("MODELFORGE_USERS=alice:admin\nMODELFORGE_PROFILE=fake\n")
    loaded = load_api_settings({"MODELFORGE_PROFILE": "default"}, dotenv=dotenv)
    assert loaded.users == {"alice": frozenset({"admin"})}
    assert loaded.core.profile == "default"  # the environment wins over the file


@pytest.mark.db
def test_identity_header_rules(app: FastAPI, client: TestClient) -> None:
    add_test_routes(app)
    for headers in ({}, {IDENTITY_HEADER: ""}, {IDENTITY_HEADER: "  "}):
        response = client.post(APPROVER_ROUTE, headers=headers)
        assert response.status_code == 401, headers
        assert response.json()["error"]["kind"] == "identity"
    for name in ("a" * 65, "alice b", "alice/b", "aliceé"):
        response = client.post(APPROVER_ROUTE, headers={IDENTITY_HEADER.encode(): name.encode()})
        assert response.status_code == 401, name
        body = response.json()
        assert set(body) == {"error"}
        assert body["error"]["kind"] == "identity"
        assert isinstance(body["error"]["message"], str)
    response = client.post(APPROVER_ROUTE, headers={IDENTITY_HEADER: "alice.b@example.org"})
    assert response.status_code == 200
    assert response.json() == {"name": "alice.b@example.org"}
    # open mode: every identity has every role
    response = client.post(REQUESTER_ROUTE, headers={IDENTITY_HEADER: "a" * 64})
    assert response.status_code == 200
    assert response.json()["roles"] == ["admin", "approver", "requester"]


@pytest.mark.db
def test_role_map_enforced(api_settings: ApiSettings) -> None:
    app = create_app(replace(api_settings, users={"alice": frozenset({"requester"})}))
    add_test_routes(app)
    with TestClient(app) as client:
        response = client.post(REQUESTER_ROUTE, headers={IDENTITY_HEADER: "alice"})
        assert response.status_code == 200
        assert response.json() == {"name": "alice", "roles": ["requester"]}

        response = client.post(APPROVER_ROUTE, headers={IDENTITY_HEADER: "alice"})
        assert response.status_code == 403
        assert response.json()["error"]["kind"] == "role"

        response = client.post(REQUESTER_ROUTE, headers={IDENTITY_HEADER: "mallory"})
        assert response.status_code == 403
        assert response.json()["error"]["kind"] == "role"

        # a malformed header is still an identity error under a role map
        response = client.post(REQUESTER_ROUTE)
        assert response.status_code == 401
        assert response.json()["error"]["kind"] == "identity"

        config = client.get("/api/v1/config").json()
        assert config["identity"] == {"mode": "roles", "roles": {"alice": ["requester"]}}
