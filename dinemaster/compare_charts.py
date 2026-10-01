"""Plotly figures for the semester comparison (`dinemaster.compare`).

Pure builders in the style of `dinemaster.charts`: computed data in, `plotly.graph_objects.Figure`
out, no Streamlit and no I/O. The current semester is drawn in its pot's color (ME navy, DD
orange); past semesters are gray, and also differ by dash so they are not told apart by shade alone.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dinemaster.charts import COLORS
from dinemaster.compare import SemesterSummary

_POT = {
    "ME": {"color": COLORS["me"], "title": "Meal exchanges", "axis": "Swipes per week", "value": "%{y:.0f} swipes"},
    "DD": {"color": COLORS["dd"], "title": "Dining dollars", "axis": "Dollars per week", "value": "$%{y:.2f}"},
}
# Past semesters, oldest-listed first: (gray, dash). More than three reuse the styles in order.
_HISTORY_STYLES = [(COLORS["ideal"], "solid"), ("#5E6B7A", "dash"), ("#9AA5B1", "dot")]
_MARGIN = dict(l=10, r=10, t=40, b=10)


def _layout(fig: go.Figure, title: str, **extra) -> go.Figure:
    fig.update_layout(title=title, margin=_MARGIN, template="plotly_white", **extra)
    return fig


def weekly_overlay_chart(weekly: pd.DataFrame, pot: str) -> go.Figure:
    """Each semester's weekly usage of one pot as a line over "week of semester".

    `weekly` is the frame from `compare.weekly_usage`; `pot` is "ME" (swipes) or "DD" (dollars).
    One line per semester that has rows for the pot, past semesters first and gray, the current
    semester last in the pot's color. Weeks only partly covered (the edges of an old export, the
    current unfinished week) get a hollow marker and say so on hover, since their totals are for
    fewer than seven days. With no rows for the pot the figure is empty and titled "(no data)".
    """
    style = _POT[pot]
    name = f"{style['title']} by week of semester"
    fig = go.Figure()
    rows = weekly[weekly["pot"] == pot] if not weekly.empty else weekly
    if rows.empty:
        return _layout(fig, f"{name} (no data)")

    past = 0
    for semester, group in rows.groupby("semester", sort=False):
        current = bool(group["current"].iloc[0])
        if current:
            color, dash, width = style["color"], "solid", 3
        else:
            color, dash = _HISTORY_STYLES[past % len(_HISTORY_STYLES)]
            width = 2
            past += 1
        part = ["" if done else f" (part week, {days} of 7 days)" for done, days in zip(group["complete"], group["days"])]
        fig.add_trace(
            go.Scatter(
                x=list(group["week"]),
                y=list(group["amount"]),
                mode="lines+markers",
                name=semester,
                line=dict(color=color, width=width, dash=dash),
                marker=dict(
                    size=8,
                    color=color,
                    symbol=["circle" if done else "circle-open" for done in group["complete"]],
                    line=dict(color=color, width=2),
                ),
                customdata=list(zip([f"{d:%b} {d.day}" for d in group["week_start"]], part)),
                hovertemplate=f"{semester}, week of %{{customdata[0]}}: {style['value']}%{{customdata[1]}}<extra></extra>",
            )
        )
    fig.update_xaxes(title="Week of semester", dtick=1, range=[0.5, int(rows["week"].max()) + 0.5])
    fig.update_yaxes(title=style["axis"], rangemode="tozero")
    return _layout(
        fig,
        name,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )


def per_day_comparison_chart(summaries: list[SemesterSummary]) -> go.Figure:
    """Average use per covered day, one bar per semester, in two side-by-side panels.

    `summaries` is the list from `compare.semester_summaries`. The left panel is meal exchanges
    (swipes per day), the right dining dollars (dollars per day) — separate panels because the two
    units share no scale. Exactly two bar traces, ME then DD, each with one x position per
    semester in the order given; a semester without that pot (or without rate days) has no bar
    and a small "no data" label instead. Hover gives the days the average is taken over and, for
    dining dollars, the meal/snack split. An empty list gives an empty figure titled "(no data)".
    """
    title = "Average use per day, by semester"
    if not summaries:
        return _layout(go.Figure(), f"{title} (no data)")

    fig = make_subplots(rows=1, cols=2, subplot_titles=("Meal exchanges per day", "Dining dollars per day"))
    names = [s.name for s in summaries]

    for col, pot in enumerate(("ME", "DD"), start=1):
        rates, labels, hovers = [], [], []
        for s in summaries:
            usage = s.pots[pot]
            rate = usage.per_day if usage is not None else None
            rates.append(rate)
            if rate is None:
                labels.append("")
                hovers.append("")
                fig.add_annotation(
                    x=s.name, y=0, yanchor="bottom", text="no data", showarrow=False,
                    font=dict(color="#888888", size=12), row=1, col=col,
                )  # fmt: skip
                continue
            if pot == "ME":
                labels.append(f"{rate:.2f}")
                detail = f"{usage.used:.0f} swipes over {s.rate_days} days"
            else:
                labels.append(f"${rate:.2f}")
                detail = (
                    f"${usage.used:.2f} over {s.rate_days} days<br>"
                    f"{s.dd_meal_count} meals (${s.dd_meal_amount:.2f}), "
                    f"{s.dd_snack_count} snacks (${s.dd_snack_amount:.2f})"
                )
            hovers.append(f"{s.name}: {labels[-1]} a day<br>{detail}")
        fig.add_trace(
            go.Bar(
                x=names,
                y=rates,
                name=_POT[pot]["title"],
                marker_color=_POT[pot]["color"],
                text=labels,
                textposition="outside",
                cliponaxis=False,
                customdata=hovers,
                hovertemplate="%{customdata}<extra></extra>",
            ),
            row=1,
            col=col,
        )
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=names)
    fig.update_yaxes(rangemode="tozero")
    fig.update_yaxes(title="Swipes per day", row=1, col=1)
    fig.update_yaxes(title="Dollars per day", tickprefix="$", row=1, col=2)
    # extra top margin: the panel titles sit between the figure title and the plots
    return _layout(fig, title, showlegend=False, bargap=0.45).update_layout(margin=dict(t=80))
