from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.json"
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DATE_COLUMN_ALIASES = ("bar_date", "date", "datetime", "timestamp")
SYMBOL_COLUMN_ALIASES = ("symbol", "ticker")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def quoted_identifier(name: str) -> str:
    if not IDENTIFIER_RE.fullmatch(str(name)):
        raise ValueError(f"Unsafe SQLite identifier: {name!r}")
    return f'"{name}"'


def connect_database(database: str | Path) -> sqlite3.Connection:
    database_path = Path(database).expanduser()
    if not database_path.parent.exists():
        raise FileNotFoundError(
            f"Database folder does not exist: {database_path.parent}. "
            "Update database in config.json or in the dashboard sidebar."
        )
    connection = sqlite3.connect(str(database_path), timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def source_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    table_sql = quoted_identifier(table)
    return {
        str(row[1]).lower()
        for row in connection.execute(f"PRAGMA table_info({table_sql})").fetchall()
    }


@dataclass(frozen=True)
class BarSource:
    table: str
    date_column: str
    symbol_column: str | None
    volume_column: str | None

    @property
    def label(self) -> str:
        if self.symbol_column:
            return f"{self.table}.{self.symbol_column}"
        return self.table


def _column_map(connection: sqlite3.Connection, table: str) -> dict[str, str]:
    table_sql = quoted_identifier(table)
    return {
        str(row[1]).lower(): str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table_sql})").fetchall()
    }


def _database_tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    ]


def _table_name_matches_symbol(table: str, symbol: str) -> bool:
    tokens = [token for token in re.split(r"[^A-Za-z0-9]+", table.upper()) if token]
    return symbol.upper() in tokens or table.upper().startswith(symbol.upper())


def discover_bar_source(
    connection: sqlite3.Connection,
    symbol: str,
    preferred_table: str | None = None,
    symbol_source_tables: dict[str, str] | None = None,
) -> BarSource | None:
    """Locate daily OHLC bars for a symbol anywhere in the SQLite database.

    Supports shared tables using either symbol or ticker, plus symbol-specific
    tables such as spy_bars_day that do not carry a symbol column.
    """
    symbol = symbol.upper()
    configured_table = str((symbol_source_tables or {}).get(symbol, "")).strip() or None
    tables = _database_tables(connection)
    ranked: list[tuple[int, BarSource]] = []
    for table in tables:
        columns = _column_map(connection, table)
        if not {"open", "high", "low", "close"}.issubset(columns):
            continue
        date_column = next(
            (columns[alias] for alias in DATE_COLUMN_ALIASES if alias in columns), None
        )
        if date_column is None:
            continue
        symbol_column = next(
            (columns[alias] for alias in SYMBOL_COLUMN_ALIASES if alias in columns), None
        )
        table_matches = _table_name_matches_symbol(table, symbol)
        table_sql = quoted_identifier(table)
        if symbol_column:
            symbol_sql = quoted_identifier(symbol_column)
            has_rows = connection.execute(
                f"SELECT 1 FROM {table_sql} "
                f"WHERE REPLACE(UPPER({symbol_sql}),'.',' ')=REPLACE(UPPER(?),'.',' ') LIMIT 1",
                (symbol,),
            ).fetchone()
            if not has_rows:
                continue
        else:
            if not table_matches:
                continue
            has_rows = connection.execute(f"SELECT 1 FROM {table_sql} LIMIT 1").fetchone()
            if not has_rows:
                continue

        score = 100
        if symbol_column:
            score += 400
        if table_matches:
            score += 600
        if preferred_table and table.lower() == preferred_table.lower():
            score += 800
        if configured_table and table.lower() == configured_table.lower():
            score += 1_500
        if date_column.lower() == "bar_date":
            score += 20
        ranked.append(
            (
                score,
                BarSource(
                    table=table,
                    date_column=date_column,
                    symbol_column=symbol_column,
                    volume_column=columns.get("volume"),
                ),
            )
        )
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1].table.lower()))
    return ranked[0][1]


def load_symbol_daily_bars(
    connection: sqlite3.Connection,
    symbol: str,
    preferred_table: str | None = None,
    symbol_source_tables: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, BarSource]:
    source = discover_bar_source(
        connection,
        symbol,
        preferred_table=preferred_table,
        symbol_source_tables=symbol_source_tables,
    )
    if source is None:
        raise RuntimeError(
            f"No daily OHLC rows for {symbol.upper()} were found in any SQLite table."
        )
    table_sql = quoted_identifier(source.table)
    date_sql = quoted_identifier(source.date_column)
    volume_sql = quoted_identifier(source.volume_column) if source.volume_column else "0.0"
    if source.symbol_column:
        symbol_sql = quoted_identifier(source.symbol_column)
        query = f"""
            SELECT {symbol_sql} AS symbol, {date_sql} AS bar_date,
                   open, high, low, close, {volume_sql} AS volume
            FROM {table_sql}
            WHERE REPLACE(UPPER({symbol_sql}),'.',' ')=REPLACE(UPPER(?),'.',' ')
            ORDER BY {date_sql}
        """
        frame = pd.read_sql_query(query, connection, params=(symbol,))
    else:
        query = f"""
            SELECT ? AS symbol, {date_sql} AS bar_date,
                   open, high, low, close, {volume_sql} AS volume
            FROM {table_sql}
            ORDER BY {date_sql}
        """
        frame = pd.read_sql_query(query, connection, params=(symbol,))
    frame = _normalize_daily_bars(frame, symbol)
    if frame.empty:
        raise RuntimeError(f"No usable daily OHLC rows for {symbol.upper()} were found.")
    return frame, source


def validate_source_table(connection: sqlite3.Connection, table: str) -> None:
    if not table_exists(connection, table):
        raise RuntimeError(f"Source table {table!r} was not found in the database.")
    required = {"symbol", "bar_date", "open", "high", "low", "close", "volume"}
    missing = required - source_columns(connection, table)
    if missing:
        raise RuntimeError(
            f"Source table {table!r} is missing required columns: {sorted(missing)}"
        )


def load_daily_bars(
    connection: sqlite3.Connection, source_table: str, symbol: str
) -> pd.DataFrame:
    validate_source_table(connection, source_table)
    table_sql = quoted_identifier(source_table)
    query = f"""
        SELECT symbol, bar_date, open, high, low, close, volume
        FROM {table_sql}
        WHERE UPPER(symbol) = UPPER(?)
        ORDER BY bar_date
    """
    frame = pd.read_sql_query(query, connection, params=(symbol,))
    if frame.empty:
        raise RuntimeError(f"No rows for {symbol.upper()} were found in {source_table!r}.")

    return _normalize_daily_bars(frame, symbol)


def _normalize_daily_bars(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = frame.copy()
    frame["bar_date"] = pd.to_datetime(frame["bar_date"], errors="coerce", utc=True)
    frame["bar_date"] = frame["bar_date"].dt.tz_convert(None).dt.normalize()
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["bar_date", "open", "high", "low", "close"])
    frame["volume"] = frame["volume"].fillna(0.0)
    frame["symbol"] = symbol.upper()
    frame = frame.sort_values("bar_date").drop_duplicates("bar_date", keep="last")
    return frame.reset_index(drop=True)


def resample_daily_to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily bars into exchange weeks ending Friday.

    bar_date is the last actual trading date in each week, not a synthetic Friday.
    """
    if daily.empty:
        return pd.DataFrame(
            columns=["symbol", "bar_date", "open", "high", "low", "close", "volume"]
        )
    work = daily.copy()
    work["week_key"] = work["bar_date"].dt.to_period("W-FRI").astype(str)
    weekly = (
        work.groupby("week_key", sort=True)
        .agg(
            symbol=("symbol", "last"),
            bar_date=("bar_date", "max"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .reset_index(drop=True)
    )
    numeric = ["open", "high", "low", "close", "volume"]
    weekly[numeric] = weekly[numeric].astype(float)
    return weekly.sort_values("bar_date").reset_index(drop=True)


def _bar_parts(row: pd.Series) -> dict[str, float]:
    opening = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    total_range = max(high - low, 0.0)
    body = abs(close - opening)
    body_floor = max(body, total_range * 0.02, 1e-10)
    return {
        "open": opening,
        "high": high,
        "low": low,
        "close": close,
        "range": total_range,
        "body": body,
        "body_floor": body_floor,
        "upper_wick": max(high - max(opening, close), 0.0),
        "lower_wick": max(min(opening, close) - low, 0.0),
        "close_location": (close - low) / total_range if total_range > 0 else 0.5,
    }


def classify_strong_patterns(
    previous: pd.Series,
    current: pd.Series,
    average_prior_body: float,
    settings: dict[str, Any],
) -> dict[str, bool]:
    """Return only the six approved strong reversal variants."""
    prior = _bar_parts(previous)
    bar = _bar_parts(current)
    tolerance = float(settings["gap_tolerance_pct"]) / 100.0
    prior_body_pct = (
        prior["body"] / prior["range"] * 100.0 if prior["range"] > 0 else 0.0
    )
    meaningful_prior_body = (
        prior_body_pct >= float(settings["piercing_min_prior_body_pct_range"])
        and prior["body"]
        >= max(float(average_prior_body), 1e-10)
        * float(settings["piercing_min_prior_body_vs_average"])
    )
    prior_midpoint = (prior["open"] + prior["close"]) / 2.0

    bullish_piercing = bool(
        meaningful_prior_body
        and prior["close"] < prior["open"]
        and bar["close"] > bar["open"]
        and bar["open"] <= prior["close"] * (1.0 + tolerance)
        and bar["close"] > prior_midpoint
        and bar["close"] < prior["open"] * (1.0 + tolerance)
    )
    bearish_dark_cloud = bool(
        meaningful_prior_body
        and prior["close"] > prior["open"]
        and bar["close"] < bar["open"]
        and bar["open"] >= prior["close"] * (1.0 - tolerance)
        and bar["close"] < prior_midpoint
        and bar["close"] > prior["open"] * (1.0 - tolerance)
    )

    wick_ratio = float(settings["hammer_wick_to_body"])
    max_body_pct = float(settings["hammer_max_body_pct_range"]) / 100.0
    small_wick_pct = float(settings["hammer_small_wick_max_pct_range"]) / 100.0
    body_zone = float(settings["hammer_body_zone_pct"]) / 100.0
    hammer_common = bar["range"] > 0 and bar["body"] <= bar["range"] * max_body_pct
    long_handle_hammer = bool(
        hammer_common
        and bar["close"] >= bar["open"]
        and bar["lower_wick"] >= wick_ratio * bar["body_floor"]
        and bar["upper_wick"] <= small_wick_pct * bar["range"]
        and min(bar["open"], bar["close"])
        >= bar["low"] + body_zone * bar["range"]
    )
    long_handle_shooting_star = bool(
        hammer_common
        and bar["close"] <= bar["open"]
        and bar["upper_wick"] >= wick_ratio * bar["body_floor"]
        and bar["lower_wick"] <= small_wick_pct * bar["range"]
        and max(bar["open"], bar["close"])
        <= bar["low"] + (1.0 - body_zone) * bar["range"]
    )

    close_zone = float(settings["outside_close_zone_pct"]) / 100.0
    full_outside = bar["high"] > prior["high"] and bar["low"] < prior["low"]
    bullish_full_outside = bool(
        full_outside
        and bar["close"] > bar["open"]
        and bar["close_location"] >= 1.0 - close_zone
    )
    bearish_full_outside = bool(
        full_outside
        and bar["close"] < bar["open"]
        and bar["close_location"] <= close_zone
    )

    return {
        "bullish_piercing": bullish_piercing,
        "bearish_dark_cloud": bearish_dark_cloud,
        "long_handle_hammer": long_handle_hammer,
        "long_handle_shooting_star": long_handle_shooting_star,
        "bullish_full_outside": bullish_full_outside,
        "bearish_full_outside": bearish_full_outside,
    }


def _prior_trend(
    closes: pd.Series, current_index: int, weeks: int, minimum_move_pct: float
) -> tuple[bool, bool, float]:
    start = current_index - weeks - 1
    if start < 0:
        return False, False, np.nan
    history = closes.iloc[start:current_index].astype(float)
    if len(history) != weeks + 1 or history.isna().any() or history.iloc[0] == 0:
        return False, False, np.nan
    changes = history.diff().dropna()
    required_steps = max(2, math.ceil(weeks * 2.0 / 3.0))
    net_move_pct = (history.iloc[-1] / history.iloc[0] - 1.0) * 100.0
    downtrend = bool(
        (changes < 0).sum() >= required_steps and net_move_pct <= -minimum_move_pct
    )
    uptrend = bool(
        (changes > 0).sum() >= required_steps and net_move_pct >= minimum_move_pct
    )
    return downtrend, uptrend, float(net_move_pct)


def _local_extreme(
    weekly: pd.DataFrame, index: int, window: int, direction: str
) -> bool:
    start = max(0, index - window + 1)
    sample = weekly.iloc[start : index + 1]
    if len(sample) < window:
        return False
    if direction == "BULLISH":
        return bool(float(weekly.iloc[index]["low"]) <= float(sample["low"].min()))
    return bool(float(weekly.iloc[index]["high"]) >= float(sample["high"].max()))


def scan_weekly_pivots(weekly: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    settings = config["pattern_settings"]
    minimum_score = int(config.get("minimum_score", 6))
    very_strong_score = int(config.get("very_strong_score", 8))
    trend_weeks = int(settings.get("prior_trend_weeks", 3))
    minimum_move = float(settings.get("minimum_prior_move_pct", 1.0))
    volume_weeks = int(settings.get("volume_average_weeks", 20))
    rows: list[dict[str, Any]] = []

    if len(weekly) < 12:
        return pd.DataFrame()

    closes = weekly["close"]
    real_bodies = (weekly["close"] - weekly["open"]).abs()
    for index in range(max(10, trend_weeks + 1), len(weekly)):
        previous = weekly.iloc[index - 1]
        current = weekly.iloc[index]
        average_body = float(real_bodies.iloc[max(0, index - 10) : index].mean())
        flags = classify_strong_patterns(previous, current, average_body, settings)
        directions: list[str] = []
        if any(
            flags[name]
            for name in ("bullish_piercing", "long_handle_hammer", "bullish_full_outside")
        ):
            directions.append("BULLISH")
        if any(
            flags[name]
            for name in (
                "bearish_dark_cloud",
                "long_handle_shooting_star",
                "bearish_full_outside",
            )
        ):
            directions.append("BEARISH")
        if not directions:
            continue

        downtrend, uptrend, prior_move_pct = _prior_trend(
            closes, index, trend_weeks, minimum_move
        )
        bar = _bar_parts(current)
        volume_history = weekly["volume"].iloc[max(0, index - volume_weeks) : index]
        average_volume = float(volume_history.mean()) if not volume_history.empty else np.nan
        above_average_volume = bool(
            pd.notna(average_volume)
            and average_volume > 0
            and float(current["volume"]) > average_volume
        )

        for direction in directions:
            pattern_names: list[str] = []
            reasons: list[str] = []
            score = 0
            if direction == "BULLISH":
                if flags["bullish_full_outside"]:
                    pattern_names.append("BULLISH FULL OUTSIDE REVERSAL")
                    reasons.append("Full outside reversal +4")
                    score += 4
                if flags["bullish_piercing"]:
                    pattern_names.append("BULLISH PIERCING")
                    reasons.append("Bullish piercing +3")
                    score += 3
                if flags["long_handle_hammer"]:
                    pattern_names.append("LONG-HANDLE HAMMER")
                    reasons.append("Long-handle hammer +3")
                    score += 3
                trend_ok = downtrend
                close_extreme = bar["close_location"] >= 0.75
                pivot_type = "PIVOT_LOW"
                pivot_price = float(current["low"])
            else:
                if flags["bearish_full_outside"]:
                    pattern_names.append("BEARISH FULL OUTSIDE REVERSAL")
                    reasons.append("Full outside reversal +4")
                    score += 4
                if flags["bearish_dark_cloud"]:
                    pattern_names.append("BEARISH DARK-CLOUD REVERSAL")
                    reasons.append("Dark-cloud reversal +3")
                    score += 3
                if flags["long_handle_shooting_star"]:
                    pattern_names.append("LONG-HANDLE SHOOTING STAR")
                    reasons.append("Long-handle shooting star +3")
                    score += 3
                trend_ok = uptrend
                close_extreme = bar["close_location"] <= 0.25
                pivot_type = "PIVOT_HIGH"
                pivot_price = float(current["high"])

            local_5 = _local_extreme(weekly, index, 5, direction)
            local_10 = _local_extreme(weekly, index, 10, direction)
            if local_5:
                score += 2
                reasons.append("5-week local extreme +2")
            if local_10:
                score += 2
                reasons.append("10-week local extreme +2")
            if trend_ok:
                score += 2
                reasons.append(f"Prior {trend_weeks}-week trend +2")
            if close_extreme:
                score += 1
                reasons.append("Close in strongest 25% +1")
            if above_average_volume:
                score += 1
                reasons.append("Above-average volume +1")
            if score < minimum_score:
                continue

            confirmation_date: str | None = None
            confirmation_status = "PENDING"
            confirmed = 0
            if index + 1 < len(weekly):
                following = weekly.iloc[index + 1]
                confirmation_date = pd.Timestamp(following["bar_date"]).date().isoformat()
                if direction == "BULLISH":
                    holds_pivot = float(following["low"]) >= float(current["low"])
                    follows_through = float(following["close"]) > float(current["close"])
                else:
                    holds_pivot = float(following["high"]) <= float(current["high"])
                    follows_through = float(following["close"]) < float(current["close"])
                if config.get("confirmation_requires_close_followthrough", False):
                    holds_pivot = holds_pivot and follows_through
                confirmed = int(holds_pivot)
                confirmation_status = "CONFIRMED" if holds_pivot else "FAILED"

            rows.append(
                {
                    "symbol": str(current["symbol"]).upper(),
                    "bar_date": pd.Timestamp(current["bar_date"]).date().isoformat(),
                    "open": float(current["open"]),
                    "high": float(current["high"]),
                    "low": float(current["low"]),
                    "close": float(current["close"]),
                    "volume": float(current["volume"]),
                    "pivot_type": pivot_type,
                    "direction": direction,
                    "patterns": " + ".join(pattern_names),
                    "pivot_score": int(score),
                    "pivot_strength": "VERY_STRONG" if score >= very_strong_score else "STRONG",
                    "confirmed": confirmed,
                    "confirmation_status": confirmation_status,
                    "confirmation_date": confirmation_date,
                    "pivot_price": pivot_price,
                    "is_5_week_extreme": int(local_5),
                    "is_10_week_extreme": int(local_10),
                    "prior_trend_confirmed": int(trend_ok),
                    "prior_move_pct": prior_move_pct,
                    "close_in_strongest_quartile": int(close_extreme),
                    "above_average_volume": int(above_average_volume),
                    "average_volume_20w": average_volume,
                    "score_detail": "; ".join(reasons),
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
    return pd.DataFrame(rows)


def weekly_with_period_key(weekly: pd.DataFrame) -> pd.DataFrame:
    work = weekly.copy()
    work["bar_date"] = pd.to_datetime(work["bar_date"], errors="coerce")
    work["week_key"] = work["bar_date"].dt.to_period("W-FRI").astype(str)
    return work


def comma_join(values: Iterable[str]) -> str:
    return ", ".join(str(value) for value in values)
