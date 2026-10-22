"""
FastAPI application for stock fair-value valuation and investment-score screening.

Exposes endpoints for:
- Ranking a universe by a chosen valuation method (Peter Lynch, Graham Number,
  simplified DCF) and returning the top N most-undervalued (upside) and top N
  most-overvalued (downside) stocks.
- Ranking a universe by a chosen investment-score formula (currently the "full"
  Peter Lynch score) and returning the top N stocks to invest in.

Run with: uvicorn valuation.api:app --reload --port 8003
"""
from __future__ import annotations

from typing import Literal, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .ranking import MethodName, rank_stocks_by_fair_value
from .scoring import ScoreName, rank_stocks_by_score

try:
    import log_config
    log_config.configure_logging()
except ImportError:
    pass

import logging
_log = logging.getLogger(__name__)


app = FastAPI(
    title="Valuation API",
    description="Fair-value screening: Peter Lynch, Graham Number, simplified DCF",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


UniverseName = Literal["sp500", "sp400", "sp600", "nasdaq", "nyse"]
_VALID_METHODS = ("lynch", "graham", "dcf")
_VALID_SCORES = ("lynch_full",)
_VALID_UNIVERSES = ("sp500", "sp400", "sp600", "nasdaq", "nyse")


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
        "service": "Valuation API",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/api/valuation/fair-value")
def fair_value_screen(
    method: MethodName = Query(
        default="lynch",
        description="Valuation method: 'lynch', 'graham', or 'dcf'.",
    ),
    universe: UniverseName = Query(
        default="sp500",
        description="Universe: sp500, sp400, sp600, nasdaq (=NASDAQ-100), nyse.",
    ),
    limit: Optional[int] = Query(
        default=None,
        ge=1,
        description="Max number of tickers to evaluate from the universe (perf/testing knob).",
    ),
    top_n: int = Query(
        default=10,
        ge=1,
        le=50,
        description="Number of rows in each of top_upside/top_downside (default 10).",
    ),
) -> dict:
    """
    Rank a universe by fair value (chosen method) and return the most undervalued
    (highest upside_pct) and most overvalued (lowest upside_pct) stocks.
    """
    method_lower = method.lower()
    if method_lower not in _VALID_METHODS:
        raise HTTPException(status_code=400, detail=f"Unsupported method '{method}'")

    universe_lower = universe.lower()
    if universe_lower not in _VALID_UNIVERSES:
        raise HTTPException(status_code=400, detail=f"Unsupported universe '{universe}'")

    _log.info(
        "Fair value request: method=%s universe=%s limit=%s top_n=%s",
        method_lower, universe_lower, limit, top_n,
    )
    try:
        df = rank_stocks_by_fair_value(method_lower, universe_slug=universe_lower, limit=limit)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker list not found for universe '{universe}' (DB empty and CSV missing)",
        )
    except ValueError as e:
        _log.warning("Fair value screening failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    if df.empty:
        return {
            "method": method_lower,
            "universe": universe_lower,
            "count": 0,
            "top_upside": [],
            "top_downside": [],
        }

    df = df.dropna(subset=["fair_value", "current_price", "upside_pct"])
    top_upside = df.sort_values("upside_pct", ascending=False).head(top_n)
    top_downside = df.sort_values("upside_pct", ascending=True).head(top_n)

    _log.info(
        "Fair value done: method=%s universe=%s count=%d", method_lower, universe_lower, len(df)
    )
    return {
        "method": method_lower,
        "universe": universe_lower,
        "count": len(df),
        "top_upside": _df_to_records(top_upside),
        "top_downside": _df_to_records(top_downside),
    }


@app.get("/api/valuation/score")
def score_screen(
    formula: ScoreName = Query(
        default="lynch_full",
        description="Investment-score formula: currently 'lynch_full' (full Peter Lynch score).",
    ),
    universe: UniverseName = Query(
        default="sp500",
        description="Universe: sp500, sp400, sp600, nasdaq (=NASDAQ-100), nyse.",
    ),
    limit: Optional[int] = Query(
        default=None,
        ge=1,
        description="Max number of tickers to evaluate from the universe (perf/testing knob).",
    ),
    top_n: int = Query(
        default=10,
        ge=1,
        le=50,
        description="Number of top-scoring rows to return (default 10).",
    ),
) -> dict:
    """
    Rank a universe by the chosen investment-score formula and return the top N
    stocks to invest in (highest score first).
    """
    formula_lower = formula.lower()
    if formula_lower not in _VALID_SCORES:
        raise HTTPException(status_code=400, detail=f"Unsupported formula '{formula}'")

    universe_lower = universe.lower()
    if universe_lower not in _VALID_UNIVERSES:
        raise HTTPException(status_code=400, detail=f"Unsupported universe '{universe}'")

    _log.info(
        "Score request: formula=%s universe=%s limit=%s top_n=%s",
        formula_lower, universe_lower, limit, top_n,
    )
    try:
        df = rank_stocks_by_score(formula_lower, universe_slug=universe_lower, limit=limit)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker list not found for universe '{universe}' (DB empty and CSV missing)",
        )
    except ValueError as e:
        _log.warning("Score screening failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    if df.empty:
        return {"formula": formula_lower, "universe": universe_lower, "count": 0, "top": []}

    df = df.dropna(subset=["score"])
    top = df.head(top_n)

    _log.info(
        "Score done: formula=%s universe=%s count=%d", formula_lower, universe_lower, len(df)
    )
    return {
        "formula": formula_lower,
        "universe": universe_lower,
        "count": len(df),
        "top": _df_to_records(top),
    }
