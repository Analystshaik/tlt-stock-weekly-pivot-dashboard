from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

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
from tlt_weekly_pivot_backtest import calculate_backtest, summarize_backtest


def _confirmed_pivots(
    connection: sqlite3.Connection, config: dict[str, Any]
) -> pd.DataFrame:
    table = config["pivots_table"]
    if not table_exists(connection, table):
        raise RuntimeError("TLT pivot table not found. Run the main dashboard analysis first.")
    table_sql = quoted_identifier(table)
    frame = pd.read_sql_query(
        f"""
        SELECT bar_date, pivot_type, direction, pivot_strength,
               pivot_score, patterns, confirmed
        FROM {table_sql}
        WHERE confirmed=1
        ORDER BY bar_date
        """,
        connection,
    )
    if frame.empty:
        raise RuntimeError("No confirmed TLT weekly pivots are available.")
    return frame


def build_stock_analysis(
    database: str | Path,
    symbol: str,
    benchmark: str,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    config["database"] = str(database)
    symbol = symbol.strip().upper().replace(".", " ")
    benchmark = benchmark.strip().upper()
    reference = str(config["reference_symbol"]).upper()
    configured_sources = config.get("symbol_source_tables", {})
    connection = connect_database(database)
    frames: dict[str, pd.DataFrame] = {}
    sources: dict[str, str] = {}
    daily_rows: dict[str, int] = {}
    missing_optional: list[str] = []
    try:
        pivots = _confirmed_pivots(connection, config)
        required_symbols = list(dict.fromkeys([reference, symbol, benchmark]))
        for current_symbol in required_symbols:
            try:
                daily, source = load_symbol_daily_bars(
                    connection,
                    current_symbol,
                    preferred_table=config.get("source_table"),
                    symbol_source_tables=configured_sources,
                )
            except RuntimeError:
                if current_symbol in (reference, symbol):
                    raise RuntimeError(
                        f"No stored daily OHLC data was found for {current_symbol}."
                    )
                missing_optional.append(current_symbol)
                continue
            weekly = weekly_with_period_key(resample_daily_to_weekly(daily))
            frames[current_symbol] = weekly.sort_values("bar_date").reset_index(drop=True)
            sources[current_symbol] = source.label
            daily_rows[current_symbol] = len(daily)
    finally:
        connection.close()

    compared_symbols = [value for value in (symbol, benchmark) if value in frames]
    comparison_config = dict(config)
    comparison_config["index_symbols"] = list(dict.fromkeys(compared_symbols))
    detail = calculate_backtest(pivots, frames, comparison_config)
    summary = summarize_backtest(detail)
    return {
        "symbol": symbol,
        "benchmark": benchmark,
        "pivots": pivots,
        "detail": detail,
        "summary": summary,
        "weekly_frames": frames,
        "sources": sources,
        "daily_rows": daily_rows,
        "missing_optional": missing_optional,
    }


def normalized_event_paths(
    weekly_frames: dict[str, pd.DataFrame],
    pivot_date: str | pd.Timestamp,
    pre_weeks: int = 4,
    post_weeks: int = 8,
) -> pd.DataFrame:
    pivot_key = str(pd.Timestamp(pivot_date).to_period("W-FRI"))
    output: list[pd.DataFrame] = []
    for symbol, frame in weekly_frames.items():
        work = frame.copy()
        if "week_key" not in work.columns:
            work["week_key"] = pd.to_datetime(work["bar_date"]).dt.to_period("W-FRI").astype(str)
        matches = work.index[work["week_key"] == pivot_key]
        if matches.empty:
            continue
        pivot_index = int(matches[0])
        base = float(work.iloc[pivot_index]["close"])
        if base == 0:
            continue
        start = max(0, pivot_index - pre_weeks)
        stop = min(len(work), pivot_index + post_weeks + 1)
        sample = work.iloc[start:stop].copy()
        sample["week_offset"] = sample.index - pivot_index
        sample["normalized_close"] = sample["close"].astype(float) / base * 100.0
        sample["symbol"] = symbol.replace(" ", ".")
        output.append(
            sample[["bar_date", "week_offset", "symbol", "normalized_close"]]
        )
    return pd.concat(output, ignore_index=True) if output else pd.DataFrame()
