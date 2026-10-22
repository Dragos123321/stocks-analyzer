"""
One-time migration: add fair-value/investment-score columns to ticker_snapshot.

Run from the stocks-analyzer directory:
  python scripts/migrate_add_valuation_columns.py

Adds (if not already present): trailing_eps, book_value_per_share, free_cashflow,
shares_outstanding, dividend_rate, debt_to_equity. Safe to run multiple times
(uses ADD COLUMN IF NOT EXISTS) -- re-running after a later code update just adds
whatever new columns haven't been created yet.

After running, re-run "Fetch latest data" for each universe (POST
/api/screening/fetch-latest?universe=...) so these columns get populated --
existing snapshot rows will have NULLs here until refreshed, and the
valuation/ package falls back to live yfinance for any ticker whose cached
row predates this migration.
"""
from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_STATEMENTS = [
    "ALTER TABLE ticker_snapshot ADD COLUMN IF NOT EXISTS trailing_eps DOUBLE PRECISION",
    "ALTER TABLE ticker_snapshot ADD COLUMN IF NOT EXISTS book_value_per_share DOUBLE PRECISION",
    "ALTER TABLE ticker_snapshot ADD COLUMN IF NOT EXISTS free_cashflow DOUBLE PRECISION",
    "ALTER TABLE ticker_snapshot ADD COLUMN IF NOT EXISTS shares_outstanding DOUBLE PRECISION",
    "ALTER TABLE ticker_snapshot ADD COLUMN IF NOT EXISTS dividend_rate DOUBLE PRECISION",
    "ALTER TABLE ticker_snapshot ADD COLUMN IF NOT EXISTS debt_to_equity DOUBLE PRECISION",
]


def main() -> None:
    from db.connection import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            for stmt in _STATEMENTS:
                print(f"Running: {stmt}")
                cur.execute(stmt)
    print("Done. Re-run 'Fetch latest data' for each universe to populate the new columns.")


if __name__ == "__main__":
    main()
