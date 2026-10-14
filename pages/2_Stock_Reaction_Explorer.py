from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app_state import database_path_input
from pivot_core import load_config
from stock_reaction import build_stock_analysis, normalized_event_paths
from universe_core import (
    discover_available_bar_symbols,
    display_label,
    load_universe,
    normalize_ticker,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_DIR / "config.json"
CONFIG = load_config(CONFIG_PATH)

st.set_page_config(
    page_title="Stock Reaction Explorer",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.35rem; padding-bottom: 2rem;}
      [data-testid="stMetric"] {background:#111827; border:1px solid #263247;
        padding:.8rem 1rem; border-radius:.75rem;}
      .stock-card {padding:1rem 1.15rem; border-radius:.8rem; border:1px solid #334155;
        background:linear-gradient(135deg,#111827,#172033);}
      .stock-card h3 {margin:0 0 .3rem 0; color:#60a5fa;}
      .muted {color:#94a3b8;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=300, show_spinner=False)
def universe_data(database: str, universe: str) -> tuple[pd.DataFrame, str, list[str]]:
    database_path = Path(database).expanduser()
    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")
    connection = sqlite3.connect(str(database_path), timeout=60)
    try:
        holdings, source = load_universe(connection, universe)
        available = sorted(discover_available_bar_symbols(connection))
    finally:
        connection.close()
    return holdings, source, available


@st.cache_data(ttl=300, show_spinner=False)
def analyze(database: str, symbol: str, benchmark: str) -> dict:
    return build_stock_analysis(database, symbol, benchmark, CONFIG_PATH)


def horizon_metric(summary: pd.DataFrame, symbol: str, horizon: int) -> tuple[str, str]:
    row = summary[
        (summary["index_symbol"] == symbol) & (summary["horizon_weeks"] == horizon)
    ]
    if row.empty:
        return "—", "No completed samples"
    item = row.iloc[0]
    return (
        f"{float(item['average_return_pct']):+.2f}%",
        f"Hit {float(item['expectation_hit_rate_pct']):.1f}% · n={int(item['pivot_count'])}",
    )


def summary_charts(summary: pd.DataFrame, title_symbol: str) -> tuple[go.Figure, go.Figure]:
    work = summary.sort_values(["index_symbol", "horizon_weeks"]).copy()
    work["display_symbol"] = work["index_symbol"].str.replace(" ", ".", regex=False)
    returns = px.line(
        work,
        x="horizon_weeks",
        y="average_return_pct",
        color="display_symbol",
        markers=True,
        labels={
            "horizon_weeks": "Weeks after TLT pivot",
            "average_return_pct": "Average return (%)",
            "display_symbol": "Symbol",
        },
        title=f"Average forward returns — {title_symbol} vs parent ETF",
        template="plotly_dark",
    )
    returns.add_hline(y=0, line_color="#94a3b8", line_width=1)
    returns.update_xaxes(dtick=1)
    returns.update_layout(height=430, legend_orientation="h")

    hits = px.bar(
        work,
        x="horizon_weeks",
        y="expectation_hit_rate_pct",
        color="display_symbol",
        barmode="group",
        labels={
            "horizon_weeks": "Weeks after TLT pivot",
            "expectation_hit_rate_pct": "Expected-direction hit rate (%)",
            "display_symbol": "Symbol",
        },
        title="Direction-aware hit rate",
        template="plotly_dark",
    )
    hits.add_hline(y=50, line_dash="dash", line_color="#facc15", line_width=1)
    hits.update_xaxes(dtick=1)
    hits.update_yaxes(range=[0, 100])
    hits.update_layout(height=430, legend_orientation="h")
    return returns, hits


st.title("Stock Reaction Explorer")
st.caption(
    "Select an SPY, Dow 30 or QQQ constituent and measure how it behaved after each "
    "confirmed TLT weekly pivot."
)

with st.sidebar:
    st.header("Stock selection")
    database = database_path_input(
        str(CONFIG["database"]), "stock_database_path_widget"
    )
    universe_label = st.selectbox("Universe", ["SPY", "Dow 30", "QQQ"], index=0)
    universe_key = {"SPY": "SPY", "Dow 30": "DOW30", "QQQ": "QQQ"}[universe_label]
    benchmark = {"SPY": "SPY", "DOW30": "DIA", "QQQ": "QQQ"}[universe_key]
    available_only = st.checkbox("Show only symbols with stored bars", value=True)

try:
    holdings, universe_source, available_symbols = universe_data(database, universe_key)
except Exception as exc:
    st.error(str(exc))
    st.code(database or "<empty path>", language=None)
    st.info(
        "Enter the same SQLite path used on the main TLT Weekly Pivot Dashboard. "
        "Version 1.3 keeps that selection synchronized between pages."
    )
    st.stop()

labels = display_label(holdings)
available_set = set(available_symbols)
options = holdings["ticker"].dropna().astype(str).tolist()
if available_only:
    options = [ticker for ticker in options if normalize_ticker(ticker) in available_set]

with st.sidebar:
    if options:
        default_index = options.index("AAPL") if "AAPL" in options else 0
        selected = st.selectbox(
            "Stock symbol",
            options,
            index=default_index,
            format_func=lambda value: labels.get(value, value),
        )
    else:
        selected = ""
        st.warning("No stored stock bars matched this universe.")
    custom = st.text_input(
        "Or enter a symbol",
        value="",
        placeholder="Example: AAPL",
        help="A manually entered symbol overrides the dropdown.",
    ).strip().upper()
    symbol = custom or selected
    st.caption(f"Parent benchmark: {benchmark}")
    st.caption(f"Universe source: {universe_source}")
    if st.button("Refresh stock analysis", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if not symbol:
    st.info("Select or enter a stock symbol to begin.")
    st.stop()

try:
    with st.spinner(f"Analyzing {symbol} after confirmed TLT pivots..."):
        result = analyze(database, symbol, benchmark)
except Exception as exc:
    st.error(str(exc))
    st.info(
        "The stock must have daily OHLC rows somewhere in this SQLite database. "
        "The app searches shared symbol/ticker tables and separate symbol tables automatically."
    )
    st.stop()

detail = result["detail"].copy()
summary = result["summary"].copy()
internal_symbol = result["symbol"]
display_symbol = internal_symbol.replace(" ", ".")
for column in ("pivot_date", "entry_date", "exit_date"):
    if column in detail.columns:
        detail[column] = pd.to_datetime(detail[column], errors="coerce")
if not summary.empty and "last_pivot_date" in summary.columns:
    summary["last_pivot_date"] = pd.to_datetime(summary["last_pivot_date"], errors="coerce")

stock_weekly = result["weekly_frames"][internal_symbol]
stock_source = result["sources"].get(internal_symbol, "Unknown")
stock_history_start = pd.Timestamp(stock_weekly["bar_date"].min()).date()
stock_history_end = pd.Timestamp(stock_weekly["bar_date"].max()).date()
event_count = (
    int(detail.loc[detail["index_symbol"] == internal_symbol, "pivot_date"].nunique())
    if not detail.empty
    else 0
)

metric_columns = st.columns(5)
metric_columns[0].metric("Selected stock", display_symbol)
metric_columns[1].metric("Parent ETF", benchmark)
metric_columns[2].metric("Completed pivot events", event_count)
metric_columns[3].metric("Weekly bars", f"{len(stock_weekly):,}")
metric_columns[4].metric("Latest stock week", stock_history_end.strftime("%b %d, %Y"))

st.markdown(
    f"""
    <div class="stock-card">
      <h3>{display_symbol} after TLT weekly pivots</h3>
      <div>Stored history: <b>{stock_history_start}</b> through <b>{stock_history_end}</b></div>
      <div class="muted">Price source: {stock_source} · Universe: {universe_label} · Benchmark: {benchmark}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

if result["missing_optional"]:
    st.warning(
        "The parent ETF comparison is unavailable because no bars were found for: "
        + ", ".join(result["missing_optional"])
        + ". The selected stock analysis is still shown."
    )

if detail.empty:
    st.warning(
        f"{display_symbol} has stored bars, but none overlap the confirmed TLT pivots with a "
        "completed forward horizon."
    )
    st.stop()

tabs = st.tabs(
    ["Reaction explorer", "Backtest summary", "Event history", "Data & methodology"]
)

with tabs[0]:
    st.subheader(f"{display_symbol} reaction after a selected TLT pivot")
    stock_detail = detail[detail["index_symbol"] == internal_symbol]
    pivot_dates = sorted(stock_detail["pivot_date"].dropna().dt.date.unique(), reverse=True)
    selected_pivot_date = st.selectbox(
        "Confirmed TLT pivot week",
        pivot_dates,
        format_func=lambda value: value.strftime("%b %d, %Y"),
    )
    event = detail[detail["pivot_date"].dt.date == selected_pivot_date].copy()
    event["display_symbol"] = event["index_symbol"].str.replace(" ", ".", regex=False)
    event["horizon_label"] = event["horizon_weeks"].astype(int).astype(str) + "w"
    metadata = event.iloc[0]
    expected_text = "stock weakness" if metadata["pivot_direction"] == "BULLISH" else "stock strength"
    st.caption(
        f"{metadata['pivot_direction']} TLT pivot · {metadata['pivot_strength']} · "
        f"{metadata['patterns']} · Expected response: {expected_text}"
    )

    reaction = px.bar(
        event,
        x="horizon_label",
        y="index_return_pct",
        color="display_symbol",
        barmode="group",
        category_orders={
            "horizon_label": [f"{int(value)}w" for value in CONFIG["backtest_horizons_weeks"]]
        },
        labels={
            "horizon_label": "Weeks after TLT pivot",
            "index_return_pct": "Return from pivot-week close (%)",
            "display_symbol": "Symbol",
        },
        title=f"{display_symbol} and {benchmark} forward returns",
        template="plotly_dark",
    )
    reaction.add_hline(y=0, line_color="#94a3b8", line_width=1)
    reaction.update_layout(height=470, legend_orientation="h")
    st.plotly_chart(reaction, use_container_width=True, config={"displaylogo": False})

    paths = normalized_event_paths(
        result["weekly_frames"], selected_pivot_date.isoformat(), pre_weeks=4, post_weeks=8
    )
    if not paths.empty:
        path_figure = px.line(
            paths,
            x="week_offset",
            y="normalized_close",
            color="symbol",
            markers=True,
            labels={
                "week_offset": "Weeks from TLT pivot",
                "normalized_close": "Normalized close (pivot week = 100)",
                "symbol": "Symbol",
            },
            title="TLT, selected stock and parent ETF around the pivot",
            template="plotly_dark",
        )
        path_figure.add_vline(x=0, line_dash="dash", line_color="#facc15")
        path_figure.add_hline(y=100, line_color="#64748b", line_width=1)
        path_figure.update_layout(height=480, legend_orientation="h")
        st.plotly_chart(path_figure, use_container_width=True, config={"displaylogo": False})

    event_table = event[
        [
            "display_symbol",
            "horizon_weeks",
            "exit_date",
            "index_return_pct",
            "tlt_return_pct",
            "index_minus_tlt_pct",
            "expected_index_direction",
            "expectation_hit",
        ]
    ].sort_values(["horizon_weeks", "display_symbol"])
    for column in ("index_return_pct", "tlt_return_pct", "index_minus_tlt_pct"):
        event_table[column] = event_table[column].round(2)
    event_table["exit_date"] = event_table["exit_date"].dt.date
    st.dataframe(event_table, use_container_width=True, hide_index=True)

with tabs[1]:
    st.subheader(f"Historical backtest summary for {display_symbol}")
    direction = st.radio("TLT pivot direction", ["BULLISH", "BEARISH"], horizontal=True)
    selected_summary = summary[summary["pivot_direction"] == direction].copy()
    expected = "decline" if direction == "BULLISH" else "advance"
    st.caption(
        f"For {direction.lower()} TLT pivots, a hit means {display_symbol} produced the expected "
        f"{expected} by that horizon."
    )
    stock_summary = selected_summary[selected_summary["index_symbol"] == internal_symbol]
    metric_row = st.columns(3)
    for column, horizon in zip(metric_row, (1, 4, 8)):
        value, note = horizon_metric(stock_summary, internal_symbol, horizon)
        column.metric(f"Average {horizon}-week return", value, note)

    if selected_summary.empty:
        st.info("No completed events are available for this pivot direction.")
    else:
        returns_chart, hits_chart = summary_charts(selected_summary, display_symbol)
        left, right = st.columns(2)
        with left:
            st.plotly_chart(returns_chart, use_container_width=True, config={"displaylogo": False})
        with right:
            st.plotly_chart(hits_chart, use_container_width=True, config={"displaylogo": False})

        distribution = detail[
            (detail["index_symbol"] == internal_symbol)
            & (detail["pivot_direction"] == direction)
        ].copy()
        if not distribution.empty:
            distribution["horizon_label"] = (
                distribution["horizon_weeks"].astype(int).astype(str) + "w"
            )
            box = px.box(
                distribution,
                x="horizon_label",
                y="index_return_pct",
                points="all",
                category_orders={
                    "horizon_label": [
                        f"{int(value)}w" for value in CONFIG["backtest_horizons_weeks"]
                    ]
                },
                labels={
                    "horizon_label": "Weeks after TLT pivot",
                    "index_return_pct": f"{display_symbol} return (%)",
                },
                title=f"Distribution of {display_symbol} returns after {direction.lower()} TLT pivots",
                template="plotly_dark",
            )
            box.add_hline(y=0, line_color="#94a3b8", line_width=1)
            box.update_layout(height=470)
            st.plotly_chart(box, use_container_width=True, config={"displaylogo": False})

        summary_view = selected_summary[
            [
                "index_symbol",
                "horizon_weeks",
                "pivot_count",
                "average_return_pct",
                "median_return_pct",
                "return_std_pct",
                "average_expectation_adjusted_return_pct",
                "expectation_hit_rate_pct",
                "last_pivot_date",
            ]
        ].copy()
        summary_view["index_symbol"] = summary_view["index_symbol"].str.replace(
            " ", ".", regex=False
        )
        numeric = summary_view.select_dtypes(include="number").columns
        summary_view[numeric] = summary_view[numeric].round(2)
        summary_view["last_pivot_date"] = summary_view["last_pivot_date"].dt.date
        st.dataframe(summary_view, use_container_width=True, hide_index=True)

with tabs[2]:
    st.subheader(f"Every completed {display_symbol} forward-return observation")
    history = detail[detail["index_symbol"] == internal_symbol].copy()
    history["pivot_date"] = history["pivot_date"].dt.date
    history["exit_date"] = history["exit_date"].dt.date
    history = history[
        [
            "pivot_date",
            "pivot_direction",
            "pivot_strength",
            "pivot_score",
            "patterns",
            "horizon_weeks",
            "exit_date",
            "index_return_pct",
            "tlt_return_pct",
            "expected_index_direction",
            "expectation_hit",
        ]
    ].sort_values(["pivot_date", "horizon_weeks"], ascending=[False, True])
    history[["index_return_pct", "tlt_return_pct"]] = history[
        ["index_return_pct", "tlt_return_pct"]
    ].round(2)
    st.dataframe(history, use_container_width=True, hide_index=True)
    st.download_button(
        f"Download {display_symbol} event history",
        history.to_csv(index=False).encode("utf-8"),
        file_name=f"{display_symbol.replace('.', '_')}_tlt_pivot_reactions.csv",
        mime="text/csv",
    )

with tabs[3]:
    st.subheader("Detected price sources")
    source_rows = [
        {
            "symbol": symbol_key.replace(" ", "."),
            "source": source,
            "daily_rows": result["daily_rows"].get(symbol_key, 0),
        }
        for symbol_key, source in result["sources"].items()
    ]
    st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)
    st.subheader("Methodology")
    st.markdown(
        """
        - Only **confirmed** Strong and Very Strong TLT weekly pivots are used.
        - Stock and benchmark returns are measured from the TLT pivot-week close to 1, 2, 3, 4, 6 and 8 weeks later.
        - A bullish TLT pivot expects stock weakness; a bearish TLT pivot expects stock strength.
        - The hit rate is direction-aware. It does not mean the stock had a positive return in every case.
        - Calculations run on demand and do not modify the source price tables.
        """
    )
    st.info(
        "Membership lists and stored price history can cover different dates. The page reports only "
        "confirmed TLT pivots that overlap the selected stock's available data."
    )
