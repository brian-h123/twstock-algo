from datetime import timedelta

import streamlit as st

from twstock.ui.data_loader import get_available_range
from twstock.ui.names import display_label
from twstock.universe import get_top50_universe

st.title("Taiwan Stock Dashboard")

tickers = get_top50_universe()
labels = [display_label(t) for t in tickers]
default_idx = tickers.index("2330")

selected_label = st.sidebar.selectbox("Ticker", labels, index=default_idx, key="ticker")
ticker = selected_label.split(" — ")[0]

avail_start, avail_end = get_available_range(ticker)

# Clamp any persisted date state to the new ticker's available range BEFORE
# rendering the inputs — Streamlit reads session_state first for keyed widgets.
if "start_date" in st.session_state:
    st.session_state["start_date"] = min(
        max(st.session_state["start_date"], avail_start), avail_end
    )
if "end_date" in st.session_state:
    st.session_state["end_date"] = min(max(st.session_state["end_date"], avail_start), avail_end)

default_start = max(avail_start, avail_end - timedelta(days=730))

start = st.sidebar.date_input(
    "Start",
    value=default_start,
    min_value=avail_start,
    max_value=avail_end,
    key="start_date",
)
end = st.sidebar.date_input(
    "End",
    value=avail_end,
    min_value=avail_start,
    max_value=avail_end,
    key="end_date",
)

if start > end:
    st.sidebar.warning("Start date is after end date — end clamped to start.")
    end = start

st.write(f"Ticker: {ticker}")
st.write(f"Range: {start} → {end}")
st.write(f"Available: {avail_start} → {avail_end}")
