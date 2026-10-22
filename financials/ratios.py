"""Valuation ratios (P/E, PEG, Price/Sales, EV/EBITDA). Use DB when available, else yfinance."""
from __future__ import annotations

import yfinance as yf


def _ratios_from_db(ticker_symbol: str) -> dict[str, float | None] | None:
    """Return latest valuation ratios from DB, or None if not available."""
    try:
        from db.snapshots import get_latest_valuation_ratios_for_ticker
        return get_latest_valuation_ratios_for_ticker(ticker_symbol)
    except Exception:
        return None


def pe(ticker_symbol: str) -> float | None:
    """
    Return the current (trailing) price-to-earnings ratio for the given ticker.
    Uses DB (ticker_snapshot) when available, else yfinance.
    """
    db = _ratios_from_db(ticker_symbol)
    if db is not None and db.get("pe") is not None:
        return db["pe"]
    t = yf.Ticker(ticker_symbol)
    info = t.info
    return info.get("trailingPE")


def peg_ratio(ticker_symbol: str) -> float | None:
    """
    Return the PEG ratio (price/earnings to growth) for the given ticker.
    Uses DB when available, else yfinance.
    """
    db = _ratios_from_db(ticker_symbol)
    if db is not None and db.get("peg") is not None:
        return db["peg"]
    t = yf.Ticker(ticker_symbol)
    info = t.info
    return info.get("pegRatio") or info.get("trailingPegRatio")


def price_to_sales(ticker_symbol: str) -> float | None:
    """
    Return the trailing Price-to-Sales ratio for the given ticker.
    Uses DB when available, else yfinance.
    """
    db = _ratios_from_db(ticker_symbol)
    if db is not None and db.get("price_to_sales") is not None:
        return db["price_to_sales"]
    t = yf.Ticker(ticker_symbol)
    info = t.info
    return info.get("priceToSalesTrailing12Months") or info.get("priceToSales")


def ev_to_ebitda(ticker_symbol: str) -> float | None:
    """
    Return the Enterprise Value to EBITDA ratio for the given ticker.
    Uses DB when available, else yfinance.
    """
    db = _ratios_from_db(ticker_symbol)
    if db is not None and db.get("ev_to_ebitda") is not None:
        return db["ev_to_ebitda"]
    t = yf.Ticker(ticker_symbol)
    info = t.info
    return info.get("enterpriseToEbitda")
