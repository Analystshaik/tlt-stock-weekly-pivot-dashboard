from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from stock_reaction import build_stock_analysis, normalized_event_paths
from tlt_weekly_pivot_scanner import run_scan
from universe_core import discover_available_bar_symbols, load_universe


class StockReactionTests(unittest.TestCase):
    def test_bundled_universes_load(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            dow, _ = load_universe(connection, "DOW30")
            qqq, _ = load_universe(connection, "QQQ")
        finally:
            connection.close()
        self.assertEqual(len(dow), 30)
        self.assertGreaterEqual(len(qqq), 100)
        self.assertIn("AAPL", set(dow["ticker"]))
        self.assertIn("AAPL", set(qqq["ticker"]))

    def test_stock_reaction_pipeline_and_spy_membership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "stock_reaction.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE relationship_bars_day (
                    symbol TEXT, bar_date TEXT, open REAL, high REAL,
                    low REAL, close REAL, volume REAL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE stock_bars_day (
                    ticker TEXT, bar_date TEXT, open REAL, high REAL,
                    low REAL, close REAL, volume REAL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE spdr_sector_holdings_daily (
                    FundTicker TEXT, HoldingsAsOf TEXT, Name TEXT,
                    Ticker TEXT, Weight REAL
                )
                """
            )
            connection.execute(
                "INSERT INTO spdr_sector_holdings_daily VALUES (?,?,?,?,?)",
                ("XLK", "2026-10-09", "Apple", "AAPL", 7.0),
            )
            first_friday = date(2024, 1, 5)
            for week in range(80):
                bar_date = (first_friday + timedelta(weeks=week)).isoformat()
                if week < 30:
                    close = 110.0 - 0.5 * week
                    opening, high, low = close + 0.3, close + 0.8, close - 0.5
                elif week == 30:
                    opening, high, low, close = 95.0, 97.0, 94.0, 96.8
                else:
                    close = 96.8 + 0.25 * (week - 30)
                    opening, high, low = close - 0.15, close + 0.45, close - 0.45
                connection.execute(
                    "INSERT INTO relationship_bars_day VALUES (?,?,?,?,?,?,?)",
                    ("TLT", bar_date, opening, high, low, close, 2_000_000 + week),
                )
                for offset, symbol in enumerate(("AAPL", "SPY"), start=1):
                    stock_close = 120.0 + offset * 10.0 + week * 0.45
                    connection.execute(
                        "INSERT INTO stock_bars_day VALUES (?,?,?,?,?,?,?)",
                        (
                            symbol,
                            bar_date,
                            stock_close - 0.2,
                            stock_close + 0.6,
                            stock_close - 0.6,
                            stock_close,
                            1_000_000 + week,
                        ),
                    )
            connection.commit()
            connection.close()

            run_scan(PROJECT_DIR / "config.json", database)
            result = build_stock_analysis(
                database, "AAPL", "SPY", PROJECT_DIR / "config.json"
            )
            self.assertGreater(len(result["detail"]), 0)
            self.assertEqual(set(result["detail"]["index_symbol"]), {"AAPL", "SPY"})
            self.assertGreater(len(result["summary"]), 0)
            paths = normalized_event_paths(
                result["weekly_frames"], result["detail"].iloc[0]["pivot_date"]
            )
            self.assertEqual(set(paths["symbol"]), {"TLT", "AAPL", "SPY"})

            connection = sqlite3.connect(database)
            try:
                spy, source = load_universe(connection, "SPY")
                available = discover_available_bar_symbols(connection)
            finally:
                connection.close()
            self.assertEqual(source, "spdr_sector_holdings_daily")
            self.assertIn("AAPL", set(spy["ticker"]))
            self.assertTrue({"AAPL", "SPY", "TLT"}.issubset(available))


if __name__ == "__main__":
    unittest.main()
