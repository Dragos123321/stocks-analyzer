"""SEC Form 4 insider trading: fetch, parse, and analyze insider transactions from EDGAR."""
from __future__ import annotations

from typing import Optional, Dict, Any, List
import time
import requests
import pandas as pd
import xml.etree.ElementTree as ET


# REQUIRED by SEC: include a real contact email
# NOTE: DO NOT set "Host" manually (it can break data.sec.gov calls).
SEC_HEADERS = {
    "User-Agent": "dragotatar@yahoo.com",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,text/plain,*/*",
}


# ----------------------------
# SEC fetch + parsing pipeline
# ----------------------------

def _sec_get(url: str, headers: dict | None = None, max_retries: int = 4) -> requests.Response:
    """
    Perform an HTTP GET to an SEC/EDGAR URL with required headers and retries.

    Uses SEC_HEADERS (User-Agent with contact email). Retries on 429/503 or
    transient errors with exponential backoff (0.5 * (attempt+1) seconds).

    Args:
        url: Full URL to request (e.g. SEC JSON or XML endpoint).
        headers: Optional extra headers merged over SEC_HEADERS.
        max_retries: Number of attempts (default 4).

    Returns:
        requests.Response with status_code 2xx.

    Raises:
        The last exception raised if all attempts fail (e.g. requests.RequestException).
    """
    h = dict(SEC_HEADERS)
    if headers:
        h.update(headers)

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=h, timeout=30)
            # Handle rate limits politely
            if r.status_code in (429, 503):
                time.sleep(0.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            time.sleep(0.5 * (attempt + 1))

    raise last_exc  # type: ignore[misc]


def ticker_to_cik(ticker: str) -> Optional[str]:
    """
    Map a stock ticker symbol to the SEC Central Index Key (CIK).

    Uses the SEC company_tickers.json mapping. CIK is returned as a 10-digit
    zero-padded string (e.g. "0000320193" for AAPL).

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL"). Case-insensitive, stripped.

    Returns:
        Zero-padded 10-character CIK string, or None if ticker not found.
    """
    ticker = ticker.upper().strip()
    data = _sec_get("https://www.sec.gov/files/company_tickers.json").json()
    for _, row in data.items():
        if str(row.get("ticker", "")).upper() == ticker:
            cik_int = int(row["cik_str"])
            return f"{cik_int:010d}"
    return None


def _find_form4_xml_url(cik10: str, accession: str) -> Optional[str]:
    """
    Resolve the URL of the Form 4 XML file for a given SEC accession number.

    Fetches the filing index for the accession and picks the best .xml file
    (prefers names containing "form4" or "form", then "primary"; penalizes "xsl").

    Args:
        cik10: 10-digit CIK string (e.g. "0000320193").
        accession: SEC accession number (e.g. "0000320193-21-000077"), dashes allowed.

    Returns:
        Full URL to the Form 4 XML document, or None if no suitable XML found.
    """
    cik_int = str(int(cik10))  # strip leading zeros for archive path
    acc_nodash = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
    idx = _sec_get(index_url).json()

    files = idx.get("directory", {}).get("item", [])
    xml_candidates = [f["name"] for f in files if f.get("name", "").lower().endswith(".xml")]
    if not xml_candidates:
        return None

    def score(name: str) -> int:
        """Prefer filenames containing form4/form and primary; penalize xsl."""
        n = name.lower()
        s = 0
        if "form4" in n or "form" in n:
            s += 3
        if "primary" in n:
            s += 2
        if "xsl" in n:
            s -= 5
        return s

    xml_candidates.sort(key=score, reverse=True)
    chosen = xml_candidates[0]
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{chosen}"


def _xml_text(el: Optional[ET.Element]) -> Optional[str]:
    """
    Return the stripped text content of an XML element, or None.

    Args:
        el: ElementTree Element, or None.

    Returns:
        Stripped text string if present and non-empty; otherwise None.
    """
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t if t else None


def _parse_form4_xml(
    xml_bytes: bytes,
    ticker: str,
    cik10: str,
    accession: str,
    filing_date: str,
) -> List[Dict[str, Any]]:
    """
    Parse SEC Form 4 XML content into a list of transaction dictionaries.

    Extracts non-derivative and derivative transactions, plus reporting owner
    name/title. Each row includes ticker, cik, accession, filing_date,
    transaction_date, insider, title, kind, transaction_code, acquired_disposed,
    shares, price, value, shares_owned_after, direct_or_indirect.

    Args:
        xml_bytes: Raw XML bytes of the Form 4 filing.
        ticker: Stock ticker symbol (e.g. "NVDA").
        cik10: 10-digit CIK string.
        accession: SEC accession number for the filing.
        filing_date: Filing date string from the submissions index.

    Returns:
        List of dicts, one per transaction, with keys as above.
    """
    root = ET.fromstring(xml_bytes)

    owners = root.findall(".//reportingOwner")
    owner_name = None
    owner_title = None
    if owners:
        owner_name = _xml_text(owners[0].find(".//reportingOwnerId/rptOwnerName"))
        owner_title = _xml_text(owners[0].find(".//reportingOwnerRelationship/officerTitle"))

    def fnum(x: Optional[str]) -> Optional[float]:
        """Convert string to float (commas removed); return None on failure."""
        if x is None:
            return None
        try:
            return float(x.replace(",", ""))
        except Exception:
            return None

    rows: List[Dict[str, Any]] = []

    def parse_tx(tx: ET.Element, kind: str):
        """Append one transaction from XML element to rows (non-derivative or derivative)."""
        tx_date = _xml_text(tx.find(".//transactionDate/value"))
        code = _xml_text(tx.find(".//transactionCoding/transactionCode"))
        shares = fnum(_xml_text(tx.find(".//transactionAmounts/transactionShares/value")))
        price = fnum(_xml_text(tx.find(".//transactionAmounts/transactionPricePerShare/value")))
        acquired_disposed = _xml_text(tx.find(".//transactionAmounts/transactionAcquiredDisposedCode/value"))
        post_shares = fnum(_xml_text(tx.find(".//postTransactionAmounts/sharesOwnedFollowingTransaction/value")))
        direct_indirect = _xml_text(tx.find(".//ownershipNature/directOrIndirectOwnership/value"))

        rows.append(
            {
                "ticker": ticker,
                "cik": cik10,
                "accession": accession,
                "filing_date": filing_date,
                "transaction_date": tx_date,
                "insider": owner_name,
                "title": owner_title,
                "kind": kind,
                "transaction_code": code,  # P/S/M/A/F etc.
                "acquired_disposed": acquired_disposed,  # A/D
                "shares": shares,
                "price": price,
                "value": (shares * price) if (shares is not None and price is not None) else None,
                "shares_owned_after": post_shares,
                "direct_or_indirect": direct_indirect,  # D/I
            }
        )

    for tx in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        parse_tx(tx, "non-derivative")

    for tx in root.findall(".//derivativeTable/derivativeTransaction"):
        parse_tx(tx, "derivative")

    return rows


def insider_trades_df(
    ticker: str,
    limit: int = 50,
    include_amended: bool = True,
    sleep_s: float = 0.2,
) -> pd.DataFrame:
    """
    Fetch insider transactions (SEC Form 4 and 4/A) for a ticker and return a DataFrame.

    Retrieves recent filings from SEC EDGAR, downloads the Form 4 XML for each
    filing, and parses all transactions into rows. Sorted by transaction_date
    and filing_date descending.

    Args:
        ticker: Stock ticker symbol (e.g. "NVDA").
        limit: Maximum number of Form 4 filings to process (default 50). Counts
               filings, not individual transaction rows.
        include_amended: If True, include amended filings (4/A); else only "4".
        sleep_s: Seconds to sleep between filing requests (default 0.2) to ease rate limits.

    Returns:
        DataFrame with columns: ticker, cik, accession, filing_date, transaction_date,
        insider, title, kind, transaction_code, acquired_disposed, shares, price, value,
        shares_owned_after, direct_or_indirect. Empty DataFrame if ticker has no CIK.
    """
    cik10 = ticker_to_cik(ticker)
    if cik10 is None:
        return pd.DataFrame()

    submissions_url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
    sub = _sec_get(submissions_url).json()

    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])

    want_forms = {"4", "4/A"} if include_amended else {"4"}

    rows: List[Dict[str, Any]] = []
    used = 0

    for form, acc, fdate in zip(forms, accessions, filing_dates):
        if form not in want_forms:
            continue

        xml_url = _find_form4_xml_url(cik10, acc)
        if not xml_url:
            continue

        time.sleep(sleep_s)
        try:
            xml_resp = _sec_get(xml_url, headers={"Accept": "application/xml,text/xml,*/*"})
            rows.extend(_parse_form4_xml(xml_resp.content, ticker.upper(), cik10, acc, fdate))
            used += 1
            if used >= limit:
                break
        except Exception:
            continue

    df = pd.DataFrame(rows)
    if not df.empty:
        df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
        df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
        df = df.sort_values(["transaction_date", "filing_date"], ascending=[False, False])

    return df


# ----------------------------
# Analysis / cleanup helpers
# ----------------------------

def filter_open_market(df: pd.DataFrame) -> pd.DataFrame:
    """
    Restrict to open-market purchases (P) and sales (S); drop other transaction codes.

    Args:
        df: DataFrame with a "transaction_code" column (e.g. from insider_trades_df).

    Returns:
        New DataFrame with only rows where transaction_code is "P" or "S".
        Empty DataFrame returned unchanged.
    """
    if df.empty:
        return df
    return df[df["transaction_code"].isin({"P", "S"})].copy()


def collapse_form4_trades(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate multiple same-day execution rows into one row per logical trade.

    Groups by (ticker, insider, title, filing_date, transaction_date,
    transaction_code, direct_or_indirect). Sums shares and value; takes min of
    shares_owned_after; adds price_vwap = value / shares. Sorted by
    transaction_date and filing_date descending.

    Args:
        df: DataFrame from insider_trades_df (or similar) with columns including
            shares, value, and the grouping keys above.

    Returns:
        DataFrame with one row per logical trade and columns: ticker, insider, title,
        filing_date, transaction_date, transaction_code, direct_or_indirect, shares,
        value, shares_owned_after, price_vwap. Returns df unchanged if empty or
        missing "shares"/"value".
    """
    if df.empty:
        return df

    needed = {"shares", "value"}
    for col in needed:
        if col not in df.columns:
            return df

    grp_cols = [
        "ticker",
        "insider",
        "title",
        "filing_date",
        "transaction_date",
        "transaction_code",
        "direct_or_indirect",
    ]

    out = (
        df.groupby(grp_cols, dropna=False)
        .agg(
            shares=("shares", "sum"),
            value=("value", "sum"),
            shares_owned_after=("shares_owned_after", "min"),
        )
        .reset_index()
    )

    out["price_vwap"] = out["value"] / out["shares"]
    return out.sort_values(["transaction_date", "filing_date"], ascending=[False, False])


def net_insider_flow(df: pd.DataFrame, days: int = 90) -> float:
    """
    Compute net dollar flow from insider open-market buys minus sells over a window.

    Filters to transaction_date within the last `days` days (UTC), then to
    open-market only (P/S), and sums value for P minus value for S.

    Args:
        df: DataFrame from insider_trades_df (or similar) with "transaction_date"
            and "value" and "transaction_code" columns.
        days: Number of days to look back from now (default 90).

    Returns:
        Net flow in dollars (buys - sells). 0.0 if df is empty or no matching rows.
    """
    if df.empty:
        return 0.0

    d = df.copy()
    d["transaction_date"] = pd.to_datetime(d["transaction_date"], errors="coerce")
    cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=days)
    d = d[d["transaction_date"].notna() & (d["transaction_date"] >= cutoff)]
    d = filter_open_market(d)

    buys = float(d.loc[d["transaction_code"] == "P", "value"].fillna(0).sum())
    sells = float(d.loc[d["transaction_code"] == "S", "value"].fillna(0).sum())
    return buys - sells
