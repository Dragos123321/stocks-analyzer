"""PostgreSQL configuration for stock-analyzer backend."""
from __future__ import annotations

import os


def get_db_config() -> dict[str, str | int]:
    """
    Build DB connection config from environment with defaults.

    Env vars: PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE.
    Defaults: localhost, 5432, postgres, empty password, stock-analyzer.
    """
    return {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "user": os.environ.get("PGUSER", "postgres"),
        "password": os.environ.get("PGPASSWORD", "DANA!@#eugen.159"),
        "dbname": os.environ.get("PGDATABASE", "stock-analyzer"),
    }


def get_connection_string() -> str:
    """Build a psycopg2-style connection string (for connect with dsn=)."""
    c = get_db_config()
    # dbname with hyphen is valid; no quoting needed in connection string
    return (
        f"host={c['host']} port={c['port']} dbname={c['dbname']} "
        f"user={c['user']} password={c['password']}"
    )
