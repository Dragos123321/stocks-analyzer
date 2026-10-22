"""
Fetch latest yfinance data for a universe and write to ticker_snapshot and price_history.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
import yfinance as yf

from .ratio_ranking import get_tickers_for_universe
from db.snapshots import (
    clean_snapshot_and_history_for_symbols,
    get_symbol_ids_for_tickers,
    update_symbol_name_sector,
    upsert_eps_annual,
    upsert_eps_quarterly,
    upsert_price_history,
    upsert_ticker_snapshot,
    _safe_float,
    _safe_int,
)

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)


def _info_to_snapshot_fields(info: dict[str, Any]) -> dict[str, Any]:
    """Map yfinance .info dict to ticker_snapshot columns (including CANSLIM fields)."""
    return {
        "market_cap": _safe_int(info, "marketCap"),
        "trailing_pe": _safe_float(info, "trailingPE"),
        "forward_pe": _safe_float(info, "forwardPE"),
        "peg_ratio": _safe_float(info, "pegRatio") or _safe_float(info, "trailingPegRatio"),
        "price_to_sales": _safe_float(info, "priceToSalesTrailing12Months") or _safe_float(info, "priceToSales"),
        "ev_to_ebitda": _safe_float(info, "enterpriseToEbitda"),
        "earnings_growth": _safe_float(info, "earningsQuarterlyGrowth") or _safe_float(info, "earningsGrowth"),
        "revenue_growth": None,
        "return_on_equity": _safe_float(info, "returnOnEquity"),
        "price": _safe_float(info, "regularMarketPrice"),
        "fifty_two_week_high": _safe_float(info, "fiftyTwoWeekHigh"),
        "volume": _safe_int(info, "volume"),
        "avg_volume": _safe_int(info, "averageVolume"),
        "held_percent_institutions": _safe_float(info, "heldPercentInstitutions"),
        "current_ratio": _safe_float(info, "currentRatio"),
        "quick_ratio": _safe_float(info, "quickRatio"),
        "trailing_eps": _safe_float(info, "trailingEps"),
        "book_value_per_share": _safe_float(info, "bookValue"),
        "free_cashflow": _safe_float(info, "freeCashflow"),
        "shares_outstanding": _safe_float(info, "sharesOutstanding"),
        "dividend_rate": _safe_float(info, "dividendRate"),
        "debt_to_equity": _safe_float(info, "debtToEquity"),
    }


def _history_to_rows(hist: pd.DataFrame) -> list[tuple[date, float, float, float, float, int]]:
    """Convert yfinance history DataFrame to list of (date, o, h, l, c, volume)."""
    if hist is None or hist.empty:
        return []
    rows = []
    for dt, row in hist.iterrows():
        if pd.isna(dt):
            continue
        d = dt.date() if isinstance(dt, datetime) else dt
        o = float(row.get("Open", 0) or 0)
        h = float(row.get("High", 0) or 0)
        l = float(row.get("Low", 0) or 0)
        c = float(row.get("Close", 0) or 0)
        v = int(row.get("Volume", 0) or 0)
        rows.append((d, o, h, l, c, v))
    return rows


def fetch_latest_for_universe(
    universe_slug: str,
    *,
    snapshot_date: date | None = None,
    history_period: str = "2y",
    batch_size: int = 30,
) -> dict[str, int | list[str]]:
    """
    Fetch latest yfinance data for all tickers in the universe and write to DB.

    Args:
        universe_slug: 'sp500' or 'nasdaq'.
        snapshot_date: Date for ticker_snapshot rows; default today.
        history_period: yfinance period for price history (default '2y').
        batch_size: Process tickers in batches of this size (default 30).

    Returns:
        Dict with: tickers_processed, snapshots_updated, price_rows_updated, errors (list of ticker or message).
    """
    if snapshot_date is None:
        snapshot_date = date.today()

    tickers = get_tickers_for_universe(universe_slug)
    if not tickers:
        return {"tickers_processed": 0, "snapshots_updated": 0, "price_rows_updated": 0, "errors": ["No tickers for universe"]}

    symbol_ids = get_symbol_ids_for_tickers(tickers)
    missing = [t for t in tickers if t not in symbol_ids]
    if missing:
        _log.warning("Tickers not in DB (skipped): %s", missing[:20])  # log first 20

    tickers_with_id = [(t, symbol_ids[t]) for t in tickers if t in symbol_ids]
    symbol_ids_list = [sid for _, sid in tickers_with_id]

    # Clean existing snapshot and price history for this universe so we reload fresh
    clean_snapshot_and_history_for_symbols(symbol_ids_list)

    snapshots_updated = 0
    price_rows_updated = 0
    errors: list[str] = []

    for i in range(0, len(tickers_with_id), batch_size):
        batch = tickers_with_id[i : i + batch_size]
        for ticker, symbol_id in batch:
            try:
                t = yf.Ticker(ticker)
                info = t.info or {}
                fields = _info_to_snapshot_fields(info)
                upsert_ticker_snapshot(
                    symbol_id,
                    snapshot_date,
                    **fields,
                )
                snapshots_updated += 1

                # Backfill symbol name and sector from yfinance when present
                name = (info.get("shortName") or info.get("longName")) if info else None
                sector = info.get("sector") if info else None
                if name or sector:
                    update_symbol_name_sector(symbol_id, name=name, sector=sector)

                hist = t.history(period=history_period, auto_adjust=True)
                rows = _history_to_rows(hist)
                if rows:
                    upsert_price_history(symbol_id, rows)
                    price_rows_updated += len(rows)

                # Store EPS from income statements for financials API
                try:
                    from financials.eps import _compute_diluted_eps
                    income_stmt = t.income_stmt
                    if income_stmt is not None and not income_stmt.empty:
                        for col in income_stmt.columns:
                            year = getattr(col, "year", None)
                            if year is not None:
                                eps_val = _compute_diluted_eps(income_stmt, col)
                                if eps_val is not None:
                                    upsert_eps_annual(symbol_id, int(year), float(eps_val))
                    qis = t.quarterly_income_stmt
                    if qis is not None and not qis.empty:
                        for col in qis.columns:
                            period_end = col.date() if hasattr(col, "date") else col
                            if hasattr(period_end, "year"):
                                eps_val = _compute_diluted_eps(qis, col)
                                if eps_val is not None:
                                    if not isinstance(period_end, date):
                                        period_end = date(period_end.year, period_end.month, period_end.day)
                                    upsert_eps_quarterly(symbol_id, period_end, float(eps_val))
                except Exception:
                    pass
            except Exception as e:
                errors.append(f"{ticker}: {e}")
                _log.warning("Fetch failed for %s: %s", ticker, e)

    return {
        "tickers_processed": len(tickers_with_id),
        "snapshots_updated": snapshots_updated,
        "price_rows_updated": price_rows_updated,
        "errors": errors[:50],  # cap for response
    }
