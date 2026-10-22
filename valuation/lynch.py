"""Peter Lynch Fair Value: fair P/E equals the annual earnings growth rate."""
from __future__ import annotations

from .common import _clipped_growth_rate, _current_price, _get_info, _is_missing, upside_pct


def lynch_fair_value(ticker_symbol: str, *, info: dict | None = None) -> dict | None:
    """
    Peter Lynch Fair Value: fair_pe = growth_rate_pct (0-50), fair_value = trailing_eps * fair_pe.

    Growth rate comes from 'earningsGrowth' (fallback 'earningsQuarterlyGrowth'), clipped
    to [0, 0.50]. Returns None if trailing EPS is missing/<=0, growth is missing/<=0
    (the formula is not meaningful for flat/shrinking-earnings companies), or current
    price is unavailable.

    Args:
        ticker_symbol: Stock ticker symbol.
        info: Optional pre-fetched info-like dict (e.g. from a bulk DB snapshot
            query) to use instead of fetching/looking up this ticker individually.

    Returns:
        {ticker, current_price, trailing_eps, growth_rate_pct, fair_value, upside_pct}
        or None.
    """
    if info is None:
        info = _get_info(ticker_symbol)

    trailing_eps = info.get("trailingEps")
    if _is_missing(trailing_eps) or trailing_eps <= 0:
        return None

    growth_rate = _clipped_growth_rate(info, lo=0.0, hi=0.50)
    if growth_rate is None:
        return None

    current_price = _current_price(info)
    if current_price is None:
        return None

    growth_rate_pct = growth_rate * 100.0
    fair_value = float(trailing_eps) * growth_rate_pct

    return {
        "ticker": ticker_symbol,
        "current_price": current_price,
        "trailing_eps": float(trailing_eps),
        "growth_rate_pct": growth_rate_pct,
        "fair_value": fair_value,
        "upside_pct": upside_pct(fair_value, current_price),
    }
