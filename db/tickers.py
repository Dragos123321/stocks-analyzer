"""Read ticker lists from the database by universe."""
from __future__ import annotations

from .connection import get_connection

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)


def get_tickers_by_universe_slug(slug: str) -> list[str]:
    """
    Return ticker symbols for the given universe slug (e.g. 'sp500', 'nasdaq').

    Queries universe -> universe_symbol -> symbol and returns ticker strings
    in order (by symbol.id or symbol.ticker). Returns empty list if the
    universe does not exist or has no symbols.

    Raises:
        Exception: On DB connection or query errors (caller can catch and fall back to CSV).
    """
    slug_lower = slug.strip().lower()
    _log.info("DB: fetching tickers for universe slug=%s", slug_lower)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.ticker
                FROM symbol s
                JOIN universe_symbol us ON us.symbol_id = s.id
                JOIN universe u ON u.id = us.universe_id
                WHERE u.slug = %s
                ORDER BY s.ticker
                """,
                (slug_lower,),
            )
            rows = cur.fetchall()
    tickers = [r[0] for r in rows] if rows else []
    _log.info("DB: universe slug=%s returned %d tickers", slug_lower, len(tickers))
    return tickers
