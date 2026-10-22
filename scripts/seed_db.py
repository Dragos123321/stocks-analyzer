"""
Seed the database with universes and symbols from the CSV files.

Run from the stocks-analyzer directory:
  python scripts/seed_db.py [--clean]

  --clean   Delete all existing data (universe, symbol, universe_symbol,
            ticker_snapshot, price_history, financials) before seeding.
            Use for a full reload.

Reads sp500_symbols.csv, nasdaq100_symbols.csv, sp400_symbols.csv, and
sp600_symbols.csv (each expects a 'Symbol' column), inserts into universe
(sp500, nasdaq, sp400, sp600), symbol, and universe_symbol.
NASDAQ universe = NASDAQ 100.
Without --clean, existing rows are skipped (ON CONFLICT DO NOTHING).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

# Ensure project root is on path when run as script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# CSV paths relative to project root (stocks-analyzer). NASDAQ = NASDAQ 100.
SP500_CSV = os.path.join(_ROOT, "sp500_symbols.csv")
NASDAQ_CSV = os.path.join(_ROOT, "nasdaq100_symbols.csv")
SP400_CSV = os.path.join(_ROOT, "sp400_symbols.csv")
SP600_CSV = os.path.join(_ROOT, "sp600_symbols.csv")

# universe slug -> (display name, csv path)
UNIVERSES: dict[str, tuple[str, str]] = {
    "sp500": ("S&P 500", SP500_CSV),
    "nasdaq": ("NASDAQ", NASDAQ_CSV),
    "sp400": ("S&P 400", SP400_CSV),
    "sp600": ("S&P 600", SP600_CSV),
}


def load_tickers_from_csv(path: str) -> list[str]:
    """Load ticker symbols from a CSV with a 'Symbol' column."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CSV not found: {path}")
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "Symbol" not in (reader.fieldnames or []):
            raise ValueError(f"CSV must have a 'Symbol' column: {path}")
        return [row["Symbol"].strip() for row in reader if row.get("Symbol", "").strip()]


def main(clean_first: bool = False) -> None:
    from db.connection import get_connection
    from db.snapshots import clean_all_before_seed

    if clean_first:
        print("Cleaning all tables...")
        clean_all_before_seed()
        print("  Done.")

    print("Loading tickers from CSV...")
    tickers_by_slug: dict[str, list[str]] = {}
    for slug, (display_name, csv_path) in UNIVERSES.items():
        tickers_by_slug[slug] = load_tickers_from_csv(csv_path)
        print(f"  {display_name}: {len(tickers_by_slug[slug])} symbols")

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Ensure universe rows exist
            cur.execute(
                """
                INSERT INTO universe (name, slug)
                VALUES ('S&P 500', 'sp500'), ('NASDAQ', 'nasdaq'),
                       ('S&P 400', 'sp400'), ('S&P 600', 'sp600')
                ON CONFLICT (slug) DO NOTHING
                """
            )
            cur.execute(
                "SELECT id, slug FROM universe WHERE slug IN ('sp500', 'nasdaq', 'sp400', 'sp600')"
            )
            universe_ids = {row[1]: row[0] for row in cur.fetchall()}
            for slug in UNIVERSES:
                if slug not in universe_ids:
                    raise RuntimeError(f"Universe row not found after insert for slug={slug!r} (check schema)")

            # Insert all unique tickers into symbol
            all_tickers = list(dict.fromkeys(t for tickers in tickers_by_slug.values() for t in tickers))
            inserted = 0
            for ticker in all_tickers:
                if not ticker:
                    continue
                cur.execute(
                    "INSERT INTO symbol (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING",
                    (ticker,),
                )
                if cur.rowcount:
                    inserted += 1
            print(f"  Symbols:  {inserted} new, {len(all_tickers)} total unique")

            # Get symbol ids
            cur.execute("SELECT id, ticker FROM symbol WHERE ticker = ANY(%s)", (all_tickers,))
            ticker_to_id = {row[1]: row[0] for row in cur.fetchall()}

            # Link each universe's tickers
            for slug, (display_name, _csv_path) in UNIVERSES.items():
                uid = universe_ids[slug]
                links = [
                    (uid, ticker_to_id[t])
                    for t in tickers_by_slug[slug]
                    if t in ticker_to_id
                ]
                for u, sid in links:
                    cur.execute(
                        """
                        INSERT INTO universe_symbol (universe_id, symbol_id)
                        VALUES (%s, %s) ON CONFLICT (universe_id, symbol_id) DO NOTHING
                        """,
                        (u, sid),
                    )
                print(f"  {display_name}: {len(links)} universe_symbol links")

    print("Done. DB is seeded with universes and symbols.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed DB from sp500, nasdaq100, sp400, and sp600 CSVs.")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete all existing data before seeding (full reload).",
    )
    args = parser.parse_args()
    try:
        main(clean_first=args.clean)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
