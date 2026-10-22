"""Export S&P 400 (mid-cap) and S&P 600 (small-cap) constituent lists to CSV from Wikipedia."""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Literal

import pandas as pd
import requests

SPIndex = Literal["SP400", "SP600"]

WIKI_URLS: dict[str, str] = {
    "SP400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "SP600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
}

_SYMBOL_COLUMN_CANDIDATES = ("Symbol", "Ticker symbol", "Ticker")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def export_sp_index_symbols(
    index: SPIndex,
    out_csv_path: str | Path,
    *,
    timeout_s: int = 30,
) -> Path:
    """
    Scrape the Wikipedia constituents table for the S&P 400 or S&P 600 and write a
    CSV with one column 'Symbol'.

    Tickers with a dot (e.g. "BF.B") are normalized to a dash ("BF-B") to match
    yfinance/existing CSV convention (see sp500_symbols.csv's "BRK-B").

    Args:
        index: Either "SP400" or "SP600" (case-insensitive).
        out_csv_path: Path (or path string) where the CSV will be written. Parent dirs created.
        timeout_s: Request timeout in seconds. Default 30.

    Returns:
        The resolved Path where the CSV was written.

    Raises:
        ValueError: If index is not "SP400" or "SP600", or no constituents table is found.
        requests.HTTPError: On HTTP errors (e.g. 4xx/5xx) from Wikipedia.
    """
    index_upper = index.upper()
    if index_upper not in WIKI_URLS:
        raise ValueError("index must be either 'SP400' or 'SP600'")

    url = WIKI_URLS[index_upper]
    resp = requests.get(url, headers=_HEADERS, timeout=timeout_s)
    resp.raise_for_status()

    tables = pd.read_html(io.StringIO(resp.text))
    symbols_col = None
    for table in tables:
        for candidate in _SYMBOL_COLUMN_CANDIDATES:
            if candidate in table.columns:
                symbols_col = table[candidate]
                break
        if symbols_col is not None:
            break
    if symbols_col is None:
        raise ValueError(f"No constituents table with a Symbol-like column found at {url}")

    symbols = symbols_col.astype(str).str.strip().str.replace(".", "-", regex=False)

    seen: set[str] = set()
    out_symbols: list[str] = []
    for s in symbols:
        if s and s.lower() != "nan" and not (s in seen or seen.add(s)):
            out_symbols.append(s)

    out_csv_path = Path(out_csv_path)
    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with out_csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Symbol"])
        for s in out_symbols:
            w.writerow([s])

    return out_csv_path
