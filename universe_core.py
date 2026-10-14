from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

from pivot_core import DATE_COLUMN_ALIASES, SYMBOL_COLUMN_ALIASES, quoted_identifier


PROJECT_DIR = Path(__file__).resolve().parent
QQQ_CACHE = PROJECT_DIR / "data" / "qqq_holdings.csv"
VALID_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")

DOW_30 = {
    "AAPL": "Apple",
    "AMGN": "Amgen",
    "AMZN": "Amazon",
    "AXP": "American Express",
    "BA": "Boeing",
    "CAT": "Caterpillar",
    "CRM": "Salesforce",
    "CSCO": "Cisco",
    "CVX": "Chevron",
    "DIS": "Walt Disney",
    "GOOGL": "Alphabet",
    "GS": "Goldman Sachs",
    "HD": "Home Depot",
    "HON": "Honeywell",
    "IBM": "IBM",
    "JNJ": "Johnson & Johnson",
    "JPM": "JPMorgan Chase",
    "KO": "Coca-Cola",
    "MCD": "McDonald's",
    "MMM": "3M",
    "MRK": "Merck",
    "MSFT": "Microsoft",
    "NKE": "Nike",
    "NVDA": "Nvidia",
    "PG": "Procter & Gamble",
    "SHW": "Sherwin-Williams",
    "TRV": "Travelers",
    "UNH": "UnitedHealth",
    "V": "Visa",
    "WMT": "Walmart",
}


def _tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, str]:
    table_sql = quoted_identifier(table)
    return {
        str(row[1]).lower(): str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table_sql})").fetchall()
    }


def normalize_ticker(value: object) -> str:
    symbol = str(value).strip().upper().replace(" ", ".")
    return symbol if VALID_TICKER.fullmatch(symbol) else ""


def discover_available_bar_symbols(connection: sqlite3.Connection) -> set[str]:
    symbols: set[str] = set()
    for table in _tables(connection):
        columns = _columns(connection, table)
        if not {"open", "high", "low", "close"}.issubset(columns):
            continue
        if not any(alias in columns for alias in DATE_COLUMN_ALIASES):
            continue
        symbol_column = next(
            (columns[alias] for alias in SYMBOL_COLUMN_ALIASES if alias in columns), None
        )
        if symbol_column:
            table_sql = quoted_identifier(table)
            symbol_sql = quoted_identifier(symbol_column)
            try:
                values = connection.execute(
                    f"SELECT DISTINCT {symbol_sql} FROM {table_sql} WHERE {symbol_sql} IS NOT NULL"
                ).fetchall()
            except sqlite3.DatabaseError:
                continue
            symbols.update(
                symbol for (value,) in values if (symbol := normalize_ticker(value))
            )
        else:
            match = re.match(r"^([A-Za-z0-9.\-]{1,10})_(?:bars?|prices?)", table)
            if match:
                symbol = normalize_ticker(match.group(1))
                if symbol:
                    symbols.add(symbol)
    return symbols


def _load_spy_from_database(connection: sqlite3.Connection) -> pd.DataFrame:
    candidates: list[tuple[int, str, pd.DataFrame]] = []
    for table in _tables(connection):
        lower_name = table.lower()
        is_sector_holdings = lower_name == "spdr_sector_holdings_daily"
        is_spy_membership = "spy" in lower_name and any(
            token in lower_name for token in ("holding", "relationship", "constituent")
        )
        if not (is_sector_holdings or is_spy_membership):
            continue
        columns = _columns(connection, table)
        ticker_column = columns.get("ticker") or columns.get("symbol")
        if not ticker_column:
            continue
        company_column = (
            columns.get("name") or columns.get("company") or columns.get("security")
        )
        table_sql = quoted_identifier(table)
        ticker_sql = quoted_identifier(ticker_column)
        company_expression = (
            quoted_identifier(company_column) if company_column else ticker_sql
        )
        try:
            frame = pd.read_sql_query(
                f"SELECT DISTINCT {ticker_sql} AS ticker, "
                f"{company_expression} AS company FROM {table_sql}",
                connection,
            )
        except Exception:
            continue
        frame["ticker"] = frame["ticker"].map(normalize_ticker)
        frame = frame[
            frame["ticker"].ne("") & ~frame["ticker"].isin(["SPY", "TLT"])
        ].drop_duplicates("ticker")
        if frame.empty:
            continue
        score = len(frame) + (1_000 if is_sector_holdings else 0)
        candidates.append((score, table, frame))
    if not candidates:
        return pd.DataFrame(columns=["ticker", "company", "universe", "source"])
    candidates.sort(key=lambda item: item[0], reverse=True)
    _score, table, best = candidates[0]
    best = best.copy()
    best["company"] = best["company"].fillna(best["ticker"]).astype(str)
    best["universe"] = "SPY"
    best["source"] = table
    return best.sort_values("ticker").reset_index(drop=True)


def load_universe(
    connection: sqlite3.Connection, universe: str
) -> tuple[pd.DataFrame, str]:
    universe = universe.upper()
    if universe == "DOW30":
        frame = pd.DataFrame(
            {
                "ticker": list(DOW_30),
                "company": list(DOW_30.values()),
                "universe": "DOW30",
                "source": "bundled Dow 30 list",
            }
        )
        return frame, "bundled Dow 30 list"
    if universe == "QQQ":
        frame = pd.read_csv(QQQ_CACHE, dtype=str)
        frame["ticker"] = frame["ticker"].map(normalize_ticker)
        frame = frame[frame["ticker"].ne("")].drop_duplicates("ticker")
        frame["universe"] = "QQQ"
        frame["source"] = "bundled QQQ holdings"
        return frame.sort_values("ticker").reset_index(drop=True), "bundled QQQ holdings"
    if universe == "SPY":
        frame = _load_spy_from_database(connection)
        if not frame.empty:
            return frame, str(frame.iloc[0]["source"])
        available = sorted(discover_available_bar_symbols(connection) - {"SPY", "TLT"})
        frame = pd.DataFrame(
            {
                "ticker": available,
                "company": available,
                "universe": "SPY",
                "source": "available database symbols (membership fallback)",
            }
        )
        return frame, "available database symbols (SPY membership table not found)"
    raise ValueError(f"Unsupported universe: {universe}")


def display_label(frame: pd.DataFrame) -> dict[str, str]:
    labels: dict[str, str] = {}
    for row in frame.itertuples(index=False):
        ticker = str(row.ticker)
        company = str(row.company).strip()
        labels[ticker] = ticker if not company or company == ticker else f"{ticker} — {company}"
    return labels
