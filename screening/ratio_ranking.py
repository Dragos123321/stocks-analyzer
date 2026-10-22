"""Rank stocks by a ratio function (e.g. P/E, Price/Sales) from DB or CSV ticker lists."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)

SP500_CSV_PATH = "sp500_symbols.csv"
NASDAQ100_CSV_PATH = "nasdaq100_symbols.csv"
SP400_CSV_PATH = "sp400_symbols.csv"
SP600_CSV_PATH = "sp600_symbols.csv"

# Universe slug -> CSV filename (for fallback when DB is empty or unavailable)
# "nasdaq" = NASDAQ 100 (not full NASDAQ)
UNIVERSE_CSV: dict[str, str] = {
    "sp500": SP500_CSV_PATH,
    "nasdaq": NASDAQ100_CSV_PATH,
    "sp400": SP400_CSV_PATH,
    "sp600": SP600_CSV_PATH,
}

# ratio function __name__ -> ticker_snapshot column for DB-backed ratio ranking
RATIO_TO_SNAPSHOT_COLUMN: dict[str, str] = {
    "pe": "trailing_pe",
    "peg_ratio": "peg_ratio",
    "price_to_sales": "price_to_sales",
    "ev_to_ebitda": "ev_to_ebitda",
}


@dataclass
class ScreenResult:
    """Single result from a ratio screen: ticker and its ratio value."""

    ticker: str
    value: float


def get_tickers_for_universe(
    universe_slug: str,
    csv_dir: Optional[str] = None,
) -> list[str]:
    """
    Load tickers for a universe: try DB first for sp500/nasdaq/sp400/sp600, then fall back to CSV.

    Args:
        universe_slug: e.g. 'sp500', 'nasdaq', 'sp400', 'sp600'. For these, DB is tried first.
        csv_dir: Optional directory to prepend to CSV path when falling back.

    Returns:
        List of ticker strings.
    """
    slug = universe_slug.strip().lower()
    if slug in ("sp500", "nasdaq", "sp400", "sp600"):
        try:
            from db import get_tickers_by_universe_slug
            tickers = get_tickers_by_universe_slug(slug)
            if tickers:
                _log.info("Tickers for universe %s: using DB (%d symbols)", slug, len(tickers))
                return tickers
            _log.info("Tickers for universe %s: DB returned empty, falling back to CSV", slug)
        except Exception as e:
            _log.warning("Tickers for universe %s: DB failed (%s), falling back to CSV", slug, e)
        path = UNIVERSE_CSV.get(slug, SP500_CSV_PATH)
    else:
        path = UNIVERSE_CSV.get(slug, SP500_CSV_PATH)
    if csv_dir and not path.startswith("/") and "\\" not in path:
        path = os.path.join(csv_dir, path)
    _log.info("Tickers for universe %s: loading from CSV path=%s", slug, path)
    return get_tickers_from_csv(path)


def get_tickers_from_csv(path: str = SP500_CSV_PATH) -> list[str]:
    """
    Load ticker symbols from a CSV file that has a 'Symbol' column.

    Args:
        path: Path to the CSV file. Defaults to SP500_CSV_PATH ("sp500_symbols.csv").

    Returns:
        List of ticker strings (striped, no duplicates beyond the file).

    Raises:
        ValueError: If the CSV does not contain a 'Symbol' column.
    """
    _log.debug("Loading tickers from CSV: path=%s", path)
    df = pd.read_csv(path)
    if "Symbol" not in df.columns:
        raise ValueError("CSV must contain a 'Symbol' column")
    tickers = df["Symbol"].astype(str).str.strip().tolist()
    _log.info("Loaded %d tickers from CSV path=%s", len(tickers), path)
    return tickers


def rank_stocks_by_ratio(
    ratio_fn: Callable[[str], Optional[float]],
    *,
    limit: Optional[int] = None,
    min_value: Optional[float] = None,
    csv_path: Optional[str] = None,
    universe_slug: Optional[str] = None,
    tickers: Optional[list[str]] = None,
    csv_dir: Optional[str] = None,
    ascending: bool = False,
) -> pd.DataFrame:
    """
    Rank stocks by applying a ratio function to each ticker from a universe.

    Tickers come from (in order): tickers list, universe_slug (DB then CSV), or csv_path.

    Args:
        ratio_fn: Callable that takes a ticker string and returns a float or None.
        limit: If set, only consider the first this many tickers.
        min_value: If set, exclude tickers whose ratio is strictly less than this.
        csv_path: Path to CSV with a 'Symbol' column (used if universe_slug/tickers not used).
        universe_slug: e.g. 'sp500', 'nasdaq'; tickers loaded from DB then CSV fallback.
        tickers: If provided, use this list instead of loading from CSV/universe.
        csv_dir: Optional directory for CSV when using universe_slug fallback.
        ascending: If True, sort ascending (lowest ratio first); else descending.

    Returns:
        DataFrame with columns "ticker" and "ratio", sorted by "ratio".
    """
    if tickers is not None:
        tickers = list(tickers)
    elif universe_slug:
        tickers = get_tickers_for_universe(universe_slug, csv_dir=csv_dir)
    else:
        tickers = get_tickers_from_csv(csv_path or SP500_CSV_PATH)
    if limit:
        tickers = tickers[:limit]

    results: list[ScreenResult] = []

    # Try DB first when we have a universe and the ratio has a snapshot column
    ratio_name_for_db = getattr(ratio_fn, "__name__", None) or ""
    snapshot_col = RATIO_TO_SNAPSHOT_COLUMN.get(ratio_name_for_db) if ratio_name_for_db else None
    if universe_slug and snapshot_col:
        try:
            from db.snapshots import get_latest_ratios_by_universe
            db_pairs = get_latest_ratios_by_universe(universe_slug, snapshot_col)
            if db_pairs:
                for ticker, value in db_pairs:
                    if min_value is not None and value < min_value:
                        continue
                    results.append(ScreenResult(ticker, value))
                _log.info("Ratio ranking from DB: universe=%s ratio=%s count=%d", universe_slug, ratio_name_for_db, len(results))
        except Exception as e:
            _log.warning("Ratio ranking from DB failed (%s), falling back to yfinance", e)

    # Fall back to yfinance if no DB results
    if not results:
        for ticker in tickers:
            try:
                value = ratio_fn(ticker)
                if value is None:
                    continue
                value = float(value)
                if min_value is not None and value < min_value:
                    continue
                results.append(ScreenResult(ticker, value))
            except Exception:
                continue

    df = pd.DataFrame([{"ticker": r.ticker, "ratio": r.value} for r in results])
    df = df.sort_values("ratio", ascending=ascending).reset_index(drop=True)
    if limit:
        df = df.head(limit)
    return df
