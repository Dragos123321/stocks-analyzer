"""Diluted EPS: annual and quarterly. Use DB when available, else yfinance."""
from __future__ import annotations

import pandas as pd
import yfinance as yf


def _annual_eps_from_db(ticker_symbol: str, year: int) -> float | None:
    try:
        from db.snapshots import get_annual_eps_from_db
        return get_annual_eps_from_db(ticker_symbol, year)
    except Exception:
        return None


def annual_eps(ticker_symbol: str, year: int) -> float | None:
    """
    Return diluted EPS for the given ticker and fiscal year.
    Uses DB (eps_annual) when available, else yfinance.
    """
    val = _annual_eps_from_db(ticker_symbol, year)
    if val is not None:
        return val
    t = yf.Ticker(ticker_symbol)
    income_stmt = t.income_stmt
    if income_stmt is None or income_stmt.empty:
        return None
    year_dates = [d for d in income_stmt.columns if getattr(d, "year", None) == year]
    if not year_dates:
        return None
    col = year_dates[0]
    return _compute_diluted_eps(income_stmt, col)


def _quarterly_eps_from_db(ticker_symbol: str, year: int, quarter: str) -> float | None:
    try:
        from db.snapshots import get_quarterly_eps_by_calendar_from_db
        return get_quarterly_eps_by_calendar_from_db(ticker_symbol, year, quarter)
    except Exception:
        return None


def quarterly_eps(ticker_symbol: str, year: int, quarter: str) -> float | None:
    """
    Return diluted EPS for the given ticker, fiscal year, and quarter.
    Uses DB (eps_quarterly) when available, else yfinance.
    """
    q = quarter.upper()
    if q not in ("Q1", "Q2", "Q3", "Q4"):
        return None
    val = _quarterly_eps_from_db(ticker_symbol, year, q)
    if val is not None:
        return val
    t = yf.Ticker(ticker_symbol)
    qis = t.quarterly_income_stmt
    if qis is None or qis.empty:
        return None
    col = _find_quarter_column(qis, fiscal_year=year, quarter_label=q)
    if col is None:
        return None
    return _compute_diluted_eps(qis, col)


def eps_change_pct(ticker_symbol: str, year_start: int, year_end: int) -> float | None:
    """
    Return the percentage change in diluted EPS from year_start to year_end.

    Formula: (EPS_year_end - EPS_year_start) / EPS_year_start * 100.

    Args:
        ticker_symbol: Stock ticker symbol (e.g. "AAPL").
        year_start: Starting fiscal year for the comparison.
        year_end: Ending fiscal year for the comparison.

    Returns:
        Percentage change in diluted EPS, or None if either year's EPS is
        missing or if year_start EPS is zero (to avoid division by zero).
    """
    eps_start = annual_eps(ticker_symbol, year_start)
    eps_end = annual_eps(ticker_symbol, year_end)
    if eps_start is None or eps_end is None or eps_start == 0:
        return None
    return (eps_end - eps_start) / eps_start * 100


def _find_quarter_column(
    df: pd.DataFrame,
    fiscal_year: int,
    quarter_label: str,
):
    """
    Map a (year, quarter_label) to the DataFrame column for that calendar quarter.

    Columns are assumed to be period-end dates. Selects the latest date whose
    calendar year equals fiscal_year and whose month is in the quarter's range
    (Q1: 1–3, Q2: 4–6, Q3: 7–9, Q4: 10–12).

    Args:
        df: Quarterly income statement (or similar) with date-like columns.
        fiscal_year: Calendar year to match (e.g. 2025).
        quarter_label: Quarter label "Q1", "Q2", "Q3", or "Q4".

    Returns:
        The column (period-end) for that quarter, or None if no matching column.
    """
    # Normalise to upper just in case
    q = quarter_label.upper()
    if q not in ("Q1", "Q2", "Q3", "Q4"):
        return None

    # Calendar month ranges per quarter
    quarter_months = {
        "Q1": (1, 3),
        "Q2": (4, 6),
        "Q3": (7, 9),
        "Q4": (10, 12),
    }
    start_m, end_m = quarter_months[q]

    candidates = []
    for d in df.columns:
        year = getattr(d, "year", None)
        month = getattr(d, "month", None)
        if year is None or month is None:
            continue
        if year == fiscal_year and start_m <= month <= end_m:
            candidates.append(d)

    if not candidates:
        return None

    # Use the latest quarter-end in that window
    return max(candidates)


def _compute_diluted_eps(df: pd.DataFrame, col) -> float | None:
    """
    Compute diluted EPS for a single period column from an income statement DataFrame.

    First tries the reported Diluted EPS row; if missing, uses
    (Net Income - Preferred Dividends) / (Weighted Avg. Common + Dilutive Shares).

    Args:
        df: Income statement DataFrame with index rows (e.g. "Net Income", "Diluted EPS").
        col: Column (period-end) for which to compute EPS.

    Returns:
        Diluted EPS value for that column, or None if required data is missing.
    """
    # 1) Reported diluted EPS, if available
    reported_eps = _get_value(df, col, ("Diluted EPS", "DilutedEps"))
    if reported_eps is not None:
        return reported_eps

    # 2) Formula-based diluted EPS
    net_income = _get_value(df, col, ("Net Income", "NetIncome"))
    if net_income is None:
        return None

    preferred_dividends = _get_value(
        df,
        col,
        ("Preferred Dividends", "Preferred Stock Dividends", "PreferredDividends"),
        default=0.0,
    )
    numerator = net_income - preferred_dividends

    # Denominator: Weighted Avg. Common Shares + Dilutive Potential Shares
    diluted_shares = _get_value(
        df,
        col,
        (
            "Diluted Average Shares",
            "DilutedAverageShares",
            "Weighted Average Shares",
            "WeightedAverageShares",
        ),
    )
    if diluted_shares is not None and diluted_shares > 0:
        denominator = diluted_shares
    else:
        basic = _get_value(
            df,
            col,
            ("Basic Average Shares", "BasicAverageShares", "Weighted Average Shares", "WeightedAverageShares"),
        )
        dilutive = _get_value(
            df,
            col,
            ("Dilutive Potential Shares", "DilutivePotentialShares", "Dilutive Securities", "DilutiveSecurities"),
            default=0.0,
        )
        if basic is not None and (basic + dilutive) > 0:
            denominator = basic + dilutive
        else:
            return None

    if denominator == 0:
        return None

    return float(numerator / denominator)


def _get_value(df: pd.DataFrame, col, row_names: tuple, default=None):
    """
    Return the first non-null value from the DataFrame for the given column and row names.

    Tries each name in row_names (e.g. ("Net Income", "NetIncome")) until a
    non-null value is found in df.loc[name, col].

    Args:
        df: DataFrame with index containing row names (e.g. financial statement line items).
        col: Column (e.g. period-end date) to read from.
        row_names: Tuple of alternative index names to try, in order.
        default: Value to return if no non-null value is found. Defaults to None.

    Returns:
        The first non-null value as float, or default.
    """
    for name in row_names:
        if name in df.index:
            val = df.loc[name, col]
            if pd.notna(val):
                return float(val)
    return default
