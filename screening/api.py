"""
FastAPI application for stock screening.

Exposes endpoints for:
- Ratio-based ranking (P/E, PEG, Price/Sales, EV/EBITDA)
- CANSLIM-style ranking
- Cross-sectional momentum (top decile by RS6 / RS6+RS12)

Run with: uvicorn screening.api:app --reload
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from financials.ratios import ev_to_ebitda, pe, peg_ratio, price_to_sales
from .canslim import rank_stocks_by_canslim
from .fetch_latest import fetch_latest_for_universe
from .momentum import top_decile_momentum
from .ratio_ranking import rank_stocks_by_ratio

try:
    import log_config
    log_config.configure_logging()
except ImportError:
    pass

import logging
_log = logging.getLogger(__name__)


app = FastAPI(
    title="Screening API",
    description="Stock screening: ratios, CANSLIM, and momentum",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


RatioName = Literal["pe", "peg", "price_to_sales", "ev_to_ebitda"]
UniverseName = Literal["sp500", "nasdaq", "sp400", "sp600"]

_RATIO_FUNCS: dict[RatioName, callable] = {
    "pe": pe,
    "peg": peg_ratio,
    "price_to_sales": price_to_sales,
    "ev_to_ebitda": ev_to_ebitda,
}


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to JSON-serializable records (datetimes -> ISO, NaN -> null)."""
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.tz_localize(None).astype(object).where(df[col].notna(), None)
            df[col] = df[col].apply(lambda x: x.isoformat() if hasattr(x, "isoformat") else x)
    return df.replace({pd.NA: None, float("nan"): None}).to_dict(orient="records")


@app.get("/")
def root() -> dict:
    """Health/info endpoint."""
    return {
        "service": "Screening API",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.post("/api/screening/fetch-latest")
def fetch_latest(
    universe: Annotated[
        UniverseName,
        Query(description="Universe to refresh: 'sp500', 'nasdaq', 'sp400', or 'sp600'."),
    ] = "sp500",
) -> dict:
    """
    Fetch latest data from yfinance for all tickers in the chosen universe
    and write to ticker_snapshot and price_history tables.

    Requires the universe's tickers to already exist in the `symbol` table
    (run `python scripts/seed_db.py` first; tickers missing from the DB are
    skipped, see fetch_latest_for_universe's `errors`/skip logging).

    This can take several minutes for large universes. Returns counts of
    tickers processed, snapshots updated, and price rows written.
    """
    universe_lower = universe.lower()
    if universe_lower not in ("sp500", "nasdaq", "sp400", "sp600"):
        raise HTTPException(status_code=400, detail="Universe must be 'sp500', 'nasdaq', 'sp400', or 'sp600'")
    _log.info("Fetch latest started for universe=%s", universe_lower)
    try:
        result = fetch_latest_for_universe(universe_lower)
    except Exception as e:
        _log.exception("Fetch latest failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    _log.info(
        "Fetch latest done: universe=%s processed=%s snapshots=%s price_rows=%s",
        universe_lower,
        result.get("tickers_processed"),
        result.get("snapshots_updated"),
        result.get("price_rows_updated"),
    )
    return result


@app.get("/api/screening/ratios/{ratio}")
def screen_by_ratio(
    ratio: RatioName,
    universe: UniverseName = Query(
        default="sp500",
        description="Universe to screen: 'sp500' or 'nasdaq'.",
    ),
    limit: Optional[int] = Query(
        default=None,
        ge=1,
        description="Max number of tickers to consider from the CSV",
    ),
    min_value: Optional[float] = Query(
        default=None,
        description="Minimum ratio value to include",
    ),
    ascending: bool = Query(
        default=False,
        description="Sort ascending instead of descending",
    ),
) -> dict:
    """
    Rank stocks by a fundamental ratio (P/E, PEG, Price/Sales, EV/EBITDA).

    Universe is selected by name ('sp500' or 'nasdaq'). Tickers are loaded from
    the database when available, with CSV fallback.
    """
    if ratio not in _RATIO_FUNCS:
        raise HTTPException(status_code=400, detail=f"Unsupported ratio '{ratio}'")

    universe_lower = universe.lower()
    if universe_lower not in ("sp500", "nasdaq", "sp400", "sp600"):
        raise HTTPException(status_code=400, detail=f"Unsupported universe '{universe}'")

    _log.info("Screening request: ratio=%s universe=%s limit=%s ascending=%s", ratio, universe_lower, limit, ascending)
    fn = _RATIO_FUNCS[ratio]
    try:
        df = rank_stocks_by_ratio(
            fn,
            limit=limit,
            min_value=min_value,
            universe_slug=universe_lower,
            ascending=ascending,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker list not found for universe '{universe}' (DB empty and CSV missing)",
        )
    except ValueError as e:
        _log.warning("Ratio screening failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    records = _df_to_records(df)
    _log.info("Ratio screening done: ratio=%s universe=%s count=%d", ratio, universe_lower, len(records))
    return {
        "ratio": ratio,
        "universe": universe_lower,
        "count": len(records),
        "results": records,
    }


@app.get("/api/screening/canslim")
def screen_by_canslim(
    universe: UniverseName = Query(
        default="sp500",
        description="Universe to screen: 'sp500' or 'nasdaq'.",
    ),
    limit: Optional[int] = Query(
        default=None,
        ge=1,
        description="Max number of tickers to consider from the CSV.",
    ),
    benchmark: str = Query(
        default="SPY",
        description="Benchmark ticker for CANSLIM 'M' and relative strength (default SPY).",
    ),
) -> dict:
    """
    CANSLIM-style ranking for a named ticker universe.

    Tickers are loaded from the database when available (sp500/nasdaq), with CSV fallback.
    Uses the default letter weights defined in the CANSLIM implementation.
    """
    universe_lower = universe.lower()
    if universe_lower not in ("sp500", "nasdaq", "sp400", "sp600"):
        raise HTTPException(status_code=400, detail=f"Unsupported universe '{universe}'")

    _log.info("Screening request: canslim universe=%s limit=%s benchmark=%s", universe_lower, limit, benchmark)
    try:
        df = rank_stocks_by_canslim(
            universe_slug=universe_lower,
            limit=limit,
            benchmark=benchmark,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker list not found for universe '{universe}' (DB empty and CSV missing)",
        )
    except ValueError as e:
        _log.warning("CANSLIM screening failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    records = _df_to_records(df)
    _log.info("CANSLIM screening done: universe=%s count=%d", universe_lower, len(records))
    return {
        "universe": universe_lower,
        "benchmark": benchmark,
        "count": len(records),
        "results": records,
    }


@app.get("/api/screening/momentum/top-decile")
def momentum_top_decile(
    index: str = Query(
        default="sp500",
        description="Index name (sp500, nasdaq100, nasdaq, nyse) or path to CSV, "
        "or comma-separated tickers.",
    ),
    use_rs6_rs12: bool = Query(
        default=True,
        description="Use combined RS6+RS12 (0.7*RS6 + 0.3*RS12). If false, only RS6 is used.",
    ),
    rs6_weight: float = Query(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Weight on 6M RS when combining RS6 and RS12.",
    ),
    rs12_weight: float = Query(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Weight on 12M RS when combining RS6 and RS12.",
    ),
    top_pct: float = Query(
        default=0.10,
        gt=0.0,
        le=1.0,
        description="Fraction of universe to keep as top decile (default 0.10).",
    ),
    min_tickers: int = Query(
        default=20,
        ge=1,
        description="Minimum tickers with valid returns required to compute RS.",
    ),
    csv_dir: Optional[str] = Query(
        default=None,
        description="Optional directory for index CSV files (if index is a known name).",
    ),
) -> dict:
    """
    Cross-sectional momentum: top decile by total relative strength.

    - RS6  = z-score of 6M return
    - RS12 = z-score of 12M return
    - Total RS = rs6_weight * RS6 + rs12_weight * RS12 (default 0.7/0.3)
    """
    # Allow index to be a comma-separated list of tickers
    if "," in index and not index.lower().endswith(".csv"):
        tickers = [t.strip().upper() for t in index.split(",") if t.strip()]
    else:
        tickers = index  # index name or CSV path; handled by top_decile_momentum

    _log.info(
        "Screening request: momentum index=%s use_rs6_rs12=%s top_pct=%s",
        index, use_rs6_rs12, top_pct,
    )
    try:
        df = top_decile_momentum(
            tickers,
            use_rs6_rs12=use_rs6_rs12,
            rs6_weight=rs6_weight,
            rs12_weight=rs12_weight,
            top_pct=top_pct,
            min_tickers=min_tickers,
            csv_dir=csv_dir,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"CSV file not found or index not resolvable: {index}")
    except ValueError as e:
        _log.warning("Momentum screening failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    records = _df_to_records(df)
    _log.info("Momentum screening done: index=%s count=%d", index, len(records))
    return {
        "index": index,
        "use_rs6_rs12": use_rs6_rs12,
        "rs6_weight": rs6_weight,
        "rs12_weight": rs12_weight,
        "top_pct": top_pct,
        "count": len(records),
        "results": records,
    }

