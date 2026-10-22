"""Rank stocks across a universe by fair-value method (Lynch, Graham, DCF)."""
from __future__ import annotations

from typing import Callable, Literal, Optional

import pandas as pd

from screening.ratio_ranking import get_tickers_for_universe

from .common import compute_with_cache
from .dcf import dcf_fair_value
from .graham import graham_number_fair_value
from .lynch import lynch_fair_value

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)

MethodName = Literal["lynch", "graham", "dcf"]

_METHOD_FUNCS: dict[str, Callable[[str], Optional[dict]]] = {
    "lynch": lynch_fair_value,
    "graham": graham_number_fair_value,
    "dcf": dcf_fair_value,
}


def rank_stocks_by_fair_value(
    method: MethodName,
    *,
    universe_slug: str = "sp500",
    limit: Optional[int] = None,
    csv_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Compute fair value for every ticker in a universe using the chosen method.

    Tickers are resolved via get_tickers_for_universe (DB for sp500/nasdaq/sp400/sp600,
    CSV otherwise). Valuation inputs are loaded in ONE bulk DB query per universe
    (get_valuation_snapshots_for_universe, populated by "Fetch latest data") instead
    of a live yfinance call per ticker. Any ticker missing from the cache, or whose
    cached row predates the valuation columns, falls back to a live per-ticker
    yfinance call (same as calling the method function directly). The per-ticker
    method function is called for each ticker, skipping failures/None results (same
    pattern as rank_stocks_by_ratio / rank_stocks_by_canslim).

    Args:
        method: One of "lynch", "graham", "dcf".
        universe_slug: e.g. 'sp500', 'sp400', 'sp600', 'nasdaq', 'nyse'.
        limit: If set, only evaluate the first this many tickers from the universe.
        csv_dir: Optional directory for CSV when using universe_slug fallback.

    Returns:
        DataFrame with at least columns [ticker, current_price, fair_value, upside_pct]
        plus method-specific extra columns. Not sorted -- caller decides ascending vs
        descending for upside/downside views.

    Raises:
        ValueError: If method is not one of the supported names.
    """
    if method not in _METHOD_FUNCS:
        raise ValueError(f"Unsupported method '{method}'")
    fn = _METHOD_FUNCS[method]

    tickers = get_tickers_for_universe(universe_slug, csv_dir=csv_dir)
    if limit:
        tickers = tickers[:limit]

    rows, cache_hits = compute_with_cache(fn, tickers, universe_slug)

    _log.info(
        "Fair value ranking: method=%s universe=%s evaluated=%d valid=%d cache_hits=%d",
        method, universe_slug, len(tickers), len(rows), cache_hits,
    )

    if not rows:
        return pd.DataFrame(columns=["ticker", "current_price", "fair_value", "upside_pct"])

    return pd.DataFrame(rows)
