"""Plotly figure builders for the Habits tab.

Pure: each function takes a result from `dinemaster.habits` and returns a
`plotly.graph_objects.Figure`. No Streamlit, no I/O. Styling follows `charts.py`
(ME navy, DD orange, `plotly_white`, no chart junk); with nothing to draw the figure is empty and
its title ends in "(no data)".
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from dinemaster.charts import COLORS
from dinemaster.habits import WEEKDAYS, format_hour

_MARGIN = dict(l=10, r=10, t=40, b=10)
_SURFACE = "#FFFFFF"
# One hue, light to dark: empty cells stay near the surface, busy cells reach ME navy.
_HEAT_SCALE = [[0.0, "#F3F4F7"], [0.25, "#C5CBDA"], [0.6, "#6C789C"], [1.0, COLORS["me"]]]


def _layout(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=title,
        margin=_MARGIN,
        template="plotly_white",
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _hour_label(hour: int) -> str:
    """Compact axis label for a clock hour: 0 -> "12a", 13 -> "1p"."""
    return f"{hour % 12 or 12}{'a' if hour < 12 else 'p'}"


def heatmap_chart(matrix: pd.DataFrame | None) -> go.Figure:
    """Weekday x hour heatmap of meals, from `habits.hour_weekday_matrix`.

    Monday is the top row, hours run left to right from 12a to 11p, darker navy = more meals,
    and each non-empty cell is labelled with its count. An all-zero, empty or None matrix gives
    an empty figure titled "When you eat (no data)".
    """
    fig = go.Figure()
    if matrix is None or matrix.empty or int(matrix.to_numpy().max()) <= 0:
        return _layout(fig, "When you eat (no data)")

    z = matrix.to_numpy()
    hours = [int(c) for c in matrix.columns]
    fig.add_trace(
        go.Heatmap(
            z=z,
            x=[_hour_label(h) for h in hours],
            y=[str(w) for w in matrix.index],
            customdata=[[format_hour(h) for h in hours] for _ in matrix.index],
            text=[[str(int(v)) if v > 0 else "" for v in row] for row in z],
            texttemplate="%{text}",
            colorscale=_HEAT_SCALE,
            zmin=0,
            zmax=max(int(z.max()), 1),
            xgap=2,
            ygap=2,
            colorbar=dict(title="meals", thickness=10, len=0.8, outlinewidth=0, tickformat="d"),
            hovertemplate="%{y}, %{customdata}: %{z} meals<extra></extra>",
        )
    )
    fig.update_xaxes(title="Hour of day", type="category", showgrid=False, fixedrange=True)
    fig.update_yaxes(
        autorange="reversed", type="category", categoryorder="array", categoryarray=WEEKDAYS,
        showgrid=False, fixedrange=True,
    )
    fig.update_layout(height=340, plot_bgcolor=_SURFACE)
    return _layout(fig, "When you eat (meals by weekday and hour)")


def place_chart(place_stats: pd.DataFrame | None) -> go.Figure:
    """Visits per place as horizontal stacked bars, from `habits.place_stats`.

    One bar per place, busiest at the top: ME swipes (navy) stacked with DD purchases (orange),
    both counted in visits so they share one axis. The bar end is labelled with total visits;
    hover adds DD dollars, the typical time of day and the favorite weekday. An empty or None
    frame gives an empty figure titled "Where you eat (no data)".
    """
    fig = go.Figure()
    if place_stats is None or place_stats.empty:
        return _layout(fig, "Where you eat (no data)")

    stats = place_stats.iloc[::-1]  # plotly draws the first category at the bottom
    places = [str(p) for p in stats.index]
    dd_purchases = stats["dd_meals"] + stats["dd_snacks"]
    habit = [
        f"usually around {format_hour(hour)}, most often on {weekday}"
        for hour, weekday in zip(stats["typical_hour"], stats["favorite_weekday"])
    ]
    gap = dict(color=_SURFACE, width=2)

    fig.add_trace(
        go.Bar(
            x=stats["swipes"],
            y=places,
            orientation="h",
            name="ME swipes",
            marker=dict(color=COLORS["me"], line=gap),
            customdata=habit,
            hovertemplate="%{y}: %{x:.0f} ME swipes<br>%{customdata}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=dd_purchases,
            y=places,
            orientation="h",
            name="DD purchases",
            marker=dict(color=COLORS["dd"], line=gap),
            customdata=list(zip(stats["dd_spend"], stats["dd_meals"], stats["dd_snacks"], habit)),
            hovertemplate=(
                "%{y}: %{x:.0f} DD purchases, $%{customdata[0]:.2f}"
                "<br>%{customdata[1]:.0f} meals, %{customdata[2]:.0f} snacks"
                "<br>%{customdata[3]}<extra></extra>"
            ),
        )
    )
    for place, visits in zip(places, stats["visits"]):
        fig.add_annotation(
            x=float(visits), y=place, text=f"{int(visits)}", showarrow=False, xanchor="left", xshift=6,
            font=dict(color="#4A5568"),
        )
    fig.update_layout(barmode="stack", height=max(260, 34 * len(places) + 120), legend_traceorder="normal")
    fig.update_xaxes(title="Visits", rangemode="tozero", tickformat="d")
    fig.update_yaxes(type="category", showgrid=False, ticksuffix=" ")
    return _layout(fig, "Where you eat (visits by place)")
