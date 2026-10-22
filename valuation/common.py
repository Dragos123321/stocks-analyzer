"""Shared helpers for fair-value/investment-score formulas (Lynch, Graham, DCF, ...)."""
from __future__ import annotations

from typing import Callable, Optional

import yfinance as yf

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)

# Fields used as a staleness sentinel: if ALL of these are missing on a cached row,
# the row predates the relevant migration (see scripts/migrate_add_valuation_columns.py)
# and we should fall back to a live yfinance call instead of trusting the cache.
_CACHE_FIELDS = (
    "trailingEps", "bookValue", "freeCashflow", "sharesOutstanding",
    "dividendRate", "debtToEquity",
)


def _get_db_info(ticker_symbol: str) -> dict | None:
    """Return cached valuation inputs from ticker_snapshot, or None on failure."""
    try:
        from db.snapshots import get_latest_valuation_fields_for_ticker
        return get_latest_valuation_fields_for_ticker(ticker_symbol)
    except Exception:
        return None


def _is_missing(value) -> bool:
    """True if value is None or NaN (SQL NULL surfaces as NaN once it passes through pandas)."""
    if value is None:
        return True
    return isinstance(value, float) and value != value  # NaN != NaN


def _cache_has_valuation_data(info_like: dict) -> bool:
    """True if at least one of the cached valuation-specific fields is present (not None/NaN)."""
    return any(not _is_missing(info_like.get(k)) for k in _CACHE_FIELDS)


def _get_info(ticker_symbol: str) -> dict:
    """
    Return a yfinance-info-shaped dict of valuation inputs for a ticker.

    Tries the DB cache (ticker_snapshot, populated by "Fetch latest data") first;
    falls back to a live yfinance call when there's no cached row or it predates
    the valuation columns (trailingEps/bookValue/freeCashflow/sharesOutstanding
    all null -- see scripts/migrate_add_valuation_columns.py).
    """
    db_info = _get_db_info(ticker_symbol)
    if db_info is not None and _cache_has_valuation_data(db_info):
        return db_info
    try:
        return yf.Ticker(ticker_symbol).info or {}
    except Exception:
        return {}


def _current_price(info: dict) -> float | None:
    """Return current price from info: 'currentPrice' or 'regularMarketPrice'."""
    price = info.get("currentPrice")
    if _is_missing(price):
        price = info.get("regularMarketPrice")
    if _is_missing(price):
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _clipped_growth_rate(info: dict, lo: float = 0.0, hi: float = 0.50) -> float | None:
    """
    Return annual earnings growth rate as a decimal (e.g. 0.15 for 15%), clipped to [lo, hi].

    Uses 'earningsGrowth', falling back to 'earningsQuarterlyGrowth'. Returns None if
    both are missing or the growth rate is not strictly positive (the formulas that use
    this helper are not meaningful for flat/shrinking earnings).
    """
    growth = info.get("earningsGrowth")
    if _is_missing(growth):
        growth = info.get("earningsQuarterlyGrowth")
    if _is_missing(growth):
        return None
    try:
        growth = float(growth)
    except (TypeError, ValueError):
        return None
    if growth <= 0:
        return None
    return max(lo, min(hi, growth))


def upside_pct(fair_value: float, current_price: float) -> float:
    """Percentage upside (positive) or downside (negative) of fair_value vs current_price."""
    return (fair_value - current_price) / current_price * 100.0


def compute_with_cache(
    fn: Callable[..., Optional[dict]],
    tickers: list[str],
    universe_slug: Optional[str],
) -> tuple[list[dict], int]:
    """
    Run a per-ticker valuation/score function across tickers using ONE bulk DB
    snapshot query for the universe (when available) instead of a live yfinance
    call per ticker. Any ticker missing from the cache, or whose cached row
    predates the relevant columns, falls back to a live per-ticker call (fn(ticker)
    with no info= override, which itself tries a single-ticker DB lookup before
    going live -- see _get_info).

    Shared by valuation/ranking.py and valuation/scoring.py so every fair-value
    method and every investment-score formula gets the same DB-cache-first
    behavior for free.

    Args:
        fn: Per-ticker function with signature (ticker_symbol, *, info=None) -> dict | None.
        tickers: Tickers to evaluate.
        universe_slug: Universe to bulk-fetch cached snapshots for, or None to skip
            the bulk fetch entirely (every ticker goes through fn's own DB/live fallback).

    Returns:
        (rows, cache_hits) -- rows is the list of non-None results; cache_hits is
        how many were served from the bulk cache without a live yfinance call.
    """
    snapshot_by_ticker: dict[str, dict] = {}
    if universe_slug:
        try:
            from db.snapshots import get_valuation_snapshots_for_universe
            snap_df = get_valuation_snapshots_for_universe(universe_slug)
            if not snap_df.empty:
                snapshot_by_ticker = {row["ticker"]: row for row in snap_df.to_dict(orient="records")}
        except Exception as e:
            _log.warning("Bulk valuation cache fetch failed (%s), using live yfinance", e)

    rows: list[dict] = []
    cache_hits = 0
    for ticker in tickers:
        snap = snapshot_by_ticker.get(ticker)
        try:
            if snap is not None and _cache_has_valuation_data(snap):
                result = fn(ticker, info=snap)
                cache_hits += 1
            else:
                result = fn(ticker)
        except Exception:
            continue
        if result is None:
            continue
        rows.append(result)

    return rows, cache_hits
