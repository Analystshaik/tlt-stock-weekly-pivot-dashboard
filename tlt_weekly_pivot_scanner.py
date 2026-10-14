from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from pivot_core import (
    connect_database,
    load_config,
    load_daily_bars,
    quoted_identifier,
    resample_daily_to_weekly,
    scan_weekly_pivots,
)


def initialize_output_tables(connection: sqlite3.Connection, config: dict[str, Any]) -> None:
    weekly_table = quoted_identifier(config["weekly_bars_table"])
    pivots_table = quoted_identifier(config["pivots_table"])
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {weekly_table} (
            symbol TEXT NOT NULL,
            bar_date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL,
            PRIMARY KEY(symbol, bar_date)
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {pivots_table} (
            symbol TEXT NOT NULL,
            bar_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            pivot_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            patterns TEXT NOT NULL,
            pivot_score INTEGER NOT NULL,
            pivot_strength TEXT NOT NULL,
            confirmed INTEGER NOT NULL,
            confirmation_status TEXT NOT NULL,
            confirmation_date TEXT,
            pivot_price REAL NOT NULL,
            is_5_week_extreme INTEGER NOT NULL,
            is_10_week_extreme INTEGER NOT NULL,
            prior_trend_confirmed INTEGER NOT NULL,
            prior_move_pct REAL,
            close_in_strongest_quartile INTEGER NOT NULL,
            above_average_volume INTEGER NOT NULL,
            average_volume_20w REAL,
            score_detail TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY(symbol, bar_date, pivot_type)
        )
        """
    )
    index_name = quoted_identifier(f"idx_{config['pivots_table']}_date_score")
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS {index_name} "
        f"ON {pivots_table}(bar_date DESC, pivot_score DESC)"
    )
    connection.commit()


def persist_scan(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    weekly: pd.DataFrame,
    pivots: pd.DataFrame,
) -> None:
    symbol = str(config["reference_symbol"]).upper()
    weekly_table = quoted_identifier(config["weekly_bars_table"])
    pivots_table = quoted_identifier(config["pivots_table"])
    weekly_out = weekly.copy()
    weekly_out["bar_date"] = pd.to_datetime(weekly_out["bar_date"]).dt.date.astype(str)
    with connection:
        connection.execute(f"DELETE FROM {weekly_table} WHERE symbol=?", (symbol,))
        connection.execute(f"DELETE FROM {pivots_table} WHERE symbol=?", (symbol,))
        weekly_out.to_sql(
            config["weekly_bars_table"], connection, if_exists="append", index=False
        )
        if not pivots.empty:
            pivots.to_sql(config["pivots_table"], connection, if_exists="append", index=False)


def run_scan(
    config_path: str | Path | None = None,
    database_override: str | Path | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    if database_override:
        config["database"] = str(database_override)
    symbol = str(config["reference_symbol"]).upper()
    connection = connect_database(config["database"])
    try:
        daily = load_daily_bars(connection, config["source_table"], symbol)
        weekly = resample_daily_to_weekly(daily)
        pivots = scan_weekly_pivots(weekly, config)
        initialize_output_tables(connection, config)
        persist_scan(connection, config, weekly, pivots)
    finally:
        connection.close()
    return {
        "database": config["database"],
        "symbol": symbol,
        "daily_rows": len(daily),
        "weekly_rows": len(weekly),
        "pivot_rows": len(pivots),
        "confirmed_rows": int(pivots["confirmed"].sum()) if not pivots.empty else 0,
        "latest_week": (
            pd.Timestamp(weekly["bar_date"].max()).date().isoformat() if not weekly.empty else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan TLT weekly bars for strong pivots.")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--database", default=None, help="Optional SQLite path override")
    args = parser.parse_args()
    result = run_scan(args.config, args.database)
    print(
        f"TLT weekly scan complete: {result['daily_rows']:,} daily rows -> "
        f"{result['weekly_rows']:,} weekly bars; {result['pivot_rows']:,} strong candidates, "
        f"{result['confirmed_rows']:,} confirmed. Latest week: {result['latest_week']}."
    )


if __name__ == "__main__":
    main()
