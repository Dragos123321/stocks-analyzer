"""CANSLIM-style ranking: use DB when available, else fetch from yfinance."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from .ratio_ranking import get_tickers_for_universe, get_tickers_from_csv

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)


def _minmax_0_100(s: pd.Series) -> pd.Series:
    """
    Min-max normalize a series to the [0, 100] range.

    Converts to numeric, then maps min -> 0 and max -> 100. Returns NaN series
    if all values are NaN or if min == max.

    Args:
        s: Input series (e.g. earnings growth, relative strength).

    Returns:
        Series with same index; values in [0, 100] or NaN where undefined.
    """
    s = pd.to_numeric(s, errors="coerce")
    lo = s.min(skipna=True)
    hi = s.max(skipna=True)
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return pd.Series(np.nan, index=s.index)
    return 100.0 * (s - lo) / (hi - lo)


def _clip01(x: float | None) -> float | None:
    """
    Clip a numeric value to [0.0, 1.0], or return None for non-numeric/None.

    Args:
        x: Input value (e.g. institutional ownership as a decimal).

    Returns:
        float in [0, 1], or None if x is None or not convertible to float.
    """
    if x is None:
        return None
    try:
        x = float(x)
    except Exception:
        return None
    return max(0.0, min(1.0, x))


def market_is_up(symbol: str = "SPY", lookback_days: int = 365) -> bool:
    """
    Return whether the market (benchmark) is in an uptrend.

    Uses the rule: 50-day simple moving average > 200-day SMA. If there are
    fewer than 220 trading days of data, returns True (assume uptrend).

    Args:
        symbol: Benchmark ticker (default "SPY").
        lookback_days: Number of calendar days of history to download (default 365).

    Returns:
        True if 50-day MA > 200-day MA, or if insufficient data; False otherwise.
    """
    data = yf.download(
        symbol,
        period=f"{lookback_days}d",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
    )
    close = data["Close"]
    if isinstance(close, pd.DataFrame):
        if symbol in close.columns:
            close = close[symbol]
        else:
            close = close.iloc[:, 0]
    close = close.dropna()
    if len(close) < 220:
        return True
    ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    return bool(ma50 > ma200)


def fetch_info_fields(tickers: list[str]) -> pd.DataFrame:
    """
    Fetch CANSLIM-related fields from yfinance for a list of tickers.

    Retrieves earnings growth, ROE, price, 52-week high, volume, and
    institutional ownership for use in CANSLIM scoring.

    Args:
        tickers: List of stock ticker symbols (e.g. ["AAPL", "NVDA"]).

    Returns:
        DataFrame with one row per ticker and columns: ticker,
        earningsQuarterlyGrowth, earningsGrowth, returnOnEquity, price,
        52WeekHigh, volume, avgVolume, heldPercentInstitutions.
        Missing values are NaN.
    """
    rows = []
    for t in tickers:
        try:
            info = yf.Ticker(t).info or {}
        except Exception:
            info = {}
        rows.append({
            "ticker": t,
            "earningsQuarterlyGrowth": info.get("earningsQuarterlyGrowth"),
            "earningsGrowth": info.get("earningsGrowth"),
            "returnOnEquity": info.get("returnOnEquity"),
            "price": info.get("regularMarketPrice"),
            "52WeekHigh": info.get("fiftyTwoWeekHigh"),
            "volume": info.get("volume"),
            "avgVolume": info.get("averageVolume"),
            "heldPercentInstitutions": info.get("heldPercentInstitutions"),
        })
    return pd.DataFrame(rows)


def fetch_prices_for_rs(
    tickers: list[str], benchmark: str = "SPY", period: str = "1y"
) -> pd.DataFrame:
    """
    Download daily close prices for tickers and benchmark for relative strength.

    Used to compute 6-month price return vs benchmark (CANSLIM "L" factor).

    Args:
        tickers: List of stock ticker symbols.
        benchmark: Benchmark ticker for relative strength (default "SPY").
        period: yfinance period string (default "1y").

    Returns:
        DataFrame with columns = tickers + benchmark, index = date, values = close.
        Drops rows where all values are NaN.
    """
    all_syms = tickers + [benchmark]
    data = yf.download(
        all_syms, period=period, interval="1d",
        group_by="ticker", auto_adjust=True, progress=False
    )
    closes = {}
    if isinstance(data.columns, pd.MultiIndex):
        for sym in all_syms:
            if (sym, "Close") in data.columns:
                closes[sym] = data[(sym, "Close")]
    else:
        closes[all_syms[0]] = data["Close"]
    return pd.DataFrame(closes).dropna(how="all")


def rank_stocks_by_canslim(
    csv_path: str = "sp500_symbols.csv",
    *,
    universe_slug: str | None = None,
    tickers: list[str] | None = None,
    limit: int | None = None,
    weights: dict[str, float] | None = None,
    benchmark: str = "SPY",
    csv_dir: str | None = None,
) -> pd.DataFrame:
    """
    Rank stocks by a CANSLIM-style composite score from a ticker universe.

    Scores each ticker on C (earnings growth), A (annual growth/ROE), N (near high),
    S (volume surge), L (relative strength), I (institutional), M (market trend).
    Only tickers with at least 5 of 6 letter scores are included. Results are
    sorted by total score descending.

    Args:
        csv_path: Path to CSV with 'Symbol' column (used if universe_slug/tickers not set).
        universe_slug: e.g. 'sp500', 'nasdaq'; tickers from DB then CSV fallback.
        tickers: If provided, use this list instead of loading from CSV/universe.
        limit: If set, only evaluate the first this many tickers.
        weights: Optional dict of letter -> weight (e.g. {"C": 20, "A": 15, ...}).
        benchmark: Ticker for market trend and relative strength (default "SPY").
        csv_dir: Optional directory for CSV when using universe_slug fallback.

    Returns:
        DataFrame with columns: ticker, C, A, N, S, L, I, M, coverage_count,
        score_core, score. Sorted by score descending.
    """
    if tickers is not None:
        tickers = list(tickers)
    elif universe_slug:
        tickers = get_tickers_for_universe(universe_slug, csv_dir=csv_dir)
    else:
        tickers = get_tickers_from_csv(csv_path)
    if limit:
        tickers = tickers[:limit]

    m_ok = market_is_up(benchmark)

    # Try DB first when we have a universe (needs CANSLIM columns in ticker_snapshot)
    info_df = None
    prices = None
    if universe_slug:
        try:
            from db.snapshots import get_canslim_snapshots_for_universe, get_close_prices_for_universe
            info_df = get_canslim_snapshots_for_universe(universe_slug)
            if not info_df.empty and "returnOnEquity" in info_df.columns:
                end_d = date.today()
                start_d = end_d - timedelta(days=400)
                prices = get_close_prices_for_universe(universe_slug, start_d, end_d)
                if not prices.empty and len(prices) >= 130:
                    spy_series = yf.download(
                        benchmark, start=start_d, end=end_d, progress=False,
                        auto_adjust=True, group_by="column",
                    )
                    if not spy_series.empty and "Close" in spy_series.columns:
                        spy_close = spy_series["Close"]
                        if isinstance(spy_close, pd.DataFrame):
                            spy_close = spy_close.iloc[:, 0]
                        spy_aligned = spy_close.reindex(prices.index)
                        prices[benchmark] = spy_aligned.values
                    if benchmark in prices.columns:
                        _log.info("CANSLIM from DB: universe=%s tickers=%d", universe_slug, len(info_df))
        except Exception as e:
            _log.warning("CANSLIM from DB failed (%s), falling back to yfinance", e)
            info_df = None
            prices = None

    if info_df is None or info_df.empty or prices is None or prices.empty or benchmark not in prices.columns:
        info_df = fetch_info_fields(tickers)
        prices = fetch_prices_for_rs(tickers, benchmark=benchmark, period="1y")

    if benchmark in prices.columns:
        spy = prices[benchmark].dropna()
    else:
        spy = pd.Series(dtype=float)

    rs = pd.Series(index=info_df["ticker"], dtype=float)
    if not spy.empty:
        for t in info_df["ticker"]:
            if t not in prices.columns:
                continue
            s = prices[t].dropna()
            if len(s) < 130 or len(spy) < 130:
                continue
            r_stock = (s.iloc[-1] / s.iloc[-126]) - 1.0
            r_spy = (spy.iloc[-1] / spy.iloc[-126]) - 1.0
            rs.loc[t] = r_stock - r_spy
    info_df["relStrength"] = rs.values

    info_df["nearHigh"] = np.where(
        (pd.to_numeric(info_df["price"], errors="coerce") > 0)
        & (pd.to_numeric(info_df["52WeekHigh"], errors="coerce") > 0),
        pd.to_numeric(info_df["price"], errors="coerce")
        / pd.to_numeric(info_df["52WeekHigh"], errors="coerce"),
        np.nan,
    )
    info_df["nearHigh"] = info_df["nearHigh"].clip(lower=0, upper=1)

    info_df["volSurge"] = np.where(
        (pd.to_numeric(info_df["volume"], errors="coerce") > 0)
        & (pd.to_numeric(info_df["avgVolume"], errors="coerce") > 0),
        pd.to_numeric(info_df["volume"], errors="coerce")
        / pd.to_numeric(info_df["avgVolume"], errors="coerce"),
        np.nan,
    )

    info_df["inst"] = info_df["heldPercentInstitutions"].apply(_clip01)

    c_score = _minmax_0_100(info_df["earningsQuarterlyGrowth"])
    a_primary = pd.to_numeric(info_df["earningsGrowth"], errors="coerce")
    a_fallback = pd.to_numeric(info_df["returnOnEquity"], errors="coerce")
    a_mix = a_primary.copy()
    a_mix[a_mix.isna()] = a_fallback[a_mix.isna()]
    a_score = _minmax_0_100(a_mix)

    n_score = _minmax_0_100(info_df["nearHigh"])
    s_score = _minmax_0_100(info_df["volSurge"])
    l_score = _minmax_0_100(info_df["relStrength"])
    i_score = _minmax_0_100(info_df["inst"])
    m_score = 100.0 if m_ok else 0.0

    out = info_df[["ticker"]].copy()
    out["C"] = c_score
    out["A"] = a_score
    out["N"] = n_score
    out["S"] = s_score
    out["L"] = l_score
    out["I"] = i_score
    out["M"] = m_score

    if weights is None:
        weights = {"C": 20, "A": 15, "N": 15, "S": 10, "L": 25, "I": 10, "M": 5}
    w_sum = sum(weights.values())
    w = {k: v / w_sum for k, v in weights.items()}

    letters = ["C", "A", "N", "S", "L", "I"]
    out["coverage_count"] = out[letters].notna().sum(axis=1)
    min_letters_required = 5
    out = out[out["coverage_count"] >= min_letters_required].copy()

    neutral = 50.0
    for k in letters:
        out[k] = pd.to_numeric(out[k], errors="coerce").fillna(neutral)

    out["score_core"] = 0.0
    for k in letters:
        out["score_core"] += w.get(k, 0.0) * out[k]
    out["score"] = out["score_core"] + w.get("M", 0.0) * out["M"]

    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    return out
