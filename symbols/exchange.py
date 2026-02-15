"""Export exchange symbol lists (NYSE, NASDAQ) to CSV from Nasdaq Trader data."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Literal

import requests

Market = Literal["NYSE", "NASDAQ"]


def export_exchange_symbols(
    market: Market,
    out_csv_path: str | Path,
    *,
    include_etfs: bool = True,
    timeout_s: int = 30,
) -> Path:
    """
    Download exchange symbol list from Nasdaq Trader and write a CSV with one column 'Symbol'.

    For NASDAQ uses nasdaqlisted.txt; for NYSE uses otherlisted.txt filtered to
    Exchange == 'N'. Excludes test issues (Test Issue == 'Y'). Optionally excludes ETFs.

    Args:
        market: Either "NYSE" or "NASDAQ" (case-insensitive).
        out_csv_path: Path (or path string) where the CSV will be written. Parent dirs created.
        include_etfs: If True, include ETFs; if False, exclude them. Default True.
        timeout_s: Request timeout in seconds. Default 30.

    Returns:
        The resolved Path where the CSV was written.

    Raises:
        ValueError: If market is not "NYSE" or "NASDAQ".
        requests.HTTPError: On HTTP errors (e.g. 4xx/5xx) from Nasdaq Trader.
    """
    market = market.upper()
    if market not in ("NYSE", "NASDAQ"):
        raise ValueError("market must be either 'NYSE' or 'NASDAQ'")

    out_csv_path = Path(out_csv_path)

    if market == "NASDAQ":
        url = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
        symbol_idx = 0
        test_issue_idx = 3
        etf_idx = 6

        def row_filter(fields: list[str]) -> bool:
            """Keep row if not test issue and (ETFs allowed or row is not ETF)."""
            if len(fields) <= max(test_issue_idx, etf_idx, symbol_idx):
                return False
            if fields[test_issue_idx].strip() != "N":
                return False
            if not include_etfs and fields[etf_idx].strip() == "Y":
                return False
            return True

        def symbol_from(fields: list[str]) -> str:
            """Extract symbol string from pipe-delimited fields for NASDAQ."""
            return fields[symbol_idx].strip()

    else:
        url = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
        exchange_idx = 2
        cqs_symbol_idx = 3
        test_issue_idx = 6
        etf_idx = 4

        def row_filter(fields: list[str]) -> bool:
            """Keep row if NYSE (N), not test issue, and (ETFs allowed or not ETF)."""
            if len(fields) <= max(exchange_idx, cqs_symbol_idx, test_issue_idx, etf_idx):
                return False
            if fields[exchange_idx].strip() != "N":
                return False
            if fields[test_issue_idx].strip() != "N":
                return False
            if not include_etfs and fields[etf_idx].strip() == "Y":
                return False
            return True

        def symbol_from(fields: list[str]) -> str:
            """Extract CQS symbol from pipe-delimited fields for NYSE."""
            return fields[cqs_symbol_idx].strip()

    resp = requests.get(url, timeout=timeout_s)
    resp.raise_for_status()
    text = resp.text

    symbols: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("File Creation Time"):
            break
        if line.startswith("Symbol|") or line.startswith("ACT Symbol|"):
            continue
        fields = line.split("|")
        if row_filter(fields):
            sym = symbol_from(fields)
            if sym:
                symbols.append(sym)

    seen: set[str] = set()
    symbols = [s for s in symbols if not (s in seen or seen.add(s))]

    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with out_csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Symbol"])
        for s in symbols:
            w.writerow([s])

    return out_csv_path
