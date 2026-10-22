"""Graham Number: conservative growth-independent intrinsic value benchmark."""
from __future__ import annotations

import math

from .common import _current_price, _get_info, _is_missing, upside_pct


def graham_number_fair_value(ticker_symbol: str, *, info: dict | None = None) -> dict | None:
    """
    Graham Number: fair_value = sqrt(22.5 * trailing_eps * book_value_per_share).

    Returns None if trailing EPS or book value per share is missing/<=0 (the formula
    is undefined/meaningless otherwise), or if current price is unavailable.

    Args:
        ticker_symbol: Stock ticker symbol.
        info: Optional pre-fetched info-like dict (e.g. from a bulk DB snapshot
            query) to use instead of fetching/looking up this ticker individually.

    Returns:
        {ticker, current_price, trailing_eps, book_value_per_share, fair_value, upside_pct}
        or None.
    """
    if info is None:
        info = _get_info(ticker_symbol)

    trailing_eps = info.get("trailingEps")
    book_value_per_share = info.get("bookValue")
    if _is_missing(trailing_eps) or trailing_eps <= 0:
        return None
    if _is_missing(book_value_per_share) or book_value_per_share <= 0:
        return None

    current_price = _current_price(info)
    if current_price is None:
        return None

    fair_value = math.sqrt(22.5 * float(trailing_eps) * float(book_value_per_share))

    return {
        "ticker": ticker_symbol,
        "current_price": current_price,
        "trailing_eps": float(trailing_eps),
        "book_value_per_share": float(book_value_per_share),
        "fair_value": fair_value,
        "upside_pct": upside_pct(fair_value, current_price),
    }
