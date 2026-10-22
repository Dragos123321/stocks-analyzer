"""Simplified single-stage DCF: FCF per share projected forward with a Gordon-growth terminal value."""
from __future__ import annotations

from .common import _clipped_growth_rate, _current_price, _get_info, _is_missing, upside_pct

DEFAULT_PROJECTION_YEARS = 5
DEFAULT_DISCOUNT_RATE = 0.09
DEFAULT_TERMINAL_GROWTH = 0.025
DCF_GROWTH_CAP = 0.15
DCF_DEFAULT_GROWTH = 0.03


def dcf_fair_value(
    ticker_symbol: str,
    *,
    info: dict | None = None,
    projection_years: int = DEFAULT_PROJECTION_YEARS,
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH,
) -> dict | None:
    """
    Simplified single-stage DCF (FCF-per-share based) with a Gordon-growth terminal value.

    fcf_per_share = freeCashflow / sharesOutstanding (None if either missing/<=0 -- this
    is the only hard data requirement). Growth used for the projection is the clipped
    earnings growth rate (capped lower than Lynch's, at DCF_GROWTH_CAP=0.15), defaulting
    to DCF_DEFAULT_GROWTH=0.03 when growth is missing/negative -- unlike Lynch, DCF should
    still produce a number for flat/no-growth but cash-generative companies.

    Args:
        ticker_symbol: Stock ticker symbol.
        info: Optional pre-fetched info-like dict (e.g. from a bulk DB snapshot
            query) to use instead of fetching/looking up this ticker individually.
        projection_years, discount_rate, terminal_growth: DCF parameters.

    Returns None if fcf_per_share or shares outstanding is unusable, or current price is
    unavailable.

    Raises:
        ValueError: If terminal_growth >= discount_rate (Gordon growth diverges).

    Returns:
        {ticker, current_price, fcf_per_share, growth_rate_pct, fair_value, upside_pct}
        or None.
    """
    if terminal_growth >= discount_rate:
        raise ValueError("terminal_growth must be strictly less than discount_rate")

    if info is None:
        info = _get_info(ticker_symbol)

    free_cashflow = info.get("freeCashflow")
    shares_outstanding = info.get("sharesOutstanding")
    if _is_missing(free_cashflow) or _is_missing(shares_outstanding) or shares_outstanding <= 0:
        return None
    fcf_per_share = float(free_cashflow) / float(shares_outstanding)
    if fcf_per_share <= 0:
        return None

    current_price = _current_price(info)
    if current_price is None:
        return None

    growth_rate = _clipped_growth_rate(info, lo=0.0, hi=DCF_GROWTH_CAP)
    if growth_rate is None:
        growth_rate = DCF_DEFAULT_GROWTH

    discounted_sum = 0.0
    projected_fcf = fcf_per_share
    for year in range(1, projection_years + 1):
        projected_fcf = fcf_per_share * (1 + growth_rate) ** year
        discounted_sum += projected_fcf / (1 + discount_rate) ** year

    terminal_value = projected_fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    discounted_terminal = terminal_value / (1 + discount_rate) ** projection_years

    fair_value = discounted_sum + discounted_terminal

    return {
        "ticker": ticker_symbol,
        "current_price": current_price,
        "fcf_per_share": fcf_per_share,
        "growth_rate_pct": growth_rate * 100.0,
        "fair_value": fair_value,
        "upside_pct": upside_pct(fair_value, current_price),
    }
