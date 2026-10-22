"""Extensible investment-score formulas (composite scores, not just fair value) and ranking."""
from __future__ import annotations

from typing import Callable, Literal, Optional

import pandas as pd

from screening.ratio_ranking import get_tickers_for_universe

from .common import compute_with_cache
from .lynch_full import full_lynch_score

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)

ScoreName = Literal["lynch_full"]

# Registry of investment-score formulas. To add a new one: write a module exposing
# a function with signature (ticker_symbol, *, info=None) -> dict | None whose
# returned dict includes a "score" key (higher = more attractive), then register
# it here and add its name to ScoreName above so the API/UI can select it.
_SCORE_FUNCS: dict[str, Callable[..., Optional[dict]]] = {
    "lynch_full": full_lynch_score,
}


def rank_stocks_by_score(
    score_name: ScoreName,
    *,
    universe_slug: str = "sp500",
    limit: Optional[int] = None,
    csv_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Compute an investment score for every ticker in a universe using the chosen
    formula, and return them sorted by score descending (most attractive first).

    Same DB-cache-first behavior as rank_stocks_by_fair_value (see
    valuation.common.compute_with_cache): tries one bulk DB query per universe
    before falling back to a live yfinance call for any ticker not usefully cached.

    Args:
        score_name: One of the registered formula names (currently "lynch_full").
        universe_slug: e.g. 'sp500', 'sp400', 'sp600', 'nasdaq', 'nyse'.
        limit: If set, only evaluate the first this many tickers from the universe.
        csv_dir: Optional directory for CSV when using universe_slug fallback.

    Returns:
        DataFrame with at least columns [ticker, current_price, score] plus
        formula-specific extra columns, sorted by score descending.

    Raises:
        ValueError: If score_name is not registered.
    """
    if score_name not in _SCORE_FUNCS:
        raise ValueError(f"Unsupported score '{score_name}'")
    fn = _SCORE_FUNCS[score_name]

    tickers = get_tickers_for_universe(universe_slug, csv_dir=csv_dir)
    if limit:
        tickers = tickers[:limit]

    rows, cache_hits = compute_with_cache(fn, tickers, universe_slug)

    _log.info(
        "Score ranking: formula=%s universe=%s evaluated=%d valid=%d cache_hits=%d",
        score_name, universe_slug, len(tickers), len(rows), cache_hits,
    )

    if not rows:
        return pd.DataFrame(columns=["ticker", "current_price", "score"])

    df = pd.DataFrame(rows)
    return df.sort_values("score", ascending=False).reset_index(drop=True)
