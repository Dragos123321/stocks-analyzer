"""
FastAPI application for per-ticker financial metrics.

Exposes endpoints for:
- EPS (annual, quarterly, EPS change %)
- Valuation ratios (P/E, PEG, Price/Sales, EV/EBITDA)
- Liquidity ratios (current ratio, quick ratio)

Run with: uvicorn financials.api:app --reload
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

from .debt import get_current_ratio, get_quick_ratio
from .eps import annual_eps, eps_change_pct, quarterly_eps
from .ratios import ev_to_ebitda, pe, peg_ratio, price_to_sales


app = FastAPI(
    title="Financials API",
    description="Per-ticker EPS, valuation ratios, and liquidity ratios",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict:
    """Health/info endpoint."""
    return {
        "service": "Financials API",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/api/financials/eps/annual/{ticker}")
def eps_annual(
    ticker: str,
    year: Annotated[int, Query(ge=1900, le=3000, description="Fiscal year (e.g. 2024)")],
) -> dict:
    """Get diluted EPS for a given ticker and fiscal year."""
    ticker = ticker.upper().strip()
    value = annual_eps(ticker, year)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Diluted EPS not available for {ticker} in {year}")
    return {"ticker": ticker, "year": year, "eps_diluted": value}


@app.get("/api/financials/eps/quarterly/{ticker}")
def eps_quarterly(
    ticker: str,
    year: Annotated[int, Query(ge=1900, le=3000, description="Calendar year (e.g. 2024)")],
    quarter: Annotated[str, Query(pattern="^Q[1-4]$", description="Quarter: Q1, Q2, Q3, or Q4")],
) -> dict:
    """Get diluted EPS for a given ticker, calendar year, and quarter (Q1–Q4)."""
    ticker = ticker.upper().strip()
    quarter = quarter.upper()
    value = quarterly_eps(ticker, year, quarter)
    if value is None:
        raise HTTPException(
            status_code=404,
            detail=f"Diluted EPS not available for {ticker} {year} {quarter}",
        )
    return {"ticker": ticker, "year": year, "quarter": quarter, "eps_diluted": value}


@app.get("/api/financials/eps/change/{ticker}")
def eps_change(
    ticker: str,
    start_year: Annotated[int, Query(alias="year_start", ge=1900, le=3000)],
    end_year: Annotated[int, Query(alias="year_end", ge=1900, le=3000)],
) -> dict:
    """Get percentage change in diluted EPS between two fiscal years."""
    ticker = ticker.upper().strip()
    if end_year <= start_year:
        raise HTTPException(status_code=400, detail="year_end must be greater than year_start")
    change = eps_change_pct(ticker, start_year, end_year)
    if change is None:
        raise HTTPException(
            status_code=404,
            detail=f"EPS change not available for {ticker} between {start_year} and {end_year}",
        )
    return {
        "ticker": ticker,
        "year_start": start_year,
        "year_end": end_year,
        "eps_change_pct": change,
    }


@app.get("/api/financials/ratios/{ticker}")
def valuation_ratios(ticker: str) -> dict:
    """
    Get key valuation ratios for a ticker:
    - P/E
    - PEG
    - Price/Sales
    - EV/EBITDA
    """
    ticker = ticker.upper().strip()
    return {
        "ticker": ticker,
        "pe": pe(ticker),
        "peg": peg_ratio(ticker),
        "price_to_sales": price_to_sales(ticker),
        "ev_to_ebitda": ev_to_ebitda(ticker),
    }


@app.get("/api/financials/debt/{ticker}")
def liquidity_ratios(ticker: str) -> dict:
    """
    Get liquidity ratios (current ratio and quick ratio) for a ticker.
    """
    ticker = ticker.upper().strip()
    try:
        current = get_current_ratio(ticker)
    except ValueError:
        current = None
    try:
        quick = get_quick_ratio(ticker)
    except ValueError:
        quick = None

    if current is None and quick is None:
        raise HTTPException(status_code=404, detail=f"Liquidity ratios not available for {ticker}")

    return {
        "ticker": ticker,
        "current_ratio": current,
        "quick_ratio": quick,
    }

