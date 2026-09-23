"""Plotly figure builders for DineMaster.

Every function here is pure: it takes already-computed data (pandas Series/
DataFrames produced by ``dinemaster.metrics``) plus a ``Config``, and returns
a ``plotly.graph_objects.Figure``. Nothing here touches Streamlit, reads a
file, or does I/O — callers (app.py) are responsible for computing the input
series/frames via ``dinemaster.metrics`` and passing them in.

Color convention (kept consistent across every chart):
    ME    navy    (#232D4B)
    DD    orange  (#E57200)
    ideal gray, dashed reference line for pace
    projection    same color as the series it projects, dashed
Charts show the full semester on the x-axis (S..E), mark "today" (as-of) and
semester end with vertical lines, and shade enabled away periods.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go

from dinemaster.config import Config

COLORS = {
    "me": "#232D4B",
    "dd": "#E57200",
    "pooled": "#2C6E49",
    "ideal": "#9AA5B1",
    "snack": "#F2C29A",
    "avg": "#5B7FBD",
}

_LAYOUT_MARGIN = dict(l=10, r=10, t=40, b=10)


def _base_layout(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=title,
        margin=_LAYOUT_MARGIN,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_white",
    )
    return fig


def _add_calendar_refs(fig: go.Figure, cfg: Config, a: date) -> None:
    """Add the as-of / semester-end vertical lines and shaded away periods.

    Plotly's shape helpers (add_vline/add_vrect) want date *strings*, not raw
    `datetime.date` objects, when the x-axis is a date axis — so every
    boundary is converted with `.isoformat()` before being passed in.
    """
    fig.add_vline(
        x=a.isoformat(), line_dash="dot", line_color="#666666", annotation_text="today", annotation_position="top"
    )
    fig.add_vline(
        x=cfg.semester_end.isoformat(),
        line_dash="dot",
        line_color="#999999",
        annotation_text="semester end",
        annotation_position="top",
    )
    for period in cfg.away_periods:
        if not period.enabled:
            continue
        fig.add_vrect(
            x0=period.start.isoformat(),
            x1=period.end.isoformat(),
            fillcolor="#E57200",
            opacity=0.10,
            line_width=0,
            annotation_text=period.name,
            annotation_position="top left",
        )


def balance_chart(
    cfg: Config,
    a: date,
    actual: pd.Series,
    ideal: pd.Series,
    projection: pd.Series | None,
    color: str,
    title: str,
    y_title: str,
) -> go.Figure:
    """Balance-over-time chart: actual (solid), ideal (gray line), projection (dashed).

    `actual` is indexed S..A (or a subset), `ideal` S..E, `projection` (if
    given) A..min(run-out, E). Values are whatever unit the caller wants
    (dollars or meal-equivalents) — this function only styles them.
    """
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=list(ideal.index),
            y=list(ideal.values),
            mode="lines",
            name="ideal pace",
            line=dict(color=COLORS["ideal"], width=2, dash="solid"),
            hovertemplate="%{x|%b %d}: %{y:.1f} (ideal)<extra></extra>",
        )
    )
    if actual is not None and len(actual) > 0:
        fig.add_trace(
            go.Scatter(
                x=list(actual.index),
                y=list(actual.values),
                mode="lines",
                name="actual",
                line=dict(color=color, width=3),
                hovertemplate="%{x|%b %d}: %{y:.1f} (actual)<extra></extra>",
            )
        )
    if projection is not None and len(projection) > 0:
        fig.add_trace(
            go.Scatter(
                x=list(projection.index),
                y=list(projection.values),
                mode="lines",
                name="projected (burn rate)",
                line=dict(color=color, width=2, dash="dash"),
                hovertemplate="%{x|%b %d}: %{y:.1f} (projected)<extra></extra>",
            )
        )
    _add_calendar_refs(fig, cfg, a)
    fig.update_xaxes(range=[cfg.semester_start.isoformat(), cfg.semester_end.isoformat()], title="Date")
    fig.update_yaxes(title=y_title, rangemode="tozero")
    return _base_layout(fig, title)


def daily_meals_chart(
    cfg: Config,
    daily: pd.DataFrame,
    pace_me_only: float | None,
    pace_with_dd: float | None,
) -> go.Figure:
    """Stacked daily-meals bars (ME swipes + DD meals) with a 7-day rolling average
    line and horizontal reference lines for the currently-allowed pace.
    """
    fig = go.Figure()
    if daily.empty:
        _add_calendar_refs(fig, cfg, cfg.semester_start)
        return _base_layout(fig, "Daily meals (no data)")

    idx = list(daily.index)
    fig.add_trace(
        go.Bar(
            x=idx,
            y=daily["me_swipes"],
            name="ME swipes",
            marker_color=COLORS["me"],
            hovertemplate="%{x|%b %d}: %{y:.0f} ME<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=idx,
            y=daily["dd_meals"],
            name="DD meals",
            marker_color=COLORS["dd"],
            hovertemplate="%{x|%b %d}: %{y:.0f} DD meals<extra></extra>",
        )
    )
    rolling = daily["meals"].rolling(7, min_periods=1).mean()
    fig.add_trace(
        go.Scatter(
            x=idx,
            y=rolling,
            name="7-day avg",
            mode="lines",
            line=dict(color=COLORS["avg"], width=2),
            hovertemplate="%{x|%b %d}: %{y:.2f} avg<extra></extra>",
        )
    )
    if pace_me_only is not None:
        fig.add_hline(
            y=pace_me_only,
            line_dash="dash",
            line_color=COLORS["me"],
            annotation_text=f"allowed pace, ME-only ({pace_me_only:.2f}/day)",
            annotation_position="right",
        )
    if pace_with_dd is not None:
        fig.add_hline(
            y=pace_with_dd,
            line_dash="dash",
            line_color=COLORS["dd"],
            annotation_text=f"allowed pace, with DD ({pace_with_dd:.2f}/day)",
            annotation_position="right",
        )
    fig.update_layout(barmode="stack")
    a = daily.index.max()
    _add_calendar_refs(fig, cfg, a)
    fig.update_xaxes(range=[cfg.semester_start.isoformat(), cfg.semester_end.isoformat()], title="Date")
    fig.update_yaxes(title="Meals")
    return _base_layout(fig, "Daily meals")


def _weekly_buckets(daily: pd.DataFrame) -> pd.DataFrame:
    """Bucket each day's meal count into 0/1/2/3+ and group into Monday-start weeks."""
    if daily.empty:
        return pd.DataFrame(columns=["week_start", "0", "1", "2", "3+"])
    counts = daily["meals"].clip(lower=0).round().clip(upper=3).astype(int)
    idx = pd.to_datetime(pd.Index(daily.index))
    week_start = (idx - pd.to_timedelta(idx.weekday, unit="D")).normalize()
    frame = pd.DataFrame({"count": counts.values, "week_start": week_start})
    pivot = frame.pivot_table(index="week_start", columns="count", values="count", aggfunc="size", fill_value=0)
    for col in (0, 1, 2, 3):
        if col not in pivot.columns:
            pivot[col] = 0
    pivot = pivot[[0, 1, 2, 3]]
    pivot.columns = ["0", "1", "2", "3+"]
    return pivot.reset_index()


def weekly_mix_chart(
    cfg: Config,
    daily: pd.DataFrame,
    target_one_per_week: float | None,
    target_two_per_week: float | None,
) -> go.Figure:
    """Stacked bars of 0/1/2/3+-meal-day counts per Monday-start week, with target
    one-/two-meal-days-per-week reference lines (the pooled with-DD day mix).
    """
    fig = go.Figure()
    pivot = _weekly_buckets(daily)
    if pivot.empty:
        return _base_layout(fig, "Weekly day mix (no data)")

    bucket_colors = {"0": "#D9DCE1", "1": COLORS["ideal"], "2": COLORS["dd"], "3+": COLORS["me"]}
    for bucket in ("0", "1", "2", "3+"):
        fig.add_trace(
            go.Bar(
                x=pivot["week_start"],
                y=pivot[bucket],
                name=f"{bucket} meals/day",
                marker_color=bucket_colors[bucket],
                hovertemplate="week of %{x|%b %d}: %{y:.0f} days" + f" ({bucket} meals)<extra></extra>",
            )
        )
    if target_one_per_week is not None:
        fig.add_hline(
            y=target_one_per_week,
            line_dash="dash",
            line_color=COLORS["ideal"],
            annotation_text=f"target 1-meal days/wk ({target_one_per_week:.1f})",
            annotation_position="left",
        )
    if target_two_per_week is not None:
        fig.add_hline(
            y=target_two_per_week,
            line_dash="dash",
            line_color=COLORS["dd"],
            annotation_text=f"target 2-meal days/wk ({target_two_per_week:.1f})",
            annotation_position="left",
        )
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title="Week of")
    fig.update_yaxes(title="Days", range=[0, 7.5])
    return _base_layout(fig, "Weekly day mix")


def me_location_chart(counts: pd.Series) -> go.Figure:
    """Horizontal bar of ME swipe counts by location (description)."""
    fig = go.Figure()
    if counts is None or counts.empty:
        return _base_layout(fig, "ME swipes by location (no data)")
    counts = counts.sort_values(ascending=True)
    fig.add_trace(
        go.Bar(
            x=counts.values,
            y=counts.index,
            orientation="h",
            marker_color=COLORS["me"],
            hovertemplate="%{y}: %{x:.0f} swipes<extra></extra>",
        )
    )
    fig.update_xaxes(title="Swipes")
    return _base_layout(fig, "ME swipes by location")


def dd_location_chart(by_location: pd.DataFrame) -> go.Figure:
    """Horizontal stacked bar of DD $ spend by location, split meal vs snack.

    `by_location` columns: location, meal_amount, snack_amount.
    """
    fig = go.Figure()
    if by_location is None or by_location.empty:
        return _base_layout(fig, "DD spend by location (no data)")
    ordered = by_location.assign(total=by_location["meal_amount"] + by_location["snack_amount"]).sort_values("total")
    fig.add_trace(
        go.Bar(
            x=ordered["meal_amount"],
            y=ordered["location"],
            orientation="h",
            name="meal",
            marker_color=COLORS["dd"],
            hovertemplate="%{y}: $%{x:.2f} meal<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=ordered["snack_amount"],
            y=ordered["location"],
            orientation="h",
            name="snack",
            marker_color=COLORS["snack"],
            hovertemplate="%{y}: $%{x:.2f} snack<extra></extra>",
        )
    )
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title="Dollars spent")
    return _base_layout(fig, "DD spend by location")
