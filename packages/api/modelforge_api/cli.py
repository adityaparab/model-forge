"""The ``modelforge-api`` command: ``serve`` (the default), ``openapi`` and ``fixtures``."""

import json
import logging
from functools import cache
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from fastapi import FastAPI
from modelforge_lineage.observability import configure_logging

from modelforge_api import fixtures as fixture_capture
from modelforge_api.app import create_app
from modelforge_api.settings import load_api_settings

__all__ = ["app"]

app = typer.Typer(add_completion=False, help="ModelForge API.")


@cache
def _application() -> FastAPI:
    settings = load_api_settings()
    configure_logging(settings.core.log_level)
    return create_app(settings)


def __getattr__(name: str) -> FastAPI:
    """``application``: the app uvicorn serves, built on first access (so importing this
    module for ``openapi`` reads no settings and builds nothing)."""
    if name == "application":
        return _application()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Serve the API when no command is given."""
    if ctx.invoked_subcommand is None:
        serve(host=None, port=None, reload=False)


@app.command()
def serve(
    host: Annotated[
        str | None, typer.Option(help="Bind address (default MODELFORGE_API_HOST or 0.0.0.0).")
    ] = None,
    port: Annotated[
        int | None, typer.Option(help="Port (default MODELFORGE_API_PORT or 8000).")
    ] = None,
    reload: Annotated[bool, typer.Option(help="Reload on code changes (development).")] = False,
) -> None:
    """Serve the API with uvicorn."""
    settings = load_api_settings()
    uvicorn.run(
        "modelforge_api.cli:application",
        factory=False,
        host=settings.host if host is None else host,
        port=settings.port if port is None else port,
        reload=reload,
    )


@app.command()
def openapi() -> None:
    """Print the OpenAPI document as JSON; needs no database."""
    document = create_app(load_api_settings()).openapi()
    typer.echo(json.dumps(document, indent=2, sort_keys=True))


@app.command()
def fixtures(
    out: Annotated[
        Path, typer.Option(help="Directory to write into (the console's src/fixtures).")
    ],
) -> None:
    """Drive one synthetic run on a temporary database and write the read endpoints' JSON."""
    settings = load_api_settings()
    configure_logging(settings.core.log_level)
    for client_logger in ("httpx", "httpx2"):  # the test client logs every in-process request
        logging.getLogger(client_logger).setLevel(logging.WARNING)
    for path in fixture_capture.capture(settings.core, out):
        typer.echo(str(path))
