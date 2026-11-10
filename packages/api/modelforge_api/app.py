"""The FastAPI application: a control plane over the lineage store.

`create_app` touches no database; the lifespan opens the pool, applies the migrations,
creates the checkpoint tables, seeds the server-side settings and configures tracing.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from modelforge_lineage import migrate
from modelforge_lineage import settings as server_settings
from modelforge_lineage.db import Database
from modelforge_lineage.store import ArtifactStore
from modelforge_lineage.tracing import configure_tracing, provider, shutdown_tracing
from modelforge_orchestrator.config import setting_seeds
from modelforge_orchestrator.worker import setup_checkpointer
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

import modelforge_api
from modelforge_api import errors
from modelforge_api.routers import documents, findings, inventory, runs, system
from modelforge_api.settings import ApiSettings

__all__ = ["API_PREFIX", "EXCLUDED_URLS", "create_app"]

API_PREFIX = "/api/v1"
EXCLUDED_URLS = "/api/v1/health,.*/events/stream"
"""Request paths the HTTP instrumentor does not trace: the health probe and the SSE streams."""


def create_app(settings: ApiSettings) -> FastAPI:
    """Build the application over ``settings``; nothing connects until the lifespan starts."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        core = settings.core
        db = Database(core.database_url, max_size=16)
        instrumented = False
        try:
            app.state.db = db
            app.state.store = ArtifactStore(core.artifact_dir)
            migrate.apply(db)
            setup_checkpointer(db)
            with db.transaction() as conn:
                server_settings.seed(conn, setting_seeds(core))
            configure_tracing("modelforge-api", core.gateway.otlp_endpoints)
            installed = provider()
            if installed is not None:
                FastAPIInstrumentor.instrument_app(
                    app, tracer_provider=installed, excluded_urls=EXCLUDED_URLS
                )
                # the stack was built for the lifespan; rebuild it with the instrumentor
                app.middleware_stack = None
                instrumented = True
            yield
        finally:
            if instrumented:
                FastAPIInstrumentor.uninstrument_app(app)
            db.close()
            shutdown_tracing()

    app = FastAPI(
        title="ModelForge API",
        version=modelforge_api.__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    errors.install(app)
    app.include_router(system.router, prefix=API_PREFIX)
    app.include_router(runs.router, prefix=API_PREFIX)
    app.include_router(documents.router, prefix=API_PREFIX)
    app.include_router(findings.router, prefix=API_PREFIX)
    app.include_router(inventory.router, prefix=API_PREFIX)
    return app
