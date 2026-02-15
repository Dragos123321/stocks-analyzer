"""Liquidity ratios: current ratio and quick ratio from yfinance."""
import yfinance as yf


def get_current_ratio(ticker: str) -> float:
    """
    Return the current ratio (current assets / current liabilities) for the ticker.

    Data is from yfinance. The current ratio measures short-term liquidity.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL").

    Returns:
        Current ratio as a float.

    Raises:
        ValueError: If current ratio is not available for the ticker.
    """
    stock = yf.Ticker(ticker)
    info = stock.info

    ratio = info.get("currentRatio")
    if ratio is None:
        raise ValueError(f"Current ratio not available for {ticker}")

    return float(ratio)


def get_quick_ratio(ticker: str) -> float:
    """
    Return the quick ratio (acid test) for the ticker.

    Quick ratio = (current assets - inventory) / current liabilities.
    Data is from yfinance.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL").

    Returns:
        Quick ratio as a float.

    Raises:
        ValueError: If quick ratio is not available for the ticker.
    """
    stock = yf.Ticker(ticker)
    info = stock.info

    ratio = info.get("quickRatio")
    if ratio is None:
        raise ValueError(f"Quick ratio not available for {ticker}")

    return float(ratio)
