"""Temporary Postgres databases, an in-memory span exporter and pytest fixtures for the packages."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from psycopg import Connection, sql
from psycopg.rows import DictRow

from modelforge_lineage.db import Database
from modelforge_lineage.migrate import TABLES, apply
from modelforge_lineage.models import RunRecord
from modelforge_lineage.runs import create_run
from modelforge_lineage.tracing import configure_tracing, shutdown_tracing

DEFAULT_DATABASE_URL = "postgresql://modelforge:modelforge@localhost:5432/modelforge"


@contextmanager
def temporary_database(url: str) -> Iterator[str]:
    """Create a throwaway database next to ``url``'s, yield its URL, then drop it."""
    name = f"modelforge_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        try:
            yield urlunsplit(urlsplit(url)._replace(path=f"/{name}"))
        finally:
            admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s", (name,)
            )
            admin.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("MODELFORGE_DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.Error as exc:
        message = f"Postgres unreachable at {url}: {exc}"
        if os.environ.get("MODELFORGE_REQUIRE_DB") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return url


@pytest.fixture(scope="session")
def database(database_url: str) -> Iterator[Database]:
    with temporary_database(database_url) as url:
        db = Database(url)
        try:
            apply(db)
            yield db
        finally:
            db.close()


@pytest.fixture
def db(database: Database) -> Iterator[Database]:
    truncate = sql.SQL("truncate {} restart identity cascade").format(
        sql.SQL(", ").join(sql.Identifier(t) for t in TABLES)
    )
    with database.transaction() as conn:
        conn.execute(truncate)
    yield database


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    """Install the tracing provider with an in-memory exporter; yield it, then shut tracing down."""
    exporter = InMemorySpanExporter()
    configure_tracing("test", exporter=exporter)
    try:
        yield exporter
    finally:
        shutdown_tracing()


def create_test_run(
    conn: Connection[DictRow], run_id: str = "run_a", *, requester: str = "alice"
) -> RunRecord:
    """Create a queued run with fixed values; call inside a transaction."""
    return create_run(
        conn,
        run_id=run_id,
        requester=requester,
        profile="default",
        brief_hash="h",
        brief_ref="b" * 64,
        dataset_ref="synthetic_credit",
        seed=42,
    )
