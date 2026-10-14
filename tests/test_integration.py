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

from tlt_weekly_pivot_backtest import run_backtest
from tlt_weekly_pivot_scanner import run_scan


class PipelineIntegrationTest(unittest.TestCase):
    def test_scanner_and_backtest_write_derived_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "synthetic.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE relationship_bars_day (
                    symbol TEXT NOT NULL,
                    bar_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    PRIMARY KEY(symbol, bar_date)
                )
                """
            )
            first_friday = date(2024, 1, 5)
            symbols = ["TLT", "SPY", "QQQ", "DIA", "IWM", "IWN"]
            rows: list[tuple[object, ...]] = []
            tlt_close = 110.0
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
                tlt_close = close
                rows.append(("TLT", bar_date, opening, high, low, close, 2_000_000 + week))
                for offset, symbol in enumerate(symbols[1:], start=1):
                    index_close = 100.0 + offset * 10.0 + week * 0.35
                    rows.append(
                        (
                            symbol,
                            bar_date,
                            index_close - 0.1,
                            index_close + 0.5,
                            index_close - 0.5,
                            index_close,
                            1_000_000 + week,
                        )
                    )
            connection.executemany(
                "INSERT INTO relationship_bars_day VALUES (?,?,?,?,?,?,?)", rows
            )
            connection.commit()
            connection.close()

            scan = run_scan(PROJECT_DIR / "config.json", database)
            self.assertGreaterEqual(scan["pivot_rows"], 1)
            self.assertGreaterEqual(scan["confirmed_rows"], 1)

            backtest = run_backtest(PROJECT_DIR / "config.json", database)
            self.assertGreater(backtest["detail_rows"], 0)
            self.assertEqual(backtest["symbols"], ["DIA", "IWM", "IWN", "QQQ", "SPY"])

            connection = sqlite3.connect(database)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                connection.close()
            self.assertTrue(
                {
                    "tlt_weekly_bars",
                    "tlt_weekly_pivots",
                    "tlt_weekly_pivot_backtest",
                    "tlt_weekly_pivot_backtest_summary",
                }.issubset(tables)
            )

    def test_split_tables_are_discovered_and_missing_symbol_is_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "split_sources.sqlite"
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
                CREATE TABLE index_prices_day (
                    ticker TEXT, bar_date TEXT, open REAL, high REAL,
                    low REAL, close REAL, volume REAL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE dia_bars_day (
                    date TEXT, open REAL, high REAL, low REAL, close REAL
                )
                """
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
                for offset, symbol in enumerate(("SPY", "QQQ", "IWM"), start=1):
                    index_close = 100.0 + offset * 10.0 + week * 0.35
                    connection.execute(
                        "INSERT INTO index_prices_day VALUES (?,?,?,?,?,?,?)",
                        (
                            symbol,
                            bar_date,
                            index_close - 0.1,
                            index_close + 0.5,
                            index_close - 0.5,
                            index_close,
                            1_000_000 + week,
                        ),
                    )
                dia_close = 140.0 + week * 0.25
                connection.execute(
                    "INSERT INTO dia_bars_day VALUES (?,?,?,?,?)",
                    (bar_date, dia_close - 0.1, dia_close + 0.4, dia_close - 0.4, dia_close),
                )
            connection.commit()
            connection.close()

            scan = run_scan(PROJECT_DIR / "config.json", database)
            self.assertGreaterEqual(scan["confirmed_rows"], 1)
            backtest = run_backtest(PROJECT_DIR / "config.json", database)

            self.assertEqual(backtest["missing_symbols"], ["IWN"])
            self.assertEqual(
                backtest["symbols"], ["DIA", "IWM", "QQQ", "SPY"]
            )
            self.assertEqual(backtest["sources"]["TLT"], "relationship_bars_day")
            self.assertEqual(backtest["sources"]["SPY"], "index_prices_day")
            self.assertEqual(backtest["sources"]["DIA"], "dia_bars_day")

            connection = sqlite3.connect(database)
            try:
                status = dict(
                    connection.execute(
                        "SELECT symbol, available FROM tlt_weekly_pivot_source_status"
                    ).fetchall()
                )
            finally:
                connection.close()
            self.assertEqual(status["IWN"], 0)
            self.assertEqual(status["SPY"], 1)


if __name__ == "__main__":
    unittest.main()
