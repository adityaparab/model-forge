from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from modelforge_api.app import create_app
from modelforge_api.settings import ApiSettings
from modelforge_orchestrator.config import Settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .conftest import Submit


@pytest.mark.db
def test_missing_console_page(client: TestClient) -> None:
    for path in ("/", "/runs/abc"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "make console" in response.text
        assert "make ui-dev" in response.text
    assert client.get("/api/v1/config").json()["console"] == "missing"

    for path in ("/api/v1/nope", "/api/nope", "/api"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.headers["content-type"] == "application/json"
        assert response.json()["error"]["kind"] == "not_found"
    for path in ("/openapi.json", "/docs", "/redoc"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "make console" not in response.text

    wrong_method = client.delete("/api/v1/health")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["kind"] == "method_not_allowed"


@pytest.mark.db
def test_built_console_spa_fallback(settings: Settings, tmp_path: Path) -> None:
    console = tmp_path / "dist"
    (console / "assets").mkdir(parents=True)
    index = b"<!doctype html><title>ModelForge</title><div id=root></div>"
    (console / "index.html").write_bytes(index)
    (console / "assets/app.js").write_text("console.log('modelforge')")
    api_settings = ApiSettings(core=settings, console_dir=console)
    with TestClient(create_app(api_settings)) as client:
        for path in ("/", "/runs/x", "/inventory/model_1?tab=docs"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.content == index
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["content-type"].startswith("text/html")
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert asset.text == "console.log('modelforge')"
        assert "immutable" in asset.headers["cache-control"]
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/v1/nope").json()["error"]["kind"] == "not_found"
        assert client.get("/api/v1/config").json()["console"] == "built"


@pytest.mark.db
def test_dev_redirect(settings: Settings, tmp_path: Path) -> None:
    api_settings = ApiSettings(
        core=settings, console_dir=tmp_path / "dist", console_dev_url="http://localhost:5173"
    )
    with TestClient(create_app(api_settings), follow_redirects=False) as client:
        root = client.get("/")
        assert root.status_code == 307
        assert root.headers["location"] == "http://localhost:5173/"
        deep = client.get("/runs/x?tab=steps")
        assert deep.status_code == 307
        assert deep.headers["location"] == "http://localhost:5173/runs/x?tab=steps"
        assert client.get("/api/v1/nope").status_code == 404
        assert client.get("/api/v1/config").json()["console"] == "dev"


@pytest.mark.db
def test_request_spans(
    spans: InMemorySpanExporter, api_settings: ApiSettings, submit: Submit
) -> None:
    run_id = submit()
    with TestClient(create_app(api_settings)) as c:
        assert c.get(f"/api/v1/runs/{run_id}").status_code == 200
        assert c.get("/api/v1/runs").status_code == 200
    by_route = {
        s.attributes["http.route"]: s
        for s in spans.get_finished_spans()
        if s.attributes and "http.route" in s.attributes
    }
    detail = by_route["/api/v1/runs/{run_id}"]
    assert detail.attributes is not None
    assert detail.attributes["modelforge.run_id"] == run_id
    listing = by_route["/api/v1/runs"]
    assert listing.attributes is not None
    assert "modelforge.run_id" not in listing.attributes
