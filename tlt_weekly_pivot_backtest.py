from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from pivot_core import (
    connect_database,
    load_config,
    load_symbol_daily_bars,
    quoted_identifier,
    resample_daily_to_weekly,
    table_exists,
    weekly_with_period_key,
)


def initialize_backtest_tables(
    connection: sqlite3.Connection, config: dict[str, Any]
) -> None:
    detail = quoted_identifier(config["backtest_table"])
    summary = quoted_identifier(config["backtest_summary_table"])
    status = quoted_identifier(config["source_status_table"])
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {detail} (
            pivot_date TEXT NOT NULL,
            pivot_type TEXT NOT NULL,
            pivot_direction TEXT NOT NULL,
            pivot_strength TEXT NOT NULL,
            pivot_score INTEGER NOT NULL,
            patterns TEXT NOT NULL,
            index_symbol TEXT NOT NULL,
            horizon_weeks INTEGER NOT NULL,
            entry_date TEXT NOT NULL,
            exit_date TEXT NOT NULL,
            entry_close REAL NOT NULL,
            exit_close REAL NOT NULL,
            index_return_pct REAL NOT NULL,
            tlt_return_pct REAL,
            index_minus_tlt_pct REAL,
            expected_index_direction TEXT NOT NULL,
            expectation_adjusted_return_pct REAL NOT NULL,
            expectation_hit INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(pivot_date, pivot_type, index_symbol, horizon_weeks)
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {status} (
            symbol TEXT PRIMARY KEY,
            available INTEGER NOT NULL,
            source_table TEXT,
            source_label TEXT,
            daily_rows INTEGER NOT NULL,
            first_date TEXT,
            last_date TEXT,
            message TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {summary} (
            pivot_direction TEXT NOT NULL,
            index_symbol TEXT NOT NULL,
            horizon_weeks INTEGER NOT NULL,
            pivot_count INTEGER NOT NULL,
            average_return_pct REAL,
            median_return_pct REAL,
            return_std_pct REAL,
            average_tlt_return_pct REAL,
            average_expectation_adjusted_return_pct REAL,
            expectation_hit_rate_pct REAL,
            last_pivot_date TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY(pivot_direction, index_symbol, horizon_weeks)
        )
        """
    )
    connection.commit()


def _load_pivots(
    connection: sqlite3.Connection, config: dict[str, Any]
) -> pd.DataFrame:
    table = config["pivots_table"]
    if not table_exists(connection, table):
        raise RuntimeError("Pivot table not found. Run tlt_weekly_pivot_scanner.py first.")
    table_sql = quoted_identifier(table)
    confirmed_clause = "WHERE confirmed=1" if config.get("backtest_confirmed_only", True) else ""
    pivots = pd.read_sql_query(
        f"""
        SELECT bar_date, pivot_type, direction, pivot_strength,
               pivot_score, patterns, confirmed
        FROM {table_sql}
        {confirmed_clause}
        ORDER BY bar_date
        """,
        connection,
    )
    if pivots.empty:
        qualifier = "confirmed " if config.get("backtest_confirmed_only", True) else ""
        raise RuntimeError(f"No {qualifier}pivots are available to backtest.")
    return pivots


def _build_weekly_universe(
    connection: sqlite3.Connection, config: dict[str, Any]
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, list[str]]:
    symbols = [str(config["reference_symbol"]).upper()] + [
        str(value).upper() for value in config["index_symbols"]
    ]
    frames: dict[str, pd.DataFrame] = {}
    status_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    configured_sources = {
        str(key).upper(): str(value)
        for key, value in config.get("symbol_source_tables", {}).items()
        if str(value).strip()
    }
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for symbol in dict.fromkeys(symbols):
        try:
            daily, source = load_symbol_daily_bars(
                connection,
                symbol,
                preferred_table=config.get("source_table"),
                symbol_source_tables=configured_sources,
            )
        except RuntimeError as exc:
            missing.append(symbol)
            status_rows.append(
                {
                    "symbol": symbol,
                    "available": 0,
                    "source_table": None,
                    "source_label": None,
                    "daily_rows": 0,
                    "first_date": None,
                    "last_date": None,
                    "message": str(exc),
                    "updated_at": now,
                }
            )
            if symbol == str(config["reference_symbol"]).upper():
                raise
            continue
        weekly = weekly_with_period_key(resample_daily_to_weekly(daily))
        weekly = weekly.sort_values("bar_date").reset_index(drop=True)
        frames[symbol] = weekly
        status_rows.append(
            {
                "symbol": symbol,
                "available": 1,
                "source_table": source.table,
                "source_label": source.label,
                "daily_rows": len(daily),
                "first_date": pd.Timestamp(daily["bar_date"].min()).date().isoformat(),
                "last_date": pd.Timestamp(daily["bar_date"].max()).date().isoformat(),
                "message": "Ready",
                "updated_at": now,
            }
        )
    return frames, pd.DataFrame(status_rows), missing


def calculate_backtest(
    pivots: pd.DataFrame,
    weekly_frames: dict[str, pd.DataFrame],
    config: dict[str, Any],
) -> pd.DataFrame:
    reference_symbol = str(config["reference_symbol"]).upper()
    horizons = sorted({int(value) for value in config["backtest_horizons_weeks"]})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    reference = weekly_frames[reference_symbol]
    reference_lookup = {key: index for index, key in enumerate(reference["week_key"])}

    for pivot in pivots.itertuples(index=False):
        pivot_timestamp = pd.Timestamp(pivot.bar_date)
        pivot_key = str(pivot_timestamp.to_period("W-FRI"))
        reference_index = reference_lookup.get(pivot_key)
        if reference_index is None:
            continue
        tlt_entry_close = float(reference.iloc[reference_index]["close"])
        for symbol in [str(value).upper() for value in config["index_symbols"]]:
            if symbol not in weekly_frames:
                continue
            frame = weekly_frames[symbol]
            lookup = {key: index for index, key in enumerate(frame["week_key"])}
            entry_index = lookup.get(pivot_key)
            if entry_index is None:
                continue
            entry = frame.iloc[entry_index]
            entry_close = float(entry["close"])
            if entry_close == 0:
                continue
            for horizon in horizons:
                exit_index = entry_index + horizon
                tlt_exit_index = reference_index + horizon
                if exit_index >= len(frame) or tlt_exit_index >= len(reference):
                    continue
                exit_bar = frame.iloc[exit_index]
                tlt_exit = reference.iloc[tlt_exit_index]
                index_return = (float(exit_bar["close"]) / entry_close - 1.0) * 100.0
                tlt_return = (
                    (float(tlt_exit["close"]) / tlt_entry_close - 1.0) * 100.0
                    if tlt_entry_close
                    else np.nan
                )
                expected_direction = "DOWN" if pivot.direction == "BULLISH" else "UP"
                direction_multiplier = -1.0 if expected_direction == "DOWN" else 1.0
                adjusted = index_return * direction_multiplier
                rows.append(
                    {
                        "pivot_date": pivot_timestamp.date().isoformat(),
                        "pivot_type": pivot.pivot_type,
                        "pivot_direction": pivot.direction,
                        "pivot_strength": pivot.pivot_strength,
                        "pivot_score": int(pivot.pivot_score),
                        "patterns": pivot.patterns,
                        "index_symbol": symbol,
                        "horizon_weeks": horizon,
                        "entry_date": pd.Timestamp(entry["bar_date"]).date().isoformat(),
                        "exit_date": pd.Timestamp(exit_bar["bar_date"]).date().isoformat(),
                        "entry_close": entry_close,
                        "exit_close": float(exit_bar["close"]),
                        "index_return_pct": index_return,
                        "tlt_return_pct": tlt_return,
                        "index_minus_tlt_pct": index_return - tlt_return,
                        "expected_index_direction": expected_direction,
                        "expectation_adjusted_return_pct": adjusted,
                        "expectation_hit": int(adjusted > 0),
                        "created_at": now,
                    }
                )
    return pd.DataFrame(rows)


def summarize_backtest(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame()
    summary = (
        detail.groupby(["pivot_direction", "index_symbol", "horizon_weeks"], as_index=False)
        .agg(
            pivot_count=("pivot_date", "nunique"),
            average_return_pct=("index_return_pct", "mean"),
            median_return_pct=("index_return_pct", "median"),
            return_std_pct=("index_return_pct", "std"),
            average_tlt_return_pct=("tlt_return_pct", "mean"),
            average_expectation_adjusted_return_pct=(
                "expectation_adjusted_return_pct",
                "mean",
            ),
            expectation_hit_rate_pct=("expectation_hit", "mean"),
            last_pivot_date=("pivot_date", "max"),
        )
    )
    summary["expectation_hit_rate_pct"] *= 100.0
    summary["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return summary


def persist_backtest(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    detail: pd.DataFrame,
    summary: pd.DataFrame,
    status: pd.DataFrame,
) -> None:
    detail_table = quoted_identifier(config["backtest_table"])
    summary_table = quoted_identifier(config["backtest_summary_table"])
    status_table = quoted_identifier(config["source_status_table"])
    with connection:
        connection.execute(f"DELETE FROM {detail_table}")
        connection.execute(f"DELETE FROM {summary_table}")
        connection.execute(f"DELETE FROM {status_table}")
        if not detail.empty:
            detail.to_sql(config["backtest_table"], connection, if_exists="append", index=False)
        if not summary.empty:
            summary.to_sql(
                config["backtest_summary_table"],
                connection,
                if_exists="append",
                index=False,
            )
        if not status.empty:
            status.to_sql(
                config["source_status_table"], connection, if_exists="append", index=False
            )


def run_backtest(
    config_path: str | Path | None = None,
    database_override: str | Path | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    if database_override:
        config["database"] = str(database_override)
    connection = connect_database(config["database"])
    try:
        initialize_backtest_tables(connection, config)
        pivots = _load_pivots(connection, config)
        weekly_frames, status, missing = _build_weekly_universe(connection, config)
        detail = calculate_backtest(pivots, weekly_frames, config)
        summary = summarize_backtest(detail)
        persist_backtest(connection, config, detail, summary, status)
    finally:
        connection.close()
    return {
        "database": config["database"],
        "pivots_tested": int(detail["pivot_date"].nunique()) if not detail.empty else 0,
        "detail_rows": len(detail),
        "summary_rows": len(summary),
        "symbols": sorted(detail["index_symbol"].unique().tolist()) if not detail.empty else [],
        "missing_symbols": missing,
        "sources": {
            row.symbol: row.source_table
            for row in status[status["available"] == 1].itertuples(index=False)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest SPY/QQQ/DIA/IWM/IWN after TLT weekly pivots."
    )
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--database", default=None, help="Optional SQLite path override")
    args = parser.parse_args()
    result = run_backtest(args.config, args.database)
    print(
        f"Backtest complete: {result['pivots_tested']:,} pivots, "
        f"{result['detail_rows']:,} forward-return rows, "
        f"{result['summary_rows']:,} summary rows."
    )
    print(
        "Detected sources: "
        + ", ".join(f"{symbol}={table}" for symbol, table in result["sources"].items())
    )
    if result["missing_symbols"]:
        print(
            "Skipped unavailable symbols: " + ", ".join(result["missing_symbols"])
        )


if __name__ == "__main__":
    main()
