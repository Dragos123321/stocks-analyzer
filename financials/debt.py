"""Liquidity ratios: current ratio and quick ratio. Use DB when available, else yfinance."""
from __future__ import annotations

import yfinance as yf


def _debt_from_db(ticker: str) -> dict[str, float | None] | None:
    """Return current_ratio and quick_ratio from DB, or None if not available."""
    try:
        from db.snapshots import get_latest_debt_ratios_for_ticker
        return get_latest_debt_ratios_for_ticker(ticker)
    except Exception:
        return None


def get_current_ratio(ticker: str) -> float:
    """
    Return the current ratio (current assets / current liabilities) for the ticker.
    Uses DB (ticker_snapshot) when available, else yfinance.
    """
    db = _debt_from_db(ticker)
    if db is not None and db.get("current_ratio") is not None:
        return float(db["current_ratio"])
    stock = yf.Ticker(ticker)
    info = stock.info
    ratio = info.get("currentRatio")
    if ratio is None:
        raise ValueError(f"Current ratio not available for {ticker}")
    return float(ratio)


def get_quick_ratio(ticker: str) -> float:
    """
    Return the quick ratio (acid test) for the ticker.
    Uses DB when available, else yfinance.
    """
    db = _debt_from_db(ticker)
    if db is not None and db.get("quick_ratio") is not None:
        return float(db["quick_ratio"])
    stock = yf.Ticker(ticker)
    info = stock.info
    ratio = info.get("quickRatio")
    if ratio is None:
        raise ValueError(f"Quick ratio not available for {ticker}")
    return float(ratio)
