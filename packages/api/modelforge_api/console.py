"""Serving the console (spec 3.6): the built single-page app, the Vite dev server or a page
saying how to build it.

Everything outside the API is the console: ``<console_dir>/assets`` is served as static
files with long cache headers (Vite hashes the names) and every other ``GET`` answers
``index.html`` with ``Cache-Control: no-cache`` so the client-side router takes the path.
With ``console_dev_url`` set those paths redirect (307) to the dev server instead, and
without an ``index.html`` they answer the missing-console page. ``api/...``,
``openapi.json``, ``docs`` and ``redoc`` never reach the console: they fall through to
FastAPI's routing and its 404.
"""

import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.requests import Request
from starlette.routing import Match, Route
from starlette.staticfiles import PathLike, StaticFiles
from starlette.types import Scope

from modelforge_api.settings import ApiSettings

__all__ = ["MISSING_PAGE", "console_mode", "install"]

ConsoleMode = Literal["dev", "built", "missing"]

_API_PATHS = re.compile(r"/(?:api|openapi\.json|docs|redoc)(?:/.*)?")

MISSING_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ModelForge console not built</title>
<style>
  body { font-family: system-ui, sans-serif; background: #0f1115; color: #d7dae0;
         max-width: 40rem; margin: 4rem auto; padding: 0 1rem; line-height: 1.5; }
  code { background: #1b1f27; padding: 0.1rem 0.35rem; border-radius: 4px; }
  a { color: #7aa2f7; }
</style>
</head>
<body>
<h1>The console is not built</h1>
<p>The API is running; the console has no <code>index.html</code> to serve yet.</p>
<ul>
  <li><code>make console</code> builds it once into the console directory.</li>
  <li><code>make ui-dev</code> runs the Vite dev server with hot reload against this API.</li>
</ul>
<p>The API itself is at <a href="/docs">/docs</a>.</p>
</body>
</html>
"""


def console_mode(settings: ApiSettings) -> ConsoleMode:
    """``dev`` with a dev server URL, ``built`` with an ``index.html``, else ``missing``."""
    if settings.console_dev_url is not None:
        return "dev"
    if (settings.console_dir / "index.html").is_file():
        return "built"
    return "missing"


class _Assets(StaticFiles):
    """Static files with a year-long immutable cache: Vite puts a hash in every name."""

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class _ConsoleRoute(Route):
    """The catch-all that leaves the API's paths unmatched, so they 404 through the API."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] == "http" and _API_PATHS.fullmatch(scope["path"]):
            return Match.NONE, {}
        return super().matches(scope)


def install(app: FastAPI, settings: ApiSettings) -> None:
    """Mount the assets and the catch-all; call after every API router is included."""
    assets = settings.console_dir / "assets"
    if settings.console_dev_url is None and assets.is_dir():
        app.mount("/assets", _Assets(directory=assets), name="console-assets")
    index: Path = settings.console_dir / "index.html"

    async def console(request: Request) -> Response:
        mode = console_mode(settings)
        if mode == "dev":
            assert settings.console_dev_url is not None
            target = f"{settings.console_dev_url.rstrip('/')}{request.url.path}"
            if request.url.query:
                target = f"{target}?{request.url.query}"
            return RedirectResponse(target, status_code=307)
        if mode == "built":
            return FileResponse(
                index, media_type="text/html", headers={"Cache-Control": "no-cache"}
            )
        return HTMLResponse(MISSING_PAGE)

    app.router.routes.append(
        _ConsoleRoute("/{path:path}", console, methods=["GET", "HEAD"], include_in_schema=False)
    )
