"""
Cross-sectional momentum: rank index constituents by 6M and 12M RS, return top decile.

Uses DB price_history when index is sp500 or nasdaq and data is available; else yfinance.
Relative strength is computed as cross-sectional z-scores:
  RS6  = (Return_6m - Mean(Return_6m)) / StdDev(Return_6m)
  RS12 = (Return_12m - Mean(Return_12m)) / StdDev(Return_12m)
  Total RS = rs6_weight * RS6 + rs12_weight * RS12   (default 0.7 * RS6 + 0.3 * RS12)
Rank by total RS and long the top 10% (rebalance monthly).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal, Union

import numpy as np
import pandas as pd
import yfinance as yf

from .ratio_ranking import get_tickers_for_universe, get_tickers_from_csv, SP500_CSV_PATH

try:
    from log_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str):
        return logging.getLogger(name)

_log = get_logger(__name__)


# ~6 and ~12 months in trading days
TRADING_DAYS_6M = 126
TRADING_DAYS_12M = 252
TRADING_DAYS_1M = 21
# Trading days to skip from the most recent date when computing momentum
SKIP_DAYS = 21

# Index identifier -> CSV path (relative to typical project root)
INDEX_CSV = {
    "sp500": "sp500_symbols.csv",
    "s&p500": "sp500_symbols.csv",
    "s&p": "sp500_symbols.csv",
    "sp": "sp500_symbols.csv",
    "nasdaq100": "nasdaq100_symbols.csv",
    "nasdaq": "nasdaq100_symbols.csv",
    "nyse": "nyse_symbols.csv",
    "sp400": "sp400_symbols.csv",
    "sp600": "sp600_symbols.csv",
}

IndexName = Literal["sp500", "s&p500", "s&p", "sp", "nasdaq100", "nasdaq", "nyse", "sp400", "sp600"]


def _get_tickers(index: Union[IndexName, list[str]], csv_dir: str | None) -> list[str]:
    """
    Resolve index name or list to a list of ticker symbols.

    For sp500 and nasdaq, tickers are loaded from DB first, then CSV fallback.
    For other indices (nasdaq100, nyse), CSV is used.

    Args:
        index: Index name (e.g. "sp500", "nasdaq") or list of ticker strings.
        csv_dir: Optional directory to prepend to CSV paths.

    Returns:
        List of ticker strings, uppercase and stripped.
    """
    if isinstance(index, list):
        return [str(t).strip().upper() for t in index if t]
    key = str(index).strip().lower().replace(" ", "")
    if key in ("sp500", "s&p500", "s&p", "sp"):
        return get_tickers_for_universe("sp500", csv_dir=csv_dir)
    if key == "nasdaq":
        return get_tickers_for_universe("nasdaq", csv_dir=csv_dir)
    path = INDEX_CSV.get(key, index if key.endswith(".csv") else SP500_CSV_PATH)
    if csv_dir and not path.startswith("/") and "\\" not in path:
        import os
        path = os.path.join(csv_dir, path)
    return get_tickers_from_csv(path)


def _zscore(series: pd.Series) -> pd.Series:
    """Cross-sectional z-score; zero if std is 0 or NaN."""
    m = series.mean()
    s = series.std()
    if pd.isna(s) or s <= 0:
        return pd.Series(0.0, index=series.index)
    return (series - m) / s


def top_decile_momentum(
    index: Union[IndexName, list[str]],
    *,
    use_rs6_rs12: bool = True,
    rs6_weight: float = 0.7,
    rs12_weight: float = 0.3,
    top_pct: float = 0.10,
    min_tickers: int = 20,
    csv_dir: str | None = None,
) -> pd.DataFrame:
    """
    Rank index constituents by relative strength (6M + 12M) and return the top decile.

    Downloads at least 12 months of price data. Computes:
      - return_6m: (price now / price 6 months ago) - 1
      - return_12m: (price now / price 12 months ago) - 1
    Cross-sectional z-scores: RS6 = z(return_6m), RS12 = z(return_12m).
    Total RS = rs6_weight * RS6 + rs12_weight * RS12 (default 0.7*RS6 + 0.3*RS12).
    Rank by total RS and take the top `top_pct` (default 10%).

    Args:
        index: Index name ("sp500", "nasdaq", "nasdaq100", "nyse") or list of tickers.
        use_rs6_rs12: If True (default), use combined 6M+12M RS. If False, use only 6M RS.
        rs6_weight: Weight for 6-month RS when use_rs6_rs12 is True (default 0.7).
        rs12_weight: Weight for 12-month RS when use_rs6_rs12 is True (default 0.3).
        top_pct: Fraction to keep as top decile (default 0.10).
        min_tickers: Minimum tickers with valid returns (default 20).
        csv_dir: Optional directory for index CSV files.

    Returns:
        DataFrame with columns: ticker, return_6m, return_12m, rs6, rs12,
        momentum_score (total RS), rank, in_top_decile. Sorted by momentum_score descending.
    """
    tickers = _get_tickers(index, csv_dir)
    empty_cols = ["ticker", "return_1m", "return_6m", "return_12m", "rs6", "rs12", "rs_accel", "momentum_score", "rank", "in_top_decile"]
    if not tickers:
        return pd.DataFrame(columns=empty_cols)

    # Try DB first when index is sp500 or nasdaq
    closes = None
    universe_slug = None
    key = str(index).strip().lower().replace(" ", "") if not isinstance(index, list) else ""
    if key in ("sp500", "s&p500", "s&p", "sp"):
        universe_slug = "sp500"
    elif key == "nasdaq":
        universe_slug = "nasdaq"
    if universe_slug:
        try:
            from db.snapshots import get_close_prices_for_universe
            end_d = date.today()
            start_d = end_d - timedelta(days=760)
            closes = get_close_prices_for_universe(universe_slug, start_d, end_d)
            if not closes.empty and len(closes) >= TRADING_DAYS_6M + 1:
                _log.info("Momentum from DB: universe=%s rows=%d", universe_slug, len(closes))
            else:
                closes = None
        except Exception as e:
            _log.warning("Momentum from DB failed (%s), falling back to yfinance", e)
            closes = None

    if closes is None or closes.empty:
        data = yf.download(
            tickers,
            period="2y",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        if len(tickers) == 1:
            close = data["Close"].copy()
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close.name = tickers[0]
            closes = pd.DataFrame(close)
        else:
            if isinstance(data.columns, pd.MultiIndex) and data.columns.nlevels >= 2:
                closes = data.xs("Close", axis=1, level=1).copy()
            else:
                closes = data["Close"].copy() if "Close" in data.columns else data.copy()
                if isinstance(closes, pd.Series):
                    closes = pd.DataFrame(closes)
                if closes.shape[1] == 1 and tickers:
                    closes.columns = [tickers[0]]

    # Use a point 21 trading days in the past as "now" to reduce short-term mean reversion noise
    if len(closes) <= SKIP_DAYS:
        return pd.DataFrame(columns=empty_cols)
    last = closes.iloc[-1 - SKIP_DAYS]
    n = len(closes)

    # 6M return: from -126 to the skip point
    if n < TRADING_DAYS_6M + SKIP_DAYS + 1:
        return pd.DataFrame(columns=empty_cols)
    price_6m = closes.iloc[-1 - SKIP_DAYS - TRADING_DAYS_6M]
    return_6m = (last / price_6m).replace([np.inf, -np.inf], np.nan) - 1.0

    # 12M return: from -252 to the skip point
    if use_rs6_rs12 and n >= TRADING_DAYS_12M + SKIP_DAYS + 1:
        price_12m = closes.iloc[-1 - SKIP_DAYS - TRADING_DAYS_12M]
        return_12m = (last / price_12m).replace([np.inf, -np.inf], np.nan) - 1.0
    else:
        return_12m = pd.Series(np.nan, index=return_6m.index)
        use_rs6_rs12 = False

    # 1M return (acceleration): recent vs medium-term, reduces mean-reversion whipsaw
    if n < SKIP_DAYS + TRADING_DAYS_1M + 1:
        return_1m = pd.Series(np.nan, index=return_6m.index)
    else:
        price_1m_ago = closes.iloc[-1 - SKIP_DAYS - TRADING_DAYS_1M]
        return_1m = (last / price_1m_ago).replace([np.inf, -np.inf], np.nan) - 1.0
    acceleration = return_1m / (return_6m + 1e-9)

    valid = (
        (pd.to_numeric(last, errors="coerce") > 0)
        & (pd.to_numeric(price_6m, errors="coerce") > 0)
        & return_6m.notna()
        & return_1m.notna()
        & np.isfinite(acceleration)
    )
    if use_rs6_rs12:
        valid = valid & (pd.to_numeric(price_12m, errors="coerce") > 0) & return_12m.notna()
    return_6m = return_6m[valid].dropna()
    return_1m = return_1m[valid].dropna()
    acceleration = acceleration[valid].dropna()
    if use_rs6_rs12:
        return_12m = return_12m[valid].dropna()
    common = return_6m.index.intersection(acceleration.index)
    if use_rs6_rs12:
        common = common.intersection(return_12m.index)
    return_6m = return_6m.loc[common]
    return_1m = return_1m.loc[common]
    acceleration = acceleration.loc[common]
    if use_rs6_rs12:
        return_12m = return_12m.loc[common]
    else:
        return_12m = pd.Series(dtype=float)

    if len(return_6m) < min_tickers:
        return pd.DataFrame(columns=empty_cols)

    rs6 = _zscore(return_6m)
    rs_accel = _zscore(acceleration)
    if use_rs6_rs12 and len(return_12m) >= min_tickers:
        rs12 = _zscore(return_12m)
        # Blend: 0.50 rs6 + 0.25 rs12 + 0.25 acceleration (earnings momentum proxy)
        momentum_score = 0.50 * rs6 + 0.25 * rs12 + 0.25 * rs_accel
        out = pd.DataFrame({
            "ticker": momentum_score.index,
            "return_1m": return_1m.reindex(momentum_score.index).values,
            "return_6m": return_6m.reindex(momentum_score.index).values,
            "return_12m": return_12m.reindex(momentum_score.index).values,
            "rs6": rs6.values,
            "rs12": rs12.values,
            "rs_accel": rs_accel.values,
            "momentum_score": momentum_score.values,
        })
    else:
        # No 12M: 0.50 rs6 + 0.50 acceleration
        momentum_score = 0.50 * rs6 + 0.50 * rs_accel
        out = pd.DataFrame({
            "ticker": momentum_score.index,
            "return_1m": return_1m.reindex(momentum_score.index).values,
            "return_6m": return_6m.values,
            "return_12m": np.nan,
            "rs6": rs6.values,
            "rs12": np.nan,
            "rs_accel": rs_accel.values,
            "momentum_score": momentum_score.values,
        })

    out = out.sort_values("momentum_score", ascending=False).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    n_top = max(1, int(round(len(out) * top_pct)))
    out = out.head(n_top).copy()
    out["in_top_decile"] = True

    return out
