"""Rank stocks by a ratio function (e.g. P/E, Price/Sales) from CSV ticker lists."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

SP500_CSV_PATH = "sp500_symbols.csv"


@dataclass
class ScreenResult:
    """Single result from a ratio screen: ticker and its ratio value."""

    ticker: str
    value: float


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
    df = pd.read_csv(path)
    if "Symbol" not in df.columns:
        raise ValueError("CSV must contain a 'Symbol' column")
    return df["Symbol"].astype(str).str.strip().tolist()


def rank_stocks_by_ratio(
    ratio_fn: Callable[[str], Optional[float]],
    *,
    limit: Optional[int] = None,
    min_value: Optional[float] = None,
    csv_path: str = SP500_CSV_PATH,
    ascending: bool = False,
) -> pd.DataFrame:
    """
    Rank stocks by applying a ratio function to each ticker from a CSV universe.

    Loads tickers from csv_path, evaluates ratio_fn(ticker) for each, filters out
    None and (optionally) values below min_value, then sorts by the ratio.

    Args:
        ratio_fn: Callable that takes a ticker string and returns a float or None.
        limit: If set, only consider the first this many tickers from the CSV.
        min_value: If set, exclude tickers whose ratio is strictly less than this.
        csv_path: Path to CSV with a 'Symbol' column. Defaults to SP500_CSV_PATH.
        ascending: If True, sort ascending (lowest ratio first); else descending.

    Returns:
        DataFrame with columns "ticker" and "ratio", sorted by "ratio".
        Tickers that raise or return None are skipped (no row).
    """
    tickers = get_tickers_from_csv(csv_path)
    if limit:
        tickers = tickers[:limit]

    results: list[ScreenResult] = []
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
    return df
