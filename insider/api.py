"""
FastAPI application for insider trading data.

Serves JSON endpoints for use by a React (or other) frontend.
Run with: uvicorn insider.api:app --reload
"""
from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

try:
    import log_config
    log_config.configure_logging()
except ImportError:
    pass

from .trades import (
    collapse_form4_trades,
    filter_open_market,
    insider_trades_df,
    net_insider_flow,
    ticker_to_cik,
    trades_to_records,
)

app = FastAPI(
    title="Insider Trading API",
    description="SEC Form 4 insider transactions and analytics",
    version="1.0.0",
)

# Allow React dev server (and other origins) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to specific origins (e.g. ["http://localhost:3000"]) in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict:
    """Health check and API info."""
    return {
        "service": "Insider Trading API",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/api/insider/cik/{ticker}")
def get_cik(ticker: str) -> dict:
    """
    Get SEC Central Index Key (CIK) for a ticker symbol.

    Returns 404 if the ticker is not found in SEC data.
    """
    ticker = ticker.upper().strip()
    cik = ticker_to_cik(ticker)
    if cik is None:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found")
    return {"ticker": ticker, "cik": cik}


@app.get("/api/insider/trades/{ticker}")
def get_trades(
    ticker: str,
    limit: Annotated[int, Query(ge=1, le=200, description="Max number of Form 4 filings to process")] = 50,
    include_amended: Annotated[bool, Query(description="Include amended filings (4/A)")] = True,
    open_market_only: Annotated[bool, Query(description="Restrict to open-market buys (P) and sells (S)")] = False,
    collapsed: Annotated[bool, Query(description="Collapse same-day executions into one row per logical trade")] = False,
) -> dict:
    """
    Get insider transactions (Form 4) for a ticker.

    Optionally filter to open-market only and/or collapse same-day rows.
    """
    ticker = ticker.upper().strip()
    cik = ticker_to_cik(ticker)
    if cik is None:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found")

    df = insider_trades_df(ticker, limit=limit, include_amended=include_amended)
    if df.empty:
        return {"ticker": ticker, "trades": [], "count": 0}

    if open_market_only:
        df = filter_open_market(df)
    if collapsed:
        df = collapse_form4_trades(df)

    records = trades_to_records(df)
    return {"ticker": ticker, "trades": records, "count": len(records)}


@app.get("/api/insider/trades/{ticker}/net-flow")
def get_net_flow(
    ticker: str,
    days: Annotated[int, Query(ge=1, le=365, description="Lookback window in days")] = 90,
) -> dict:
    """
    Get net insider dollar flow (open-market buys minus sells) over the last N days.
    """
    ticker = ticker.upper().strip()
    cik = ticker_to_cik(ticker)
    if cik is None:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found")

    df = insider_trades_df(ticker, limit=100, include_amended=True)
    net = net_insider_flow(df, days=days)
    return {"ticker": ticker, "days": days, "net_flow_usd": round(net, 2)}

