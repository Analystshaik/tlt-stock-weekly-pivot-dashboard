# TLT Weekly Pivot Dashboard

A separate Streamlit application that uses the existing Indexes-vs-TLT SQLite database. It detects only selective, strong weekly TLT reversal patterns, confirms the pivot on the following week, and measures how SPY, QQQ, DIA, IWM and IWN behaved 1, 2, 3, 4, 6 and 8 weeks later.

Version 1.2 adds a separate **Stock Reaction Explorer** page. Select a constituent from SPY, the Dow 30 or QQQ and run the same confirmed-TLT-pivot analysis for that individual stock.

Version 1.3 keeps the active SQLite path in permanent Streamlit session state. Changing pages no longer clears or resets the database selection, and pasted paths are automatically cleaned of surrounding quotes and whitespace.

## Existing database connection

The included `config.json` points to:

```text
C:\Keystone\ibkr_production_data_engine_v2\data\ibkr_market_data.sqlite
```

TLT is normally read from `relationship_bars_day`. For every symbol, version 1.1 also searches the complete SQLite database automatically. It supports:

- shared tables using either `symbol` or `ticker`;
- separate symbol tables such as `spy_bars_day`;
- date fields named `bar_date`, `date`, `datetime` or `timestamp`;
- missing volume fields, which are treated as zero for backtesting.

Each source must contain `open`, `high`, `low` and `close`.

The source table is never changed. The project creates and refreshes only these derived tables:

- `tlt_weekly_bars`
- `tlt_weekly_pivots`
- `tlt_weekly_pivot_backtest`
- `tlt_weekly_pivot_backtest_summary`
- `tlt_weekly_pivot_source_status`

The source-status table records the detected table, history range and row count for every symbol. A missing index no longer stops the scanner or dashboard; only that symbol is skipped.

### Optional explicit table mapping

Automatic discovery should normally be sufficient. If two compatible tables contain the same symbol and you want to force one, add it to `symbol_source_tables` in `config.json`:

```json
"symbol_source_tables": {
  "SPY": "spy_bars_day",
  "QQQ": "etf_bars_day"
}
```

## Approved patterns

No other candlestick patterns can initiate a signal.

1. Bullish piercing / bearish dark-cloud reversal
2. Long-handle hammer / long-handle shooting star
3. Bullish / bearish full outside reversal

The scanner allows multiple approved patterns on one candle and combines their points.

### Score

| Component | Points |
|---|---:|
| Full outside reversal | 4 |
| Piercing or dark-cloud reversal | 3 |
| Long-handle hammer or shooting star | 3 |
| 5-week local extreme | 2 |
| 10-week local extreme | 2 |
| Reversal after a qualifying 3-week move | 2 |
| Close in strongest 25% of the candle | 1 |
| Above-average weekly volume | 1 |

- Strong: 6–7
- Very Strong: 8+
- Below 6: ignored

A bullish candidate is confirmed when the next week does not break its reversal low. A bearish candidate is confirmed when the next week does not break its reversal high. The newest candidate remains `PENDING` until another weekly candle exists.

## First-time setup

1. Keep TWS / the IBKR data engine separate; this dashboard uses bars already stored in SQLite.
2. If your database path differs, edit `database` in `config.json`. You can also enter a temporary path in the dashboard sidebar.
3. Double-click `install_requirements.bat` once.
4. Double-click `run_all.bat`.

`run_all.bat` scans pivots, runs the forward-reaction backtest, and opens the Streamlit dashboard.

For later use, `run_dashboard.bat` opens the dashboard directly. The **Run analysis** button in the sidebar refreshes both the scanner and backtest.

## Separate command-line steps

```bat
py -3.10 validate_setup.py
py -3.10 tlt_weekly_pivot_scanner.py
py -3.10 tlt_weekly_pivot_backtest.py
py -3.10 -m streamlit run tlt_weekly_pivot_dashboard.py
```

Optional database override:

```bat
py -3.10 tlt_weekly_pivot_scanner.py --database "D:\Data\ibkr_market_data.sqlite"
```

## Dashboard pages

- **Weekly chart:** TLT candlesticks, volume, and filtered pivot markers with full scoring details on hover.
- **Pivot history:** Strong and Very Strong candidates, confirmation state, score components, and CSV export.
- **Index reaction explorer:** forward index returns plus normalized TLT/index price paths around any historical pivot.
- **Backtest summary:** average returns and expected-direction hit rates by index and horizon, with sample sizes.
- **Rules & scoring:** the exact detection and confirmation logic.

The dashboard also includes a **Detected market-data sources** panel. It shows which SQLite table supplied each symbol and clearly identifies unavailable symbols.

## Stock Reaction Explorer

Choose **Stock Reaction Explorer** from Streamlit's page navigation. The page provides:

- SPY, Dow 30 and QQQ universe selection;
- a constituent dropdown filtered to symbols with stored bars by default;
- optional manual ticker entry;
- the selected stock versus its parent ETF: SPY, DIA or QQQ;
- per-pivot forward returns and normalized TLT/stock/ETF price paths;
- bullish-versus-bearish TLT pivot backtest summaries;
- average and median returns, dispersion, sample counts and direction-aware hit rates;
- a complete event-history table with CSV download.

QQQ and Dow membership lists are bundled. SPY membership is read from the database, preferring `spdr_sector_holdings_daily` or an existing SPY holdings/relationship table. If no SPY membership table exists, the page falls back to symbols with stored price bars and labels that fallback clearly.

Stock calculations run on demand and do not create hundreds of permanent backtest tables.

## Interpretation

For a bullish TLT pivot, the backtest expectation is index weakness. For a bearish TLT pivot, the expectation is index strength. This is a historical relationship study, not an order-entry system or a guarantee of future returns.

---

## GitHub / Streamlit Community Cloud package

This copy is configured for cloud deployment under GitHub user **Analystshaik**.

- Recommended repository: `tlt-stock-weekly-pivot-dashboard`
- Streamlit entrypoint: `tlt_weekly_pivot_dashboard.py`
- Cloud database: `data/streamlit_market_data.sqlite`
- Large SQLite files: Git LFS (`data/*.sqlite`)

For the complete Windows-to-GitHub-to-Streamlit steps, see `CLOUD_DEPLOYMENT.md`.
