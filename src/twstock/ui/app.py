from datetime import timedelta

import streamlit as st

from twstock.ui.charts import candlestick_volume, compute_bollinger, compute_sma
from twstock.ui.data_loader import get_available_range, load_adjusted, load_raw
from twstock.ui.names import display_label
from twstock.universe import get_top50_universe


def main() -> None:
    st.title("Taiwan Stock Dashboard")

    tickers = get_top50_universe()
    labels = [display_label(t) for t in tickers]
    default_idx = tickers.index("2330")

    selected_label = st.sidebar.selectbox("Ticker", labels, index=default_idx, key="ticker")
    ticker = selected_label.split(" — ")[0]

    avail_start, avail_end = get_available_range(ticker)

    default_start = max(avail_start, avail_end - timedelta(days=730))

    # Initialize session state on first render; clamp on every subsequent rerun.
    # Never pass value= alongside a session_state key — Streamlit treats them as
    # conflicting sources of truth and warns.
    if "start_date" not in st.session_state:
        st.session_state["start_date"] = default_start
    else:
        st.session_state["start_date"] = min(
            max(st.session_state["start_date"], avail_start), avail_end
        )

    if "end_date" not in st.session_state:
        st.session_state["end_date"] = avail_end
    else:
        st.session_state["end_date"] = min(
            max(st.session_state["end_date"], avail_start), avail_end
        )

    start = st.sidebar.date_input(
        "Start",
        min_value=avail_start,
        max_value=avail_end,
        key="start_date",
    )
    end = st.sidebar.date_input(
        "End",
        min_value=avail_start,
        max_value=avail_end,
        key="end_date",
    )

    if start > end:
        st.sidebar.warning("Start date is after end date — end clamped to start.")
        end = start

    price_view = st.sidebar.radio("Price view", ["Adjusted", "Raw"], index=0, key="price_view")

    sma_20 = st.sidebar.checkbox(label="SMA(20)", value=True)
    sma_60 = st.sidebar.checkbox(label="SMA(60)", value=True)
    bbands = st.sidebar.checkbox(label="Bollinger Bands", value=False)

    if price_view == "Adjusted":
        df = load_adjusted(ticker=ticker, start=start, end=end)
    else:
        df = load_raw(ticker=ticker, start=start, end=end)

    if df.empty:
        st.warning("No data available for the selected range.")
        st.stop()

    overlays = {}
    if sma_20:
        overlays["SMA(20)"] = compute_sma(close=df["close"], window=20)
    if sma_60:
        overlays["SMA(60)"] = compute_sma(close=df["close"], window=60)
    if bbands:
        overlays["BB lower"], overlays["BB mid"], overlays["BB upper"] = compute_bollinger(
            close=df["close"]
        )

    fig = candlestick_volume(df=df, overlays=overlays, title=f"{display_label(ticker)}")
    st.plotly_chart(fig, width="stretch")


if __name__ == "__main__":
    main()
