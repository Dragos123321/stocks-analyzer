"""Full Peter Lynch investment score: valuation (growth + dividend), debt, institutional ownership."""
from __future__ import annotations

from .common import _clipped_growth_rate, _current_price, _get_info, _is_missing, upside_pct

# Weights for the composite score (renormalized over whichever components are available).
VALUATION_WEIGHT = 60.0
DEBT_WEIGHT = 25.0
INSTITUTIONAL_WEIGHT = 15.0


def full_lynch_score(ticker_symbol: str, *, info: dict | None = None) -> dict | None:
    """
    Composite "full" Peter Lynch investment score, extending the plain growth-only
    fair value (valuation/lynch.py) with two more of Lynch's well-known rules of
    thumb from "One Up On Wall Street":

      - Valuation (weight 60): fair P/E = growth_rate_pct + dividend_yield_pct --
        Lynch's own refinement of the PEG idea to account for dividend payers
        (a stalwart growing 8%/yr with a 4% yield is valued like a 12% grower).
        fair_value = trailing_eps * fair_pe. Dividend yield is computed as
        dividendRate / current_price rather than trusting yfinance's
        dividendYield field directly, since that field's units have drifted
        between yfinance/Yahoo API versions (dividendRate, in dollars/share,
        has not).
      - Balance-sheet strength (weight 25): Lynch avoided heavily-indebted
        companies ("the worst thing a company can do with its money is
        borrow heavily"). Scored from debt-to-equity, lower is better.
      - "Undiscovered" signal (weight 15): Lynch favored stocks Wall Street
        hadn't piled into yet, since there's more room for a re-rating once
        they're "discovered". Scored from institutional ownership, lower is
        better.

    Each component maps to an absolute 0-100 scale using rule-of-thumb
    thresholds (not peer-relative ranking, since Lynch's own criteria were
    absolute thresholds rather than cross-sectional comparisons), then combined
    as a weighted average. The valuation component is mandatory -- it's the core
    Lynch signal, and this function returns None without it. Debt and
    institutional-ownership are optional extras: if either is missing, the
    remaining weights are renormalized rather than dropping the ticker.

    This is the first formula in an extensible registry -- see
    valuation/scoring.py to add further formulas (e.g. a future "insider
    buying" or "company category" component) without touching this one.

    Args:
        ticker_symbol: Stock ticker symbol.
        info: Optional pre-fetched info-like dict (e.g. from a bulk DB snapshot
            query) to use instead of fetching/looking up this ticker individually.

    Returns:
        {ticker, current_price, trailing_eps, growth_rate_pct, dividend_yield_pct,
         fair_value, upside_pct, debt_to_equity, held_percent_institutions, score,
         coverage_count} or None if the valuation component can't be computed.
    """
    if info is None:
        info = _get_info(ticker_symbol)

    trailing_eps = info.get("trailingEps")
    if _is_missing(trailing_eps) or trailing_eps <= 0:
        return None

    growth_rate = _clipped_growth_rate(info, lo=0.0, hi=0.50)
    if growth_rate is None:
        return None

    current_price = _current_price(info)
    if current_price is None:
        return None

    dividend_rate = info.get("dividendRate")
    if _is_missing(dividend_rate) or dividend_rate <= 0:
        dividend_yield = 0.0
    else:
        dividend_yield = float(dividend_rate) / current_price

    growth_rate_pct = growth_rate * 100.0
    dividend_yield_pct = dividend_yield * 100.0
    fair_pe = growth_rate_pct + dividend_yield_pct
    fair_value = float(trailing_eps) * fair_pe
    upside = upside_pct(fair_value, current_price)

    # 0% upside -> 50, +100% upside -> 100, -100% upside -> 0 (clipped beyond).
    valuation_score = max(0.0, min(100.0, (upside + 100.0) / 2.0))

    weighted_sum = VALUATION_WEIGHT * valuation_score
    weight_total = VALUATION_WEIGHT
    coverage_count = 1

    debt_to_equity = info.get("debtToEquity")
    if _is_missing(debt_to_equity):
        debt_to_equity = None
    else:
        debt_to_equity = float(debt_to_equity)
        # 0% D/E -> 100, 100% -> 50, 200%+ -> 0.
        debt_score = max(0.0, min(100.0, 100.0 - debt_to_equity / 2.0))
        weighted_sum += DEBT_WEIGHT * debt_score
        weight_total += DEBT_WEIGHT
        coverage_count += 1

    held_pct = info.get("heldPercentInstitutions")
    if _is_missing(held_pct):
        held_pct = None
    else:
        held_pct = float(held_pct)
        # 0% institutional -> 100, 100% -> 0.
        institutional_score = max(0.0, min(100.0, (1.0 - held_pct) * 100.0))
        weighted_sum += INSTITUTIONAL_WEIGHT * institutional_score
        weight_total += INSTITUTIONAL_WEIGHT
        coverage_count += 1

    score = weighted_sum / weight_total

    return {
        "ticker": ticker_symbol,
        "current_price": current_price,
        "trailing_eps": float(trailing_eps),
        "growth_rate_pct": growth_rate_pct,
        "dividend_yield_pct": dividend_yield_pct,
        "fair_value": fair_value,
        "upside_pct": upside,
        "debt_to_equity": debt_to_equity,
        "held_percent_institutions": held_pct,
        "score": score,
        "coverage_count": coverage_count,
    }
