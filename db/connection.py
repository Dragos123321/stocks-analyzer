"""PostgreSQL connection helpers."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.extensions import connection as pg_connection

from .config import get_connection_string, get_db_config

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)


@contextmanager
def get_connection() -> Generator[pg_connection, None, None]:
    """
    Context manager that yields a PostgreSQL connection.

    Caller must not hold the connection across long operations;
    use for short queries and then close.
    """
    cfg = get_db_config()
    _log.debug("Opening DB connection to %s:%s/%s", cfg["host"], cfg["port"], cfg["dbname"])
    conn = psycopg2.connect(get_connection_string())
    try:
        yield conn
        conn.commit()
        _log.debug("DB connection committed and closing")
    except Exception as e:
        conn.rollback()
        _log.warning("DB connection rollback after error: %s", e)
        raise
    finally:
        conn.close()
        _log.debug("DB connection closed")
