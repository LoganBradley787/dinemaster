"""Plotly figures for `dinemaster.forecast`. Pure builders: data in, `go.Figure` out.

Styling follows `dinemaster.charts`: ME navy, DD orange, gray reference lines, white template,
horizontal legend above the plot.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dinemaster.charts import COLORS
from dinemaster.config import Config
from dinemaster.forecast import PotForecast

_MARGIN = dict(l=10, r=10, t=40, b=10)
_SHORT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_INK = "#333333"


def _layout(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=title,
        margin=_MARGIN,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_white",
    )
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def weekday_profile_chart(profile: pd.DataFrame) -> go.Figure:
    """The day-of-week view: what a typical Monday .. Sunday looks like.

    `profile` is the frame from `forecast.weekday_profile`. Two stacked panels sharing the
    weekday axis (two measures in different units get two panels, not two y-scales):
        top     stacked bars of the plain per-day averages (ME swipes + DD meals), with a diamond
                marking the rate the forecast actually uses for that weekday (`meals_rate`:
                recent weeks count more, thin weekdays are pulled toward the overall average);
        bottom  average dining dollars spent per day (`dd_spend_avg`) as a line with markers.
    Hovering a weekday shows how many observed days it is based on.

    An empty profile, or one with no observed days, yields an empty figure titled "(no data)".
    """
    title = "Typical day of the week"
    if profile is None or profile.empty or "days" not in profile or int(profile["days"].sum()) == 0:
        return _layout(go.Figure(), f"{title} (no data)")

    days = profile["days"].astype(int).tolist()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.68, 0.32], vertical_spacing=0.10)
    for column, name, color in (("me_avg", "ME swipes", COLORS["me"]), ("dd_meals_avg", "DD meals", COLORS["dd"])):
        fig.add_trace(
            go.Bar(
                x=_SHORT_DAYS, y=profile[column].tolist(), name=name, customdata=days,
                marker=dict(color=color, line=dict(color="white", width=1.5)),
                hovertemplate="%{y:.2f} " + name + " per day (%{customdata} days observed)<extra></extra>",
            ),
            row=1, col=1,
        )
    fig.add_trace(
        go.Scatter(
            x=_SHORT_DAYS, y=profile["meals_rate"].tolist(), name="forecast rate", mode="markers",
            marker=dict(symbol="diamond", size=11, color="white", line=dict(color=_INK, width=2)),
            hovertemplate="%{y:.2f} meals per day used by the forecast<extra></extra>",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=_SHORT_DAYS, y=profile["dd_spend_avg"].tolist(), name="DD $ per day", mode="lines+markers",
            line=dict(color=COLORS["dd"], width=2), marker=dict(size=8, color=COLORS["dd"]),
            hovertemplate="$%{y:.2f} dining dollars per day<extra></extra>",
        ),
        row=2, col=1,
    )
    fig.update_layout(barmode="stack", bargap=0.4)
    fig.update_yaxes(title="Meals per day", rangemode="tozero", row=1, col=1)
    fig.update_yaxes(title="DD $ per day", rangemode="tozero", tickprefix="$", row=2, col=1)
    return _layout(fig, title)


def _add_refs(fig: go.Figure, cfg: Config, observed_through: date | None) -> None:
    """Vertical lines for the last observed day and semester end, plus shaded away periods."""
    if observed_through is not None:
        fig.add_vline(x=observed_through.isoformat(), line_dash="dot", line_color="#666666",
                      annotation_text="data through", annotation_position="top")
    fig.add_vline(x=cfg.semester_end.isoformat(), line_dash="dot", line_color="#999999",
                  annotation_text="semester end", annotation_position="top")
    for period in cfg.away_periods:
        if not period.enabled:
            continue
        fig.add_vrect(
            x0=period.start.isoformat(), x1=period.end.isoformat(),
            fillcolor=COLORS["dd"], opacity=0.10, line_width=0,
            annotation_text=period.name, annotation_position="top left",
        )


def forecast_chart(
    pot_forecast: PotForecast,
    actual_series: pd.Series | None,
    cfg: Config,
    title: str,
    unit: str,
    color: str | None = None,
) -> go.Figure:
    """Balance so far plus where it is likely headed.

    Args:
        pot_forecast: `Forecast.me` or `Forecast.dd`.
        actual_series: date -> end-of-day balance for the observed days (e.g.
            `metrics.daily(df, cfg)["me_bal"]` cut off at `forecast.observed_through`); may be
            None or empty.
        cfg: for the x-range (S..E), the semester-end line, and away-period shading.
        title: figure title.
        unit: what the balance is measured in — "$" formats values as money, anything else
            (e.g. "meals") is appended to the number and used as the y-axis title.
        color: line color; defaults to the pot's color (ME navy, DD orange).

    Draws the actual line (solid) through the last observed day, the expected line (dashed) from
    there to semester end, and the likely range as a shaded band, with a dotted line where the
    data ends. When the forecast has no numbers, only the actual line is drawn and the reason
    is noted on the chart.
    """
    color = color or COLORS.get(pot_forecast.pot.lower(), COLORS["me"])
    money = unit.strip() == "$"
    value = "$%{y:.2f}" if money else "%{y:.1f} " + unit
    fig = go.Figure()

    has_actual = actual_series is not None and len(actual_series) > 0
    if pot_forecast.available:
        low_pct, high_pct = pot_forecast.band
        x = list(pot_forecast.expected.index)
        fig.add_trace(
            go.Scatter(
                x=x, y=list(pot_forecast.hi.values), mode="lines", name="range high",
                line=dict(width=0), showlegend=False,
                hovertemplate=value + " (high end)<extra></extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=x, y=list(pot_forecast.lo.values), mode="lines", name=f"likely range ({low_pct:g}–{high_pct:g}%)",
                line=dict(width=0), fill="tonexty", fillcolor=_rgba(color, 0.18),
                hovertemplate=value + " (low end)<extra></extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=x, y=list(pot_forecast.expected.values), mode="lines", name="expected",
                line=dict(color=color, width=2, dash="dash"),
                hovertemplate=value + " (expected)<extra></extra>",
            )
        )
    if has_actual:
        fig.add_trace(
            go.Scatter(
                x=list(actual_series.index), y=list(actual_series.values), mode="lines", name="actual",
                line=dict(color=color, width=3),
                hovertemplate=value + " (actual)<extra></extra>",
            )
        )

    if pot_forecast.available:
        observed_through = pot_forecast.expected.index[0]
    else:
        observed_through = actual_series.index[-1] if has_actual else None
        fig.add_annotation(
            text=f"No forecast: {pot_forecast.reason}", xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(color="#666666"),
        )
    _add_refs(fig, cfg, observed_through)
    fig.update_xaxes(range=[cfg.semester_start.isoformat(), cfg.semester_end.isoformat()], title="Date",
                     hoverformat="%b %d")
    fig.update_yaxes(title="Dollars" if money else unit, rangemode="tozero", tickprefix="$" if money else "")
    return _layout(fig, title)
