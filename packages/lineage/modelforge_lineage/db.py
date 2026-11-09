"""A thin wrapper over a psycopg connection pool."""

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool


class Database:
    """Pooled Postgres access.

    Connections are autocommit with dict rows; write inside ``transaction()``.
    """

    def __init__(self, url: str, *, min_size: int = 1, max_size: int = 4) -> None:
        self.url = url
        self.pool: ConnectionPool[Connection[DictRow]] = ConnectionPool(
            url,
            connection_class=Connection[DictRow],
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
            open=True,
        )

    @contextmanager
    def connection(self) -> Iterator[Connection[DictRow]]:
        with self.pool.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Iterator[Connection[DictRow]]:
        with self.pool.connection() as conn, conn.transaction():
            yield conn

    def close(self) -> None:
        self.pool.close()
