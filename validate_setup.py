from __future__ import annotations

import argparse
from pathlib import Path

from pivot_core import connect_database, load_config, load_symbol_daily_bars


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the TLT pivot dashboard setup.")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--database", default=None, help="Optional SQLite path override")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.database:
        config["database"] = args.database

    database_path = Path(config["database"]).expanduser()
    if not database_path.is_file():
        raise SystemExit(f"FAILED: database not found: {database_path}")

    connection = connect_database(database_path)
    try:
        symbols = [config["reference_symbol"]] + list(config["index_symbols"])
        print(f"Database: {database_path}")
        for symbol in symbols:
            try:
                bars, source = load_symbol_daily_bars(
                    connection,
                    symbol,
                    preferred_table=config.get("source_table"),
                    symbol_source_tables=config.get("symbol_source_tables", {}),
                )
                print(
                    f"  {symbol:>4}: {len(bars):,} daily bars from {source.label} "
                    f"({bars['bar_date'].min().date()} through {bars['bar_date'].max().date()})"
                )
            except RuntimeError as exc:
                print(f"  {symbol:>4}: NOT FOUND — {exc}")
    finally:
        connection.close()
    print("Setup validation passed.")


if __name__ == "__main__":
    main()
