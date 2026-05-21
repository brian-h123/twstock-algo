import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def compute_sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window).mean()


def compute_bollinger(
    close: pd.Series, window: int = 20, k: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (lower_band, middle_band, upper_band)."""
    rolling_std = close.rolling(window).std()
    middle = compute_sma(close, window)
    lower = middle - k * rolling_std
    upper = middle + k * rolling_std
    return (lower, middle, upper)


def candlestick_volume(
    df: pd.DataFrame,
    overlays: dict[str, pd.Series] | None = None,
    ex_date_markers: pd.DataFrame | None = None,
    title: str = "",
) -> go.Figure:
    """Single Plotly figure with 2 stacked subplots (price 70%, volume 30%),
    shared x-axis. Candlestick uses Taiwan-convention colors (red up,
    green down). Overlays are line traces on the price subplot. Ex-date
    markers are vertical lines with hover annotations."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.03
    )

    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            increasing_line_color="red",
            decreasing_line_color="green",
            name="price",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(go.Bar(x=df["date"], y=df["volume"], name="volume"), row=2, col=1)

    if overlays:
        for name, series in overlays.items():
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=series,
                    mode="lines",
                    name=name,
                ),
                row=1,
                col=1,
            )

    if ex_date_markers is not None and not ex_date_markers.empty:
        for _, row in ex_date_markers.iterrows():
            ex_date_str = str(row["date"])[:10]
            fig.add_vline(x=ex_date_str, line_dash="dash", line_color="gray")

            close_row = df.loc[df["date"] == row["date"], "close"]
            y_val = close_row.iloc[0] if not close_row.empty else df["close"].mean()

            parts = [f"{ex_date_str} · {row['action_type']}"]
            if pd.notna(row.get("cash_dividend")) and row["cash_dividend"] != 0:
                parts.append(f"cash={row['cash_dividend']}")
            if pd.notna(row.get("stock_ratio")) and row["stock_ratio"] != 0:
                parts.append(f"stock_ratio={row['stock_ratio']}")
            hover = ", ".join(parts) + "<extra></extra>"

            fig.add_trace(
                go.Scatter(
                    x=[row["date"]],
                    y=[y_val],
                    mode="markers",
                    marker=dict(size=8, opacity=0),
                    hovertemplate=hover,
                    showlegend=False,
                ),
                row=1,
                col=1,
            )

    full_range = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    missing_dates = full_range.difference(pd.DatetimeIndex(df["date"]))
    fig.update_xaxes(
        rangebreaks=[dict(values=missing_dates.strftime("%Y-%m-%d").tolist())],
        rangeslider_visible=False,
    )
    fig.update_layout(title=title)
    return fig
