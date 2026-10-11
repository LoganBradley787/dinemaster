"""Charts for the `compare` section: average use by semester, weekly use overlaid by week of
semester, and each semester's most visited places.

This semester is drawn in its pot's tone, solid. A past semester is the same tone tinted and
dashed, so it is never told apart by shade alone. A week the data only partly covers gets a
hollow point.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
from markupsafe import Markup

from ...compare import SemesterSummary
from ...tiles import cents, day, num, plural
from . import kit

TONES = {"ME": kit.MEALS, "DD": kit.DOLLARS}
WORDS = {"ME": "meal exchange", "DD": "dining dollar"}

#: Past semesters in config order: (tint, dash). More than three reuse the styles in order.
PAST = ((0.6, "7 5"), (0.3, "2 4"), (0.45, "11 4 2 4"))


def past_style(index: int, pot: str) -> tuple[kit.Tone, str]:
    """The tone and dash of the `index`-th past semester for `pot`. It follows the semester, not its rank in a chart."""
    amount, dash = PAST[index % len(PAST)]
    return kit.tint(TONES[pot], amount), dash


def amount(pot: str, v: float) -> str:
    """A weekly or semester amount in words: "10 swipes", "$30.37"."""
    return plural(v, "swipe") if pot == "ME" else cents(v)


def rate(pot: str, v: float, per: str) -> str:
    """An average: "7.7" or "1.10" exchanges, "$12.47"."""
    if pot == "DD":
        return cents(v)
    return num(v, 2 if per == "day" else 1)


def rate_chart(summaries: Sequence[SemesterSummary], pot: str, per: str) -> Markup:
    """Average use of one pot per covered day or week: one bar per semester, oldest first.

    A semester without that pot (or without any rate days) has no bar and says "no records".
    """
    label = f"Average {WORDS[pot]} use a {per}, by semester"
    tone = TONES[pot]
    field = "per_day" if per == "day" else "per_week"
    values = [(s, getattr(s.pots[pot], field) if s.pots[pot] is not None else None) for s in summaries]
    if all(v is None for _, v in values):
        return kit.figure(lambda size: kit.empty(size, f"No {WORDS[pot]} records in any semester."), label, heights=(120, 120, 120))
    top = max(v for _, v in values if v is not None) or 1.0
    rows = []
    for s, v in values:
        if v is None:
            rows.append((s.name, [kit.Seg(0.0, tone)]))
            continue
        usage = s.pots[pot]
        lines = [s.name, f"{rate(pot, v, per)} a {per}", f"{amount(pot, usage.used)} over {plural(s.rate_days, 'day')}"]
        if pot == "DD":
            lines.append(f"{plural(s.dd_meal_count, 'meal')} ({cents(s.dd_meal_amount)}), {plural(s.dd_snack_count, 'snack')} ({cents(s.dd_snack_amount)})")
        rows.append((s.name, [kit.Seg(v, tone if s.current else kit.tint(tone, 0.5), "\n".join(lines))]))
    tick = (lambda v: f"${v:g}") if pot == "DD" else (lambda v: f"{v:g}")

    def draw(size: kit.Size):
        p = kit.plot(size, left=44, top=24)
        x = kit.band_scale([s.name for s in summaries], p.x0, p.x1, pad=0.4)
        y = kit.linear_scale(0, top * 1.05, p.y0, p.y1, count=4)
        parts = [kit.grid_y(p, y, tick), kit.axis_x(p, x), kit.vbars(p, x, y, rows, label=lambda v: rate(pot, v, per), max_width=84)]
        for s, v in values:
            if v is None and kit.text_width("no records") <= x.step:
                parts.append(kit.text(x(s.name), p.y0 - 8, "no records", anchor="middle"))
        return parts

    keys = [kit.Key("this semester", tone, "box")]
    if any(not s.current and v is not None for s, v in values):
        keys.append(kit.Key("a past semester", kit.tint(tone, 0.5), "box"))
    return kit.figure(draw, label, heights=(220, 250, 270), legend=kit.legend(keys))


def weekly_chart(weekly: pd.DataFrame, pot: str, names: Sequence[str]) -> Markup:
    """Each semester's weekly use of one pot as a line over the week of the semester.

    `weekly` is `compare.weekly_usage`; `names` are the semester names in config order with the
    current one last, which fixes each semester's style. A semester with no rows for the pot
    draws nothing.
    """
    label = f"{WORDS[pot].capitalize()} use by week of semester, one line per semester"
    rows = weekly[weekly["pot"] == pot] if not weekly.empty else weekly
    if rows.empty:
        return kit.figure(lambda size: kit.empty(size, f"No {WORDS[pot]} records in any semester."), label, heights=(120, 120, 120))
    tone = TONES[pot]
    series = []  # (name, tone, dash, current, [(week, amount, week_start, days, complete)])
    for i, name in enumerate(names):
        group = rows[rows["semester"] == name]
        if group.empty:
            continue
        current = bool(group["current"].iloc[0])
        color, dash = (tone, None) if current else past_style(i, pot)
        pts = [(int(r.week), float(r.amount), r.week_start, int(r.days), bool(r.complete)) for r in group.itertuples()]
        series.append((name, color, dash, current, pts))
    last = int(rows["week"].max())
    weeks = list(range(1, max(last, 4) + 1))
    top = float(rows["amount"].max()) or 1.0
    tips = []
    for week in weeks:
        lines = [f"Week {week}"]
        for name, _, _, _, pts in series:
            for w, v, start, days, complete in pts:
                if w == week:
                    part = "" if complete else f" (part week, {days} of 7 days)"
                    lines.append(f"{name}, week of {day(start)}: {amount(pot, v)}{part}")
        tips.append((week, "\n".join(lines) if len(lines) > 1 else ""))
    tick = (lambda v: f"${v:,.0f}") if pot == "DD" else (lambda v: num(v, 0))

    def draw(size: kit.Size):
        p = kit.plot(size, left=40, top=16)
        x = kit.band_scale(weeks, p.x0, p.x1, pad=0)
        y = kit.linear_scale(0, top * 1.05, p.y0, p.y1, count=4)
        parts = [kit.grid_y(p, y, tick), kit.axis_x(p, x)]
        for name, color, dash, current, pts in sorted(series, key=lambda s: s[3]):  # this semester on top
            line = [(w, v) for w, v, *_ in pts]
            parts.append(kit.line(x, y, line, color, width=3 if current else 2, dash=dash))
            if len(pts) == 1:
                parts.append(kit.points(x, y, line, color, hollow=not pts[0][4]))
            parts.append(kit.points(x, y, [(w, v) for w, v, _, _, complete in pts if not complete], color, r=4.5, hollow=True))
        parts.append(kit.hover_columns(p, x, tips))
        return parts

    keys = [kit.Key(name, color, "dash" if dash else "line") for name, color, dash, _, _ in series]
    return kit.figure(draw, label, legend=kit.legend(keys))


def places_chart(s: SemesterSummary) -> Markup:
    """One semester's most visited places: exchange swipes, with dining-dollar purchases stacked on them."""
    rows = []
    for place in s.top_places:
        bought = place.visits - place.me_swipes
        lines = [place.place, plural(place.visits, "visit")]
        if place.me_swipes:
            lines.append(plural(place.me_swipes, "exchange swipe"))
        if bought or place.dd_spend:
            lines.append(f"{cents(place.dd_spend)} in dining dollars on {plural(bought, 'purchase')}")
        tip = "\n".join(lines)
        rows.append((place.place, [kit.Seg(float(place.me_swipes), kit.MEALS, tip), kit.Seg(float(bought), kit.DOLLARS, tip, striped=True)]))
    keys = []
    if any(p.me_swipes for p in s.top_places):
        keys.append(kit.Key("exchange swipes", kit.MEALS, "box"))
    if any(p.visits - p.me_swipes for p in s.top_places):
        keys.append(kit.Key("dining-dollar purchases", kit.DOLLARS, "stripe"))
    return kit.hbar_figure(rows, f"Most visited places in {s.name}", legend_keys=keys or None, empty_text="No places on record.")
