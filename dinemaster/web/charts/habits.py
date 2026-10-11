"""Charts for the `habits` section: meals by weekday and hour, visits by place, one week's meals day by day."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import pandas as pd
from markupsafe import Markup

from ...habits import WEEKDAYS, format_hour
from ...tiles import cents, day, plural
from . import kit

WEEKDAY_NAMES = dict(zip(WEEKDAYS, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]))
MIN_HOURS = 10  # never show fewer hour columns than this


def hour_label(hour: int) -> str:
    """Short axis label for a clock hour: 0 -> "12a", 13 -> "1p"."""
    return f"{hour % 12 or 12}{'a' if hour < 12 else 'p'}"


def shown_hours(matrix: pd.DataFrame, rollover: int) -> list[int]:
    """The hour columns to draw, in dining-day order (the day starts at `rollover`, so hours after
    midnight come last). Empty hours before the first meal and after the last are left out, keeping
    one empty hour on each side and at least `MIN_HOURS` columns."""
    order = [(rollover + i) % 24 for i in range(24)]
    used = [i for i, h in enumerate(order) if int(matrix[h].sum()) > 0]
    if not used:
        return order
    lo, hi = max(0, used[0] - 1), min(23, used[-1] + 1)
    while hi - lo + 1 < MIN_HOURS:
        if lo > 0:
            lo -= 1
        if hi < 23 and hi - lo + 1 < MIN_HOURS:
            hi += 1
    return order[lo : hi + 1]


def heat_chart(matrix: pd.DataFrame, rollover: int = 4, late: Sequence[int] = ()) -> Markup:
    """Meals by weekday (rows, Monday on top) and hour (columns). A stronger square is more meals.

    `late` are the late-night clock hours; the ones on the chart get a bracket above them.
    """
    label = "Meals by weekday and hour of the day"
    if matrix is None or matrix.empty or int(matrix.to_numpy().max()) <= 0:
        return kit.figure(lambda size: kit.empty(size, "No meals yet."), label, heights=(120, 120, 120))
    hours = shown_hours(matrix, rollover)
    cells = {(h, wd): float(matrix.loc[wd, h]) for wd in matrix.index for h in hours if matrix.loc[wd, h] > 0}
    late_shown = [h for h in hours if h in set(late)]

    def tip(h, wd, v) -> str:
        return f"{WEEKDAY_NAMES[wd]} around {format_hour(h)}\n{plural(v, 'meal')}"

    def draw(size: kit.Size):
        p = kit.plot(size, lanes=1 if late_shown else 0, left=40, right=2, top=6)
        x = kit.band_scale(hours, p.x0, p.x1, pad=0)
        y = kit.band_scale(list(matrix.index), p.y1, p.y0, pad=0)
        parts = [
            kit.axis_y(p, y),
            kit.axis_x(p, x, labels=hour_label, min_gap=30),
            kit.heatmap(x, y, cells, kit.MEALS, tip=tip, label=lambda v: f"{v:.0f}"),
        ]
        if late_shown:
            x0, x1 = x(late_shown[0]) - x.step / 2 + 1, x(late_shown[-1]) + x.step / 2 - 1
            words = "late night"
            if kit.text_width(words) <= x1 - x0:
                yy = p.y1 - 5
                parts.append(Markup(f'<line class="leader" x1="{x0:.1f}" x2="{x1:.1f}" y1="{yy:.1f}" y2="{yy:.1f}"/>'))
                parts.append(kit.text(x0, yy - 5, words))
        return parts

    return kit.figure(draw, label, heights=(230, 290, 340))


def places_chart(places: pd.DataFrame) -> Markup:
    """Visits by place, most first: exchange swipes with dining-dollar purchases stacked after them."""
    rows = []
    for place, r in places.iterrows():
        habit = f"Usually around {format_hour(r['typical_hour'])}, most often on {WEEKDAY_NAMES[r['favorite_weekday']]}"
        bought = int(r["dd_meals"]) + int(r["dd_snacks"])
        dd_tip = (
            f"{place}\n{plural(bought, 'dining-dollar purchase')}, {cents(float(r['dd_spend']))}\n"
            f"{plural(int(r['dd_meals']), 'meal')}, {plural(int(r['dd_snacks']), 'snack')}\n{habit}"
        )
        segs = [
            kit.Seg(float(r["swipes"]), kit.MEALS, f"{place}\n{plural(int(r['swipes']), 'exchange swipe')}\n{habit}"),
            kit.Seg(float(bought), kit.DOLLARS, dd_tip, striped=True),
        ]
        rows.append((str(place), segs))
    keys = [kit.Key("exchange swipes", kit.MEALS, "box"), kit.Key("dining-dollar purchases", kit.DOLLARS, "stripe")]
    return kit.hbar_figure(rows, "Visits by place", legend_keys=keys, empty_text="No visits yet.")


def week_chart(daily: pd.DataFrame, monday: date) -> Markup:
    """One Monday-to-Sunday week, meals each day. Days outside `daily` (not in the data) have no bar."""
    label = f"Meals each day in the week of {day(monday)}"
    days = [monday + timedelta(days=i) for i in range(7)]
    known = [d for d in days if d in daily.index]
    if not known:
        return kit.figure(lambda size: kit.empty(size, "No days in your data for this week."), label, heights=(120, 120, 120))
    rows, tips = [], []
    for d in days:
        head = f"{d:%A}, {day(d)}"
        if d not in daily.index:
            tips.append((d, f"{head}\nNot in your data"))
            continue
        swipes, dd_meals = float(daily.at[d, "me_swipes"]), float(daily.at[d, "dd_meals"])
        rows.append((d, [kit.Seg(swipes, kit.MEALS), kit.Seg(dd_meals, kit.DOLLARS, striped=True)]))
        lines = [head, plural(swipes, "exchange swipe")]
        if dd_meals:
            lines.append(plural(dd_meals, "dining-dollar meal"))
        tips.append((d, "\n".join(lines)))
    top = max(3.0, max(float(daily.at[d, "meals"]) for d in known))

    def draw(size: kit.Size):
        p = kit.plot(size, left=26, top=12)
        x = kit.band_scale(days, p.x0, p.x1, pad=0.35)
        y = kit.linear_scale(0, top, p.y0, p.y1, count=3)
        return [
            kit.grid_y(p, y),
            kit.axis_x(p, x, labels=lambda d: f"{d:%a} {d.day}", min_gap=30),
            kit.vbars(p, x, y, rows, label=lambda v: f"{v:g}", max_width=40, gap=2),
            kit.hover_columns(p, x, tips),
        ]

    keys = [kit.Key("exchange swipes", kit.MEALS, "box"), kit.Key("dining-dollar meals", kit.DOLLARS, "stripe")]
    return kit.figure(draw, label, heights=(200, 220, 230), legend=kit.legend(keys))
