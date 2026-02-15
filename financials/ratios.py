"""Valuation ratios (P/E, PEG, Price/Sales, EV/EBITDA) from yfinance."""
import yfinance as yf


def pe(ticker_symbol: str) -> float | None:
    """
    Return the current (trailing) price-to-earnings ratio for the given ticker.

    Data is from yfinance (trailing twelve months).

    Args:
        ticker_symbol: Stock ticker symbol (e.g. "AAPL", "NVDA").

    Returns:
        Trailing P/E ratio as a float, or None if not available.
    """
    t = yf.Ticker(ticker_symbol)
    info = t.info
    return info.get("trailingPE")


def peg_ratio(ticker_symbol: str) -> float | None:
    """
    Return the PEG ratio (price/earnings to growth) for the given ticker.

    Uses 5-year expected growth when available (pegRatio or trailingPegRatio from yfinance).

    Args:
        ticker_symbol: Stock ticker symbol (e.g. "AAPL").

    Returns:
        PEG ratio as a float, or None if not available.
    """
    t = yf.Ticker(ticker_symbol)
    info = t.info
    return info.get("pegRatio") or info.get("trailingPegRatio")


def price_to_sales(ticker_symbol: str) -> float | None:
    """
    Return the trailing Price-to-Sales ratio for the given ticker.

    Uses trailing twelve months revenue (yfinance: priceToSalesTrailing12Months or priceToSales).

    Args:
        ticker_symbol: Stock ticker symbol (e.g. "AAPL").

    Returns:
        Price/Sales ratio as a float, or None if not available.
    """
    t = yf.Ticker(ticker_symbol)
    info = t.info
    return info.get("priceToSalesTrailing12Months") or info.get("priceToSales")


def ev_to_ebitda(ticker_symbol: str) -> float | None:
    """
    Return the Enterprise Value to EBITDA ratio for the given ticker.

    Data is from yfinance (enterpriseToEbitda).

    Args:
        ticker_symbol: Stock ticker symbol (e.g. "AAPL").

    Returns:
        EV/EBITDA ratio as a float, or None if not available.
    """
    t = yf.Ticker(ticker_symbol)
    info = t.info
    return info.get("enterpriseToEbitda")
