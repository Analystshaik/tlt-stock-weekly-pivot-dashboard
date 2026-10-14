from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from app_state import database_path_input
from pivot_core import (
    load_config,
    load_symbol_daily_bars,
    quoted_identifier,
    resample_daily_to_weekly,
)
from tlt_weekly_pivot_backtest import run_backtest
from tlt_weekly_pivot_scanner import run_scan


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"
BASE_CONFIG = load_config(CONFIG_PATH)

st.set_page_config(
    page_title="TLT Weekly Pivot Dashboard",
    page_icon="↕",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.35rem; padding-bottom: 2rem;}
      [data-testid="stMetric"] {background: #111827; border: 1px solid #263247;
        padding: 0.8rem 1rem; border-radius: 0.75rem;}
      [data-testid="stMetricLabel"] {color: #a7b3c7;}
      .signal-card {padding: 1rem 1.15rem; border-radius: .8rem;
        border: 1px solid #334155; background: linear-gradient(135deg,#111827,#172033);}
      .signal-card h3 {margin: 0 0 .35rem 0;}
      .muted {color: #94a3b8;}
    </style>
    """,
    unsafe_allow_html=True,
)


def _open_readonly(database: str) -> sqlite3.Connection:
    database_path = Path(database).expanduser()
    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")
    return sqlite3.connect(str(database_path), timeout=60)


@st.cache_data(ttl=60, show_spinner=False)
def read_table(database: str, table: str) -> pd.DataFrame:
    table_sql = quoted_identifier(table)
    connection = _open_readonly(database)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return pd.DataFrame()
        return pd.read_sql_query(f"SELECT * FROM {table_sql}", connection)
    finally:
        connection.close()


@st.cache_data(ttl=300, show_spinner=False)
def load_normalized_paths(
    database: str,
    source_table: str,
    symbols: tuple[str, ...],
    pivot_date: str,
    pre_weeks: int = 4,
    post_weeks: int = 8,
) -> pd.DataFrame:
    connection = _open_readonly(database)
    results: list[pd.DataFrame] = []
    pivot_key = str(pd.Timestamp(pivot_date).to_period("W-FRI"))
    try:
        for symbol in symbols:
            try:
                daily, _source = load_symbol_daily_bars(
                    connection,
                    symbol,
                    preferred_table=source_table,
                    symbol_source_tables=BASE_CONFIG.get("symbol_source_tables", {}),
                )
            except RuntimeError:
                continue
            weekly = resample_daily_to_weekly(daily)
            weekly["week_key"] = weekly["bar_date"].dt.to_period("W-FRI").astype(str)
            match = weekly.index[weekly["week_key"] == pivot_key]
            if match.empty:
                continue
            pivot_index = int(match[0])
            start = max(0, pivot_index - pre_weeks)
            stop = min(len(weekly), pivot_index + post_weeks + 1)
            sample = weekly.iloc[start:stop].copy()
            base = float(weekly.iloc[pivot_index]["close"])
            if base == 0:
                continue
            sample["week_offset"] = sample.index - pivot_index
            sample["normalized_close"] = sample["close"] / base * 100.0
            sample["symbol"] = symbol
            results.append(sample[["bar_date", "week_offset", "symbol", "normalized_close"]])
    finally:
        connection.close()
    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def prepare_dates(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column in result.columns:
            result[column] = pd.to_datetime(result[column], errors="coerce")
    return result


def pivot_chart(weekly: pd.DataFrame, pivots: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=[0.78, 0.22],
    )
    figure.add_trace(
        go.Candlestick(
            x=weekly["bar_date"],
            open=weekly["open"],
            high=weekly["high"],
            low=weekly["low"],
            close=weekly["close"],
            name="TLT",
            increasing_line_color="#22c55e",
            decreasing_line_color="#ef4444",
        ),
        row=1,
        col=1,
    )
    volume_colors = np.where(weekly["close"] >= weekly["open"], "#16a34a", "#dc2626")
    figure.add_trace(
        go.Bar(
            x=weekly["bar_date"],
            y=weekly["volume"],
            marker_color=volume_colors,
            opacity=0.45,
            name="Volume",
            hovertemplate="%{x|%Y-%m-%d}<br>Volume %{y:,.0f}<extra></extra>",
        ),
        row=2,
        col=1,
    )

    for direction, color, symbol, label in (
        ("BULLISH", "#34d399", "triangle-up", "Pivot Low"),
        ("BEARISH", "#fb7185", "triangle-down", "Pivot High"),
    ):
        subset = pivots[pivots["direction"] == direction].copy()
        if subset.empty:
            continue
        subset["marker_y"] = np.where(
            direction == "BULLISH", subset["low"] * 0.985, subset["high"] * 1.015
        )
        custom = np.column_stack(
            [
                subset["patterns"],
                subset["pivot_score"],
                subset["pivot_strength"],
                subset["confirmation_status"],
                subset["score_detail"],
            ]
        )
        figure.add_trace(
            go.Scatter(
                x=subset["bar_date"],
                y=subset["marker_y"],
                mode="markers",
                name=label,
                marker={
                    "symbol": symbol,
                    "color": color,
                    "size": np.where(subset["pivot_strength"] == "VERY_STRONG", 17, 13),
                    "line": {"color": "#0f172a", "width": 1.2},
                },
                customdata=custom,
                hovertemplate=(
                    f"<b>{label}</b> — %{{x|%Y-%m-%d}}<br>"
                    "Patterns: %{customdata[0]}<br>Score: %{customdata[1]} "
                    "(%{customdata[2]})<br>Confirmation: %{customdata[3]}<br>"
                    "%{customdata[4]}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )

    figure.update_layout(
        height=720,
        margin={"l": 12, "r": 12, "t": 55, "b": 12},
        template="plotly_dark",
        title="TLT Weekly Candles with Strong Reversal Pivots",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.04, "x": 0},
        xaxis_rangeslider_visible=False,
    )
    figure.update_yaxes(title_text="TLT price", row=1, col=1)
    figure.update_yaxes(title_text="Volume", row=2, col=1)
    return figure


def heatmap(
    summary: pd.DataFrame, value_column: str, title: str, suffix: str = "%"
) -> go.Figure:
    matrix = summary.pivot(
        index="index_symbol", columns="horizon_weeks", values=value_column
    ).sort_index()
    text = np.vectorize(
        lambda value: "" if pd.isna(value) else f"{value:.1f}{suffix}"
    )(matrix.to_numpy())
    figure = go.Figure(
        go.Heatmap(
            z=matrix.to_numpy(),
            x=[f"{int(value)}w" for value in matrix.columns],
            y=matrix.index.tolist(),
            text=text,
            texttemplate="%{text}",
            colorscale="RdYlGn",
            zmid=0 if "return" in value_column else 50,
            colorbar={"title": suffix},
            hovertemplate="%{y} · %{x}<br>%{z:.2f}" + suffix + "<extra></extra>",
        )
    )
    figure.update_layout(
        title=title,
        height=360,
        template="plotly_dark",
        margin={"l": 15, "r": 15, "t": 55, "b": 15},
    )
    return figure


st.title("TLT Weekly Pivot Dashboard")
st.caption(
    "Selective weekly reversals for TLT, with confirmation and forward reactions for "
    "SPY, QQQ, DIA, IWM and IWN."
)

with st.sidebar:
    st.header("Data & controls")
    database = database_path_input(
        str(BASE_CONFIG["database"]), "main_database_path_widget"
    )
    st.caption(f"Source table: `{BASE_CONFIG['source_table']}`")
    run_column, refresh_column = st.columns(2)
    if run_column.button("Run analysis", type="primary", use_container_width=True):
        try:
            with st.spinner("Scanning weekly pivots and calculating index reactions..."):
                scan_result = run_scan(CONFIG_PATH, database)
                backtest_result = run_backtest(CONFIG_PATH, database)
            st.cache_data.clear()
            st.success(
                f"{scan_result['pivot_rows']} candidates; "
                f"{backtest_result['pivots_tested']} pivots backtested across "
                f"{len(backtest_result['symbols'])} indexes."
            )
            if backtest_result["missing_symbols"]:
                st.warning(
                    "No stored bars found for: "
                    + ", ".join(backtest_result["missing_symbols"])
                )
        except Exception as exc:
            st.error(str(exc))
    if refresh_column.button("Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    chart_years = st.slider("Chart history (years)", 1, 15, 5)
    strengths = st.multiselect(
        "Pivot strength", ["VERY_STRONG", "STRONG"], default=["VERY_STRONG", "STRONG"]
    )
    directions = st.multiselect(
        "Direction", ["BULLISH", "BEARISH"], default=["BULLISH", "BEARISH"]
    )
    confirmation_filter = st.selectbox(
        "Confirmation",
        ["Confirmed only", "Confirmed + pending", "All candidates"],
        index=0,
    )
    st.divider()
    st.caption("The source daily-bar table is read-only. This app writes only its own derived tables.")

st.info(
    "Use **Stock Reaction Explorer** in the Streamlit page navigation to analyze an individual "
    "SPY, Dow 30 or QQQ constituent after the same TLT weekly pivots."
)

try:
    weekly = prepare_dates(
        read_table(database, BASE_CONFIG["weekly_bars_table"]), ["bar_date"]
    )
    pivots = prepare_dates(
        read_table(database, BASE_CONFIG["pivots_table"]),
        ["bar_date", "confirmation_date"],
    )
    detail = prepare_dates(
        read_table(database, BASE_CONFIG["backtest_table"]),
        ["pivot_date", "entry_date", "exit_date"],
    )
    summary = read_table(database, BASE_CONFIG["backtest_summary_table"])
    source_status = prepare_dates(
        read_table(database, BASE_CONFIG["source_status_table"]),
        ["first_date", "last_date", "updated_at"],
    )
except Exception as exc:
    st.error(str(exc))
    st.info("Correct the database path in the sidebar, then click **Run analysis**.")
    st.stop()

if weekly.empty:
    st.info(
        "No derived weekly data is available yet. Confirm the database path and click "
        "**Run analysis** in the sidebar."
    )
    st.stop()
if pivots.empty:
    st.warning(
        "The scan completed but no Strong or Very Strong pivot candidates met the configured rules. "
        "The weekly TLT chart is still available."
    )
    for column in (
        "bar_date",
        "confirmation_date",
        "pivot_strength",
        "direction",
        "confirmation_status",
        "pivot_score",
        "pivot_type",
        "patterns",
        "pivot_price",
        "prior_move_pct",
        "above_average_volume",
        "score_detail",
        "low",
        "high",
    ):
        if column not in pivots.columns:
            pivots[column] = pd.Series(dtype="object")
    pivots["bar_date"] = pd.to_datetime(pivots["bar_date"])
    pivots["confirmation_date"] = pd.to_datetime(pivots["confirmation_date"])

filtered_pivots = pivots.copy()
if strengths:
    filtered_pivots = filtered_pivots[
        filtered_pivots["pivot_strength"].isin(strengths)
    ]
else:
    filtered_pivots = filtered_pivots.iloc[0:0]
if directions:
    filtered_pivots = filtered_pivots[filtered_pivots["direction"].isin(directions)]
else:
    filtered_pivots = filtered_pivots.iloc[0:0]
if confirmation_filter == "Confirmed only":
    filtered_pivots = filtered_pivots[filtered_pivots["confirmation_status"] == "CONFIRMED"]
elif confirmation_filter == "Confirmed + pending":
    filtered_pivots = filtered_pivots[
        filtered_pivots["confirmation_status"].isin(["CONFIRMED", "PENDING"])
    ]

latest_week = weekly["bar_date"].max()
chart_start = latest_week - pd.DateOffset(years=chart_years)
chart_weekly = weekly[weekly["bar_date"] >= chart_start].copy()
chart_pivots = filtered_pivots[filtered_pivots["bar_date"] >= chart_start].copy()
latest_signal = (
    filtered_pivots.sort_values("bar_date", ascending=False).iloc[0]
    if not filtered_pivots.empty
    else None
)

metric_columns = st.columns(5)
metric_columns[0].metric("Latest TLT week", latest_week.strftime("%b %d, %Y"))
metric_columns[1].metric(
    "Confirmed pivots", int((pivots["confirmation_status"] == "CONFIRMED").sum())
)
metric_columns[2].metric(
    "Very strong", int((pivots["pivot_strength"] == "VERY_STRONG").sum())
)
metric_columns[3].metric(
    "Latest direction", latest_signal["direction"] if latest_signal is not None else "None"
)
metric_columns[4].metric(
    "Latest score", int(latest_signal["pivot_score"]) if latest_signal is not None else "—"
)

if not source_status.empty:
    unavailable = source_status[source_status["available"] == 0]
    if not unavailable.empty:
        st.warning(
            "Index reaction results exclude symbols with no stored OHLC data: "
            + ", ".join(unavailable["symbol"].astype(str))
            + ". Add those bars to any compatible SQLite table or map their table names in config.json."
        )
    with st.expander("Detected market-data sources"):
        status_view = source_status[
            [
                "symbol",
                "available",
                "source_table",
                "daily_rows",
                "first_date",
                "last_date",
                "message",
            ]
        ].copy()
        status_view["available"] = status_view["available"].map({1: "Yes", 0: "No"})
        for column in ("first_date", "last_date"):
            status_view[column] = status_view[column].dt.date
        st.dataframe(status_view, use_container_width=True, hide_index=True)

if latest_signal is not None:
    direction_color = "#34d399" if latest_signal["direction"] == "BULLISH" else "#fb7185"
    st.markdown(
        f"""
        <div class="signal-card">
          <h3 style="color:{direction_color}">Latest filtered signal: {latest_signal['pivot_type']}</h3>
          <div><b>{latest_signal['bar_date'].strftime('%B %d, %Y')}</b> ·
          {latest_signal['pivot_strength']} · score {int(latest_signal['pivot_score'])} ·
          {latest_signal['confirmation_status']}</div>
          <div class="muted">{latest_signal['patterns']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

tabs = st.tabs(
    [
        "Weekly chart",
        "Pivot history",
        "Index reaction explorer",
        "Backtest summary",
        "Rules & scoring",
    ]
)

with tabs[0]:
    if chart_pivots.empty:
        st.warning("No pivot markers match the current sidebar filters.")
    st.plotly_chart(
        pivot_chart(chart_weekly, chart_pivots),
        use_container_width=True,
        config={"displaylogo": False},
    )
    st.caption(
        "Green triangles mark bullish TLT pivot lows; red triangles mark bearish TLT pivot highs. "
        "Larger markers are Very Strong signals. Hover a marker for the complete score explanation."
    )

with tabs[1]:
    st.subheader("Strong and Very Strong TLT pivots")
    history_columns = [
        "bar_date",
        "pivot_type",
        "direction",
        "patterns",
        "pivot_score",
        "pivot_strength",
        "pivot_price",
        "confirmation_status",
        "confirmation_date",
        "prior_move_pct",
        "above_average_volume",
        "score_detail",
    ]
    history = filtered_pivots.sort_values("bar_date", ascending=False)[history_columns].copy()
    history["bar_date"] = history["bar_date"].dt.date
    history["confirmation_date"] = history["confirmation_date"].dt.date
    history["prior_move_pct"] = history["prior_move_pct"].round(2)
    history["pivot_price"] = history["pivot_price"].round(2)
    if history.empty:
        st.info("No pivots match the current filters.")
    else:
        st.dataframe(history, use_container_width=True, hide_index=True)
        st.download_button(
            "Download filtered pivot history",
            history.to_csv(index=False).encode("utf-8"),
            file_name="tlt_weekly_pivots_filtered.csv",
            mime="text/csv",
        )

with tabs[2]:
    st.subheader("What the indexes did after each TLT pivot")
    if detail.empty:
        st.info("Run the analysis to create forward index-reaction results.")
    else:
        pivot_dates = sorted(detail["pivot_date"].dropna().dt.date.unique(), reverse=True)
        selected_date = st.selectbox(
            "TLT pivot week", pivot_dates, format_func=lambda value: value.strftime("%b %d, %Y")
        )
        event = detail[detail["pivot_date"].dt.date == selected_date].copy()
        event["horizon_label"] = event["horizon_weeks"].astype(int).astype(str) + "w"
        event_title = event.iloc[0]
        st.caption(
            f"{event_title['pivot_direction']} TLT pivot · {event_title['pivot_strength']} · "
            f"{event_title['patterns']} · Expected indexes: {event_title['expected_index_direction']}"
        )
        reaction = px.bar(
            event,
            x="horizon_label",
            y="index_return_pct",
            color="index_symbol",
            barmode="group",
            category_orders={
                "horizon_label": [
                    f"{int(value)}w" for value in BASE_CONFIG["backtest_horizons_weeks"]
                ]
            },
            labels={
                "horizon_label": "Weeks after pivot",
                "index_return_pct": "Index return (%)",
                "index_symbol": "Index",
            },
            title="Forward index returns from the TLT pivot week close",
            template="plotly_dark",
        )
        reaction.add_hline(y=0, line_color="#94a3b8", line_width=1)
        reaction.update_layout(height=470, legend_orientation="h")
        st.plotly_chart(reaction, use_container_width=True, config={"displaylogo": False})

        try:
            paths = load_normalized_paths(
                database,
                BASE_CONFIG["source_table"],
                tuple([BASE_CONFIG["reference_symbol"]] + BASE_CONFIG["index_symbols"]),
                selected_date.isoformat(),
            )
        except Exception as exc:
            paths = pd.DataFrame()
            st.warning(f"Normalized path view unavailable: {exc}")
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
                title="Price paths around the selected pivot",
                template="plotly_dark",
            )
            path_figure.add_vline(x=0, line_dash="dash", line_color="#facc15")
            path_figure.add_hline(y=100, line_color="#64748b", line_width=1)
            path_figure.update_layout(height=480, legend_orientation="h")
            st.plotly_chart(path_figure, use_container_width=True, config={"displaylogo": False})

        event_table = event[
            [
                "index_symbol",
                "horizon_weeks",
                "exit_date",
                "index_return_pct",
                "tlt_return_pct",
                "index_minus_tlt_pct",
                "expected_index_direction",
                "expectation_hit",
            ]
        ].sort_values(["horizon_weeks", "index_symbol"])
        for column in ("index_return_pct", "tlt_return_pct", "index_minus_tlt_pct"):
            event_table[column] = event_table[column].round(2)
        event_table["exit_date"] = event_table["exit_date"].dt.date
        st.dataframe(event_table, use_container_width=True, hide_index=True)

with tabs[3]:
    st.subheader("Historical forward-reaction summary")
    if summary.empty:
        st.info("Run the analysis to create the backtest summary.")
    else:
        summary_direction = st.radio(
            "TLT pivot direction", ["BULLISH", "BEARISH"], horizontal=True
        )
        selected_summary = summary[
            summary["pivot_direction"] == summary_direction
        ].copy()
        expected = "index weakness" if summary_direction == "BULLISH" else "index strength"
        st.caption(
            f"For {summary_direction.lower()} TLT pivots, a hit means {expected} occurred by the selected horizon."
        )
        left, right = st.columns(2)
        with left:
            st.plotly_chart(
                heatmap(
                    selected_summary,
                    "average_return_pct",
                    "Average index return after TLT pivot",
                ),
                use_container_width=True,
                config={"displaylogo": False},
            )
        with right:
            st.plotly_chart(
                heatmap(
                    selected_summary,
                    "expectation_hit_rate_pct",
                    "Expected-direction hit rate",
                ),
                use_container_width=True,
                config={"displaylogo": False},
            )
        summary_view = selected_summary[
            [
                "index_symbol",
                "horizon_weeks",
                "pivot_count",
                "average_return_pct",
                "median_return_pct",
                "return_std_pct",
                "average_tlt_return_pct",
                "average_expectation_adjusted_return_pct",
                "expectation_hit_rate_pct",
                "last_pivot_date",
            ]
        ].sort_values(["horizon_weeks", "index_symbol"])
        numeric_columns = summary_view.select_dtypes(include="number").columns
        summary_view[numeric_columns] = summary_view[numeric_columns].round(2)
        st.dataframe(summary_view, use_container_width=True, hide_index=True)
        st.caption(
            "The sample count is shown for every cell. Use larger samples and consistency across "
            "several horizons before treating a relationship as meaningful."
        )

with tabs[4]:
    st.subheader("Approved reversal patterns")
    st.markdown(
        """
        | Pattern | Bullish rule | Bearish rule | Base score |
        |---|---|---|---:|
        | Piercing / dark cloud | Prior bearish body; bullish candle opens near/below the prior close and closes through its midpoint | Exact inverse | +3 |
        | Long-handle hammer / shooting star | Rejection wick is at least 2.5× the body; body sits in the top of the range | Exact inverse | +3 |
        | Full outside reversal | High exceeds the prior high, low breaks the prior low, and close is in the top 25% | Close is in the bottom 25% | +4 |
        """
    )
    st.subheader("Pivot confirmation and scoring")
    st.markdown(
        """
        - 5-week local extreme: **+2**
        - 10-week local extreme: **+2**
        - Reversal after a qualifying 3-week move: **+2**
        - Close in the strongest 25% of the candle: **+1**
        - Weekly volume above its prior 20-week average: **+1**
        - **Strong:** 6–7 points; **Very Strong:** 8 or more points
        - A candidate is **confirmed** when the following week does not break its reversal low (bullish) or high (bearish).
        """
    )
    st.info(
        "Only the three approved reversal families can initiate a signal. The scoring features "
        "strengthen or filter those candles; they cannot create a pivot by themselves."
    )
