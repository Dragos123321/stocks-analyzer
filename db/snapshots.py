"""Write ticker snapshots and price history to the database."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from .connection import get_connection

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)


def clean_all_before_seed() -> None:
    """
    Delete all data from screening-related tables so seed can run as a full reload.
    Order respects foreign keys: universe_symbol, ticker_snapshot, price_history,
    financials, symbol, universe.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM universe_symbol")
            cur.execute("DELETE FROM ticker_snapshot")
            cur.execute("DELETE FROM price_history")
            try:
                cur.execute("DELETE FROM financials")
            except Exception:
                pass
            try:
                cur.execute("DELETE FROM eps_quarterly")
                cur.execute("DELETE FROM eps_annual")
            except Exception:
                pass
            cur.execute("DELETE FROM symbol")
            cur.execute("DELETE FROM universe")
    _log.info("DB: cleaned all tables (universe, symbol, universe_symbol, ticker_snapshot, price_history, financials)")


def clean_snapshot_and_history_for_symbols(symbol_ids: list[int]) -> None:
    """
    Delete ticker_snapshot, price_history, and EPS rows for the given symbol IDs.
    Use before reloading data for a universe so the reload is fresh.
    """
    if not symbol_ids:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ticker_snapshot WHERE symbol_id = ANY(%s)", (symbol_ids,))
            cur.execute("DELETE FROM price_history WHERE symbol_id = ANY(%s)", (symbol_ids,))
            try:
                cur.execute("DELETE FROM eps_quarterly WHERE symbol_id = ANY(%s)", (symbol_ids,))
                cur.execute("DELETE FROM eps_annual WHERE symbol_id = ANY(%s)", (symbol_ids,))
            except Exception:
                pass
    _log.info("DB: cleaned ticker_snapshot, price_history, eps for %d symbols", len(symbol_ids))


def update_symbol_name_sector(
    symbol_id: int,
    name: str | None = None,
    sector: str | None = None,
) -> None:
    """
    Update symbol name and/or sector. Only non-None values are applied;
    COALESCE is used so existing values are not overwritten with NULL.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE symbol
                SET name = COALESCE(%s, name),
                    sector = COALESCE(%s, sector),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (name, sector, symbol_id),
            )


def get_canslim_snapshots_for_universe(universe_slug: str) -> pd.DataFrame:
    """
    Return latest ticker_snapshot rows for the universe as a DataFrame with
    columns matching fetch_info_fields: ticker, earningsQuarterlyGrowth, earningsGrowth,
    returnOnEquity, price, 52WeekHigh, volume, avgVolume, heldPercentInstitutions.
    Returns empty DataFrame if no data or CANSLIM columns missing.
    """
    slug = universe_slug.strip().lower()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.ticker,
                    ts.earnings_growth, ts.earnings_growth,
                    ts.return_on_equity, ts.price, ts.fifty_two_week_high,
                    ts.volume, ts.avg_volume, ts.held_percent_institutions
                FROM symbol s
                JOIN universe_symbol us ON us.symbol_id = s.id
                JOIN universe u ON u.id = us.universe_id
                JOIN ticker_snapshot ts ON ts.symbol_id = s.id
                WHERE u.slug = %s
                  AND ts.snapshot_date = (
                    SELECT MAX(t2.snapshot_date)
                    FROM ticker_snapshot t2
                    WHERE t2.symbol_id = s.id
                  )
                ORDER BY s.ticker
                """,
                (slug,),
            )
            rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "earningsQuarterlyGrowth", "earningsGrowth",
            "returnOnEquity", "price", "52WeekHigh",
            "volume", "avgVolume", "heldPercentInstitutions",
        ],
    )
    return df


def get_close_prices_for_universe(
    universe_slug: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """
    Return daily close prices for all symbols in the universe between start_date and end_date.
    DataFrame: index = date, columns = ticker, values = close.
    """
    slug = universe_slug.strip().lower()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ph.date, s.ticker, ph.close
                FROM price_history ph
                JOIN symbol s ON s.id = ph.symbol_id
                JOIN universe_symbol us ON us.symbol_id = s.id
                JOIN universe u ON u.id = us.universe_id
                WHERE u.slug = %s AND ph.date >= %s AND ph.date <= %s
                ORDER BY ph.date
                """,
                (slug, start_date, end_date),
            )
            rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "ticker", "close"])
    pivot = df.pivot(index="date", columns="ticker", values="close")
    pivot.index = pd.to_datetime(pivot.index)
    return pivot


def get_latest_debt_ratios_for_ticker(ticker: str) -> dict[str, float | None]:
    """
    Return latest current_ratio and quick_ratio for a ticker from ticker_snapshot.
    Keys: 'current_ratio', 'quick_ratio'. Value None if not in DB or missing.
    """
    ticker_upper = ticker.strip().upper()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT ts.current_ratio, ts.quick_ratio
                    FROM symbol s
                    JOIN ticker_snapshot ts ON ts.symbol_id = s.id
                    WHERE s.ticker = %s
                      AND ts.snapshot_date = (SELECT MAX(snapshot_date) FROM ticker_snapshot WHERE symbol_id = s.id)
                    LIMIT 1
                    """,
                    (ticker_upper,),
                )
            except Exception:
                return {"current_ratio": None, "quick_ratio": None}
            row = cur.fetchone()
    if not row:
        return {"current_ratio": None, "quick_ratio": None}
    return {
        "current_ratio": float(row[0]) if row[0] is not None else None,
        "quick_ratio": float(row[1]) if row[1] is not None else None,
    }


def get_latest_valuation_ratios_for_ticker(ticker: str) -> dict[str, float | None]:
    """
    Return latest P/E, PEG, Price/Sales, EV/EBITDA for a ticker from ticker_snapshot.
    Keys: 'pe', 'peg', 'price_to_sales', 'ev_to_ebitda'. Value None if not in DB or missing.
    """
    ticker_upper = ticker.strip().upper()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ts.trailing_pe, ts.peg_ratio, ts.price_to_sales, ts.ev_to_ebitda
                FROM symbol s
                JOIN ticker_snapshot ts ON ts.symbol_id = s.id
                WHERE s.ticker = %s
                  AND ts.snapshot_date = (SELECT MAX(snapshot_date) FROM ticker_snapshot WHERE symbol_id = s.id)
                LIMIT 1
                """,
                (ticker_upper,),
            )
            row = cur.fetchone()
    if not row:
        return {"pe": None, "peg": None, "price_to_sales": None, "ev_to_ebitda": None}
    return {
        "pe": float(row[0]) if row[0] is not None else None,
        "peg": float(row[1]) if row[1] is not None else None,
        "price_to_sales": float(row[2]) if row[2] is not None else None,
        "ev_to_ebitda": float(row[3]) if row[3] is not None else None,
    }


def get_latest_valuation_fields_for_ticker(ticker: str) -> dict[str, float | None]:
    """
    Return latest cached fair-value/investment-score inputs for a ticker from
    ticker_snapshot, shaped like a subset of yfinance's .info dict so it can be fed
    straight into the valuation/ functions: trailingEps, bookValue, freeCashflow,
    sharesOutstanding, earningsGrowth, earningsQuarterlyGrowth, regularMarketPrice,
    heldPercentInstitutions, debtToEquity, dividendRate.

    All values None if no snapshot row exists, or if columns predate the relevant
    migration (see scripts/migrate_add_valuation_columns.py).
    """
    ticker_upper = ticker.strip().upper()
    empty = {
        "trailingEps": None,
        "bookValue": None,
        "freeCashflow": None,
        "sharesOutstanding": None,
        "earningsGrowth": None,
        "earningsQuarterlyGrowth": None,
        "regularMarketPrice": None,
        "heldPercentInstitutions": None,
        "debtToEquity": None,
        "dividendRate": None,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT ts.trailing_eps, ts.book_value_per_share, ts.free_cashflow,
                        ts.shares_outstanding, ts.earnings_growth, ts.price,
                        ts.held_percent_institutions, ts.debt_to_equity, ts.dividend_rate
                    FROM symbol s
                    JOIN ticker_snapshot ts ON ts.symbol_id = s.id
                    WHERE s.ticker = %s
                      AND ts.snapshot_date = (SELECT MAX(snapshot_date) FROM ticker_snapshot WHERE symbol_id = s.id)
                    LIMIT 1
                    """,
                    (ticker_upper,),
                )
                row = cur.fetchone()
            except Exception:
                return empty
    if not row:
        return empty
    growth = float(row[4]) if row[4] is not None else None
    return {
        "trailingEps": float(row[0]) if row[0] is not None else None,
        "bookValue": float(row[1]) if row[1] is not None else None,
        "freeCashflow": float(row[2]) if row[2] is not None else None,
        "sharesOutstanding": float(row[3]) if row[3] is not None else None,
        "earningsGrowth": growth,
        "earningsQuarterlyGrowth": growth,
        "regularMarketPrice": float(row[5]) if row[5] is not None else None,
        "heldPercentInstitutions": float(row[6]) if row[6] is not None else None,
        "debtToEquity": float(row[7]) if row[7] is not None else None,
        "dividendRate": float(row[8]) if row[8] is not None else None,
    }


def get_valuation_snapshots_for_universe(universe_slug: str) -> pd.DataFrame:
    """
    Return latest cached fair-value inputs for every symbol in the universe, shaped
    like yfinance .info keys (see get_latest_valuation_fields_for_ticker), so a
    fair-value ranking over an entire universe can run from one query instead of
    one yfinance call per ticker. Returns empty DataFrame if no data.
    """
    slug = universe_slug.strip().lower()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.ticker, ts.trailing_eps, ts.book_value_per_share, ts.free_cashflow,
                    ts.shares_outstanding, ts.earnings_growth, ts.price,
                    ts.held_percent_institutions, ts.debt_to_equity, ts.dividend_rate
                FROM symbol s
                JOIN universe_symbol us ON us.symbol_id = s.id
                JOIN universe u ON u.id = us.universe_id
                JOIN ticker_snapshot ts ON ts.symbol_id = s.id
                WHERE u.slug = %s
                  AND ts.snapshot_date = (
                    SELECT MAX(t2.snapshot_date)
                    FROM ticker_snapshot t2
                    WHERE t2.symbol_id = s.id
                  )
                ORDER BY s.ticker
                """,
                (slug,),
            )
            rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=[
            "ticker", "trailingEps", "bookValue", "freeCashflow",
            "sharesOutstanding", "earningsGrowth", "regularMarketPrice",
            "heldPercentInstitutions", "debtToEquity", "dividendRate",
        ],
    )
    df["earningsQuarterlyGrowth"] = df["earningsGrowth"]
    # SQL NULL becomes NaN in numeric columns; normalize back to None so downstream
    # "is None" / "<= 0" checks in valuation/ treat missing data as missing, not as
    # a NaN value that silently passes those comparisons.
    df = df.where(pd.notna(df), None)
    return df


def get_latest_ratios_by_universe(
    universe_slug: str,
    ratio_column: str,
) -> list[tuple[str, float]]:
    """
    Return (ticker, ratio_value) for the latest ticker_snapshot per symbol in the universe.
    ratio_column: one of 'trailing_pe', 'peg_ratio', 'price_to_sales', 'ev_to_ebitda'.
    Returns empty list if no data or invalid column.
    """
    allowed = {"trailing_pe", "peg_ratio", "price_to_sales", "ev_to_ebitda"}
    if ratio_column not in allowed:
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT s.ticker, ts.{ratio_column}
                FROM symbol s
                JOIN universe_symbol us ON us.symbol_id = s.id
                JOIN universe u ON u.id = us.universe_id
                JOIN ticker_snapshot ts ON ts.symbol_id = s.id
                WHERE u.slug = %s
                  AND ts.{ratio_column} IS NOT NULL
                  AND ts.snapshot_date = (
                    SELECT MAX(t2.snapshot_date)
                    FROM ticker_snapshot t2
                    WHERE t2.symbol_id = s.id
                  )
                ORDER BY s.ticker
                """,
                (universe_slug.strip().lower(),),
            )
            rows = cur.fetchall()
    return [(r[0], float(r[1])) for r in rows if r[1] is not None]


def get_symbol_ids_for_tickers(tickers: list[str]) -> dict[str, int]:
    """
    Return mapping ticker -> symbol_id for tickers that exist in the symbol table.
    """
    if not tickers:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ticker FROM symbol WHERE ticker = ANY(%s)",
                (tickers,),
            )
            return {row[1]: row[0] for row in cur.fetchall()}


def upsert_ticker_snapshot(
    symbol_id: int,
    snapshot_date: date,
    *,
    market_cap: int | None = None,
    trailing_pe: float | None = None,
    forward_pe: float | None = None,
    peg_ratio: float | None = None,
    price_to_sales: float | None = None,
    ev_to_ebitda: float | None = None,
    earnings_growth: float | None = None,
    revenue_growth: float | None = None,
    return_on_equity: float | None = None,
    price: float | None = None,
    fifty_two_week_high: float | None = None,
    volume: int | None = None,
    avg_volume: int | None = None,
    held_percent_institutions: float | None = None,
    current_ratio: float | None = None,
    quick_ratio: float | None = None,
    trailing_eps: float | None = None,
    book_value_per_share: float | None = None,
    free_cashflow: float | None = None,
    shares_outstanding: float | None = None,
    dividend_rate: float | None = None,
    debt_to_equity: float | None = None,
) -> None:
    """Insert or update a single ticker_snapshot row (unique on symbol_id, snapshot_date)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ticker_snapshot (
                    symbol_id, snapshot_date,
                    market_cap, trailing_pe, forward_pe, peg_ratio,
                    price_to_sales, ev_to_ebitda, earnings_growth, revenue_growth,
                    return_on_equity, price, fifty_two_week_high, volume, avg_volume, held_percent_institutions,
                    current_ratio, quick_ratio,
                    trailing_eps, book_value_per_share, free_cashflow, shares_outstanding,
                    dividend_rate, debt_to_equity
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (symbol_id, snapshot_date) DO UPDATE SET
                    market_cap = EXCLUDED.market_cap,
                    trailing_pe = EXCLUDED.trailing_pe,
                    forward_pe = EXCLUDED.forward_pe,
                    peg_ratio = EXCLUDED.peg_ratio,
                    price_to_sales = EXCLUDED.price_to_sales,
                    ev_to_ebitda = EXCLUDED.ev_to_ebitda,
                    earnings_growth = EXCLUDED.earnings_growth,
                    revenue_growth = EXCLUDED.revenue_growth,
                    return_on_equity = EXCLUDED.return_on_equity,
                    price = EXCLUDED.price,
                    fifty_two_week_high = EXCLUDED.fifty_two_week_high,
                    volume = EXCLUDED.volume,
                    avg_volume = EXCLUDED.avg_volume,
                    held_percent_institutions = EXCLUDED.held_percent_institutions,
                    current_ratio = EXCLUDED.current_ratio,
                    quick_ratio = EXCLUDED.quick_ratio,
                    trailing_eps = EXCLUDED.trailing_eps,
                    book_value_per_share = EXCLUDED.book_value_per_share,
                    free_cashflow = EXCLUDED.free_cashflow,
                    shares_outstanding = EXCLUDED.shares_outstanding,
                    dividend_rate = EXCLUDED.dividend_rate,
                    debt_to_equity = EXCLUDED.debt_to_equity
                """,
                (
                    symbol_id, snapshot_date,
                    market_cap, trailing_pe, forward_pe, peg_ratio,
                    price_to_sales, ev_to_ebitda, earnings_growth, revenue_growth,
                    return_on_equity, price, fifty_two_week_high, volume, avg_volume, held_percent_institutions,
                    current_ratio, quick_ratio,
                    trailing_eps, book_value_per_share, free_cashflow, shares_outstanding,
                    dividend_rate, debt_to_equity,
                ),
            )


def upsert_price_history(
    symbol_id: int,
    rows: list[tuple[date, float, float, float, float, int]],
) -> None:
    """
    Insert or update price_history rows for a symbol.
    Each row is (date, open, high, low, close, volume).
    Uses ON CONFLICT (symbol_id, date) DO UPDATE.
    """
    if not rows:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            for d, o, h, l, c, v in rows:
                cur.execute(
                    """
                    INSERT INTO price_history (symbol_id, date, open, high, low, close, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol_id, date) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume
                    """,
                    (symbol_id, d, o, h, l, c, v),
                )


def upsert_eps_annual(symbol_id: int, fiscal_year: int, diluted_eps: float) -> None:
    """Insert or update annual diluted EPS for a symbol and fiscal year."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO eps_annual (symbol_id, fiscal_year, diluted_eps)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (symbol_id, fiscal_year) DO UPDATE SET diluted_eps = EXCLUDED.diluted_eps
                    """,
                    (symbol_id, fiscal_year, diluted_eps),
                )
            except Exception:
                pass


def upsert_eps_quarterly(symbol_id: int, period_end: date, diluted_eps: float) -> None:
    """Insert or update quarterly diluted EPS for a symbol and period-end date."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO eps_quarterly (symbol_id, period_end, diluted_eps)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (symbol_id, period_end) DO UPDATE SET diluted_eps = EXCLUDED.diluted_eps
                    """,
                    (symbol_id, period_end, diluted_eps),
                )
            except Exception:
                pass


def get_annual_eps_from_db(ticker: str, fiscal_year: int) -> float | None:
    """Return diluted EPS for ticker and fiscal year from eps_annual, or None."""
    ticker_upper = ticker.strip().upper()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT ea.diluted_eps
                    FROM eps_annual ea
                    JOIN symbol s ON s.id = ea.symbol_id
                    WHERE s.ticker = %s AND ea.fiscal_year = %s
                    LIMIT 1
                    """,
                    (ticker_upper, fiscal_year),
                )
                row = cur.fetchone()
            except Exception:
                return None
    return float(row[0]) if row and row[0] is not None else None


def get_quarterly_eps_from_db(ticker: str, period_end: date) -> float | None:
    """Return diluted EPS for ticker and period-end from eps_quarterly, or None."""
    ticker_upper = ticker.strip().upper()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT eq.diluted_eps
                    FROM eps_quarterly eq
                    JOIN symbol s ON s.id = eq.symbol_id
                    WHERE s.ticker = %s AND eq.period_end = %s
                    LIMIT 1
                    """,
                    (ticker_upper, period_end),
                )
                row = cur.fetchone()
            except Exception:
                return None
    return float(row[0]) if row and row[0] is not None else None


def get_quarterly_eps_by_calendar_from_db(ticker: str, calendar_year: int, quarter: str) -> float | None:
    """Return diluted EPS for ticker for the given calendar year and quarter (Q1–Q4). Uses latest period_end in that quarter."""
    q = quarter.upper()
    if q not in ("Q1", "Q2", "Q3", "Q4"):
        return None
    month_ranges = {"Q1": (1, 3), "Q2": (4, 6), "Q3": (7, 9), "Q4": (10, 12)}
    start_m, end_m = month_ranges[q]
    ticker_upper = ticker.strip().upper()
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT eq.diluted_eps
                    FROM eps_quarterly eq
                    JOIN symbol s ON s.id = eq.symbol_id
                    WHERE s.ticker = %s
                      AND EXTRACT(YEAR FROM eq.period_end) = %s
                      AND EXTRACT(MONTH FROM eq.period_end) BETWEEN %s AND %s
                    ORDER BY eq.period_end DESC
                    LIMIT 1
                    """,
                    (ticker_upper, calendar_year, start_m, end_m),
                )
                row = cur.fetchone()
            except Exception:
                return None
    return float(row[0]) if row and row[0] is not None else None


def _safe_float(info: dict[str, Any], key: str, alt_key: str | None = None) -> float | None:
    v = info.get(key) or (info.get(alt_key) if alt_key else None)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(info: dict[str, Any], key: str) -> int | None:
    v = info.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
