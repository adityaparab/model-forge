import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import modelforge_api
import modelforge_core
import modelforge_orchestrator
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from modelforge_api import app as app_module
from modelforge_api import errors
from modelforge_api.app import create_app
from modelforge_api.errors import ApiError
from modelforge_api.identity import IDENTITY_HEADER
from modelforge_api.settings import ApiSettings
from modelforge_gateway.settings import GatewaySettings
from modelforge_lineage import settings as server_settings
from modelforge_lineage.db import Database
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from .conftest import REPO

GATES = [
    {
        "name": "scope",
        "status": "awaiting_scope",
        "decisions": ["approve", "edit", "reject"],
    },
    {
        "name": "model",
        "status": "awaiting_model",
        "decisions": ["accept", "adjust", "reject"],
    },
    {
        "name": "signoff",
        "status": "awaiting_signoff",
        "decisions": ["approve", "approve_with_conditions", "reject"],
    },
]


def error_app() -> FastAPI:
    """A bare app (no lifespan, no database) with the error handlers and three failing routes."""
    app = FastAPI()
    errors.install(app)

    class Body(BaseModel):
        n: int

    @app.post("/body")
    def body(payload: Body) -> dict[str, int]:
        return {"n": payload.n}

    @app.get("/gone")
    def gone() -> None:
        raise ApiError(404, "not_found", "run not found: run_x", run_id="run_x")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret detail that must not leak")

    return app


def test_error_shape_for_api_errors_validation_and_crashes() -> None:
    with TestClient(error_app(), raise_server_exceptions=False) as client:
        response = client.get("/gone")
        assert response.status_code == 404
        assert response.json() == {
            "error": {"kind": "not_found", "message": "run not found: run_x", "run_id": "run_x"}
        }

        response = client.post("/body", json={"n": "x"})
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["kind"] == "validation"
        assert isinstance(error["message"], str)
        assert error["detail"][0]["loc"] == ["body", "n"]

        response = client.get("/boom")
        assert response.status_code == 500
        assert response.json() == {
            "error": {"kind": "internal", "message": "internal error", "type": "RuntimeError"}
        }


def test_api_error_keeps_its_fields() -> None:
    exc = ApiError(409, "state", "run is running; pause first", status="running")
    assert (exc.status_code, exc.kind, exc.message) == (409, "state", "run is running; pause first")
    assert exc.extra == {"status": "running"}
    assert str(exc) == "run is running; pause first"


@pytest.mark.db
def test_error_shape_for_validation(app: FastAPI, client: TestClient) -> None:
    class Body(BaseModel):
        n: int

    @app.post("/api/v1/_test/body")
    def body(payload: Body) -> dict[str, int]:
        return {"n": payload.n}

    response = client.post("/api/v1/_test/body", json={"n": "x"}, headers={IDENTITY_HEADER: "a"})
    assert response.status_code == 422
    body_json = response.json()
    assert set(body_json) == {"error"}
    assert body_json["error"]["kind"] == "validation"
    assert body_json["error"]["detail"][0]["loc"] == ["body", "n"]


@pytest.mark.db
def test_health_and_config(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "version": modelforge_api.__version__,
    }

    response = client.get("/api/v1/config")
    assert response.status_code == 200
    assert "modelforge:modelforge@" not in response.text
    config = response.json()
    assert config["profile_default"] == "fake"
    assert config["console"] == "missing"
    assert config["identity"] == {"mode": "open", "roles": {}}
    assert config["trace_urls"] == {
        "jaeger": "http://localhost:16686",
        "phoenix": "http://localhost:6006",
    }
    assert config["tracing"] == {"endpoints": [], "content": False}
    assert config["versions"] == {
        "core": modelforge_core.__version__,
        "orchestrator": modelforge_orchestrator.__version__,
        "api": modelforge_api.__version__,
    }
    assert config["nodes"][0] == "intake"
    assert config["nodes"][-1] == "seal"
    assert "gate_model" in config["nodes"]
    assert config["gates"] == GATES
    directories = config["directories"]
    assert set(directories) == {
        "artifacts",
        "data",
        "templates",
        "knowledge",
        "prompts",
        "profiles",
        "briefs",
        "console",
    }
    assert directories["prompts"] == str(REPO / "prompts")


@pytest.mark.db
def test_config_reads_the_server_side_default_profile(client: TestClient, app: FastAPI) -> None:
    db: Database = app.state.db
    with db.transaction() as conn:
        server_settings.set_value(conn, "default_profile", "default", updated_by="admin")
    try:
        assert client.get("/api/v1/config").json()["profile_default"] == "default"
    finally:
        with db.transaction() as conn:
            server_settings.set_value(conn, "default_profile", "fake", updated_by="admin")


@pytest.mark.db
def test_config_redacts_credentials_and_reports_console_modes(
    api_settings: ApiSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = replace(
        api_settings.core,
        gateway=GatewaySettings(
            prompts_dir=REPO / "prompts",
            profiles_dir=REPO / "profiles",
            otlp_endpoints=("https://user:hunter2@collector.example/v1/traces?api_key=k3y",),
            trace_content=True,
        ),
    )
    (api_settings.console_dir / "index.html").write_text("<!doctype html>")
    built = replace(
        api_settings,
        core=core,
        jaeger_url=None,
        phoenix_url="https://viewer:pw@phoenix.example/?token=t0k",
    )
    # no exporter dials the made-up collector: the endpoint is only reported
    monkeypatch.setattr(app_module, "configure_tracing", lambda *args, **kwargs: False)
    with TestClient(create_app(built)) as client:
        response = client.get("/api/v1/config")
    assert response.status_code == 200
    for secret in ("hunter2", "k3y", "pw@", "t0k", "user:", "modelforge:modelforge@"):
        assert secret not in response.text
    config = response.json()
    assert config["console"] == "built"
    assert config["tracing"] == {
        "endpoints": ["https://collector.example/v1/traces"],
        "content": True,
    }
    assert config["trace_urls"] == {"jaeger": None, "phoenix": "https://phoenix.example/"}

    dev = replace(api_settings, console_dev_url="http://localhost:5173")
    with TestClient(create_app(dev)) as client:
        assert client.get("/api/v1/config").json()["console"] == "dev"


class _BrokenDatabase:
    @contextmanager
    def connection(self) -> Iterator[Any]:
        raise OSError("connection refused")
        yield


@pytest.mark.db
def test_health_is_503_when_the_database_fails(app: FastAPI, client: TestClient) -> None:
    real = app.state.db
    app.state.db = _BrokenDatabase()
    try:
        response = client.get("/api/v1/health")
    finally:
        app.state.db = real
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "database": "OSError: connection refused",
        "version": modelforge_api.__version__,
    }


@pytest.mark.db
def test_request_spans_exclude_health(app: FastAPI, spans: InMemorySpanExporter) -> None:
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/config").status_code == 200
        names = [s.name for s in spans.get_finished_spans()]
    assert any(name.startswith("GET /api/v1/config") for name in names), names
    assert not any("/api/v1/health" in name for name in names), names


def test_openapi_command_needs_no_database() -> None:
    result = subprocess.run(
        ["uv", "run", "modelforge-api", "openapi"],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={**os.environ, "MODELFORGE_DATABASE_URL": "postgresql://nobody@127.0.0.1:1/none"},
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert "/api/v1/health" in document["paths"]
    assert "/api/v1/config" in document["paths"]
    assert "HealthResponse" in document["components"]["schemas"]
