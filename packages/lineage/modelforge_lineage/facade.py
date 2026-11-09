"""One handle on the lineage store; the repositories are used as modules alongside it."""

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg.rows import DictRow

from modelforge_lineage.db import Database


class Lineage:
    def __init__(self, db: Database) -> None:
        self.db = db

    @contextmanager
    def connection(self) -> Iterator[Connection[DictRow]]:
        with self.db.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Iterator[Connection[DictRow]]:
        with self.db.transaction() as conn:
            yield conn
