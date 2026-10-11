"""Charts for the `now` section: balances against an even pace, daily meals, the weekly mix, places."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd
from markupsafe import Markup

from ...config import Config
from ...tiles import cents, day, num, plural
from . import kit

#: Days in a week by how many meals they had: the meals hue, lighter for more; a day without a meal is a faint blank.
MIX = (
    ("0", "no meal", kit.FAINT),
    ("1", "one meal", kit.tint(kit.MEALS, -0.22)),
    ("2", "two meals", kit.tint(kit.MEALS, 0.25)),
    ("3+", "three or more", kit.tint(kit.MEALS, 0.62)),
)


def balance_chart(
    cfg: Config,
    a: date,
    actual: pd.Series,
    ideal: pd.Series,
    projection: pd.Series | None,
    color: kit.Tone,
    label: str,
    fmt: Callable[[float], str] = lambda v: num(v, 0),
    today_label: str = "today",
) -> Markup:
    """What is left day by day (solid), an even pace (thin), and the pace so far carried on (dashed).

    `actual` runs from the semester start to `a`, `ideal` over the whole semester, `projection`
    from `a` on. The x-axis is always the whole semester.
    """
    series = [s for s in (actual, ideal, projection) if s is not None and len(s)]
    top = max((float(s.max()) for s in series), default=1.0) or 1.0
    known = {d: float(v) for d, v in actual.items()}
    even = {d: float(v) for d, v in ideal.items()}
    ahead = {} if projection is None else {d: float(v) for d, v in projection.items()}

    def tip(d: date) -> str:
        lines = [f"{d:%a}, {day(d)}"]
        if d in known:
            lines.append(f"{fmt(known[d])} left")
        elif d in ahead:
            lines.append(f"{fmt(ahead[d])} left at your pace so far")
        if d in even:
            lines.append(f"{fmt(even[d])} at an even pace")
        return "\n".join(lines)

    def draw(size: kit.Size):
        p = kit.plot(size, lanes=2, left=46)
        x = kit.time_scale(cfg.semester_start, cfg.semester_end, p.x0, p.x1)
        y = kit.linear_scale(0, top * 1.04, p.y0, p.y1, count=4, nice=False)
        parts = [
            kit.notes(p, x, kit.semester_notes(cfg, a if len(actual) else None, today_label)),
            kit.grid_y(p, y, fmt),
            kit.axis_x(p, x),
            kit.line(x, y, list(even.items()), kit.DIM, width=1.5),
        ]
        if ahead:
            parts.append(kit.dashed_line(x, y, list(ahead.items()), color))
        if known:
            parts.append(kit.line(x, y, list(known.items()), color, width=3))
            parts.append(kit.end_label(p, x, y, a, known.get(a, list(known.values())[-1]), fmt(known.get(a, list(known.values())[-1])), color))
        days = [cfg.semester_start + timedelta(days=i) for i in range(x.days)]
        parts.append(kit.hover_columns(p, x, [(d, tip(d)) for d in days]))
        return parts

    keys = [kit.Key("what is left", color), kit.Key("an even pace", kit.DIM, "thin")]
    if ahead:
        keys.insert(1, kit.Key("your pace so far, carried on", color, "dash"))
    return kit.figure(draw, label, legend=kit.legend(keys))


def daily_meals_chart(cfg: Config, daily: pd.DataFrame, pace_me: float | None, pace_with_dd: float | None) -> Markup:
    """Meals each day (exchange swipes with dining-dollar meals stacked on top), the 7-day average, and the pace you can afford."""
    label = "Meals each day, with the 7-day average"
    if daily.empty or float(daily["meals"].sum()) <= 0:
        return kit.figure(lambda size: kit.empty(size, "No meals yet."), label, heights=(120, 120, 120))
    first, last = daily.index[0], max(daily.index[-1], daily.index[0] + timedelta(days=27))
    rolling = daily["meals"].rolling(7, min_periods=1).mean()
    paces = [v for v in (pace_me, pace_with_dd) if v is not None]
    top = max([float(daily["meals"].max()), 3.0, *[v for v in paces if v <= 6]])
    rows, tips = [], []
    for d, row in daily.iterrows():
        swipes, dd_meals = float(row["me_swipes"]), float(row["dd_meals"])
        rows.append((d, [kit.Seg(swipes, kit.MEALS), kit.Seg(dd_meals, kit.DOLLARS, striped=True)]))
        lines = [f"{d:%a}, {day(d)}", plural(swipes, "exchange swipe")]
        if dd_meals:
            lines.append(plural(dd_meals, "dining-dollar meal"))
        if cfg.is_away(d):
            lines.append("away")
        lines.append(f"7-day average {num(float(rolling[d]), 2)}")
        tips.append((d, "\n".join(lines)))
    both = pace_me is not None and pace_with_dd is not None and abs(pace_me - pace_with_dd) > 0.005

    def draw(size: kit.Size):
        p = kit.plot(size, lanes=1, left=30)
        x = kit.time_scale(first, last, p.x0, p.x1)
        y = kit.linear_scale(0, top, p.y0, p.y1, count=4)
        away = [n for n in kit.semester_notes(cfg, markers=False) if n.kind == "away"]
        parts = [kit.notes(p, x, away), kit.grid_y(p, y), kit.axis_x(p, x), kit.vbars(p, x, y, rows, max_width=18)]
        parts.append(kit.line(x, y, [(d, float(v)) for d, v in rolling.items()], kit.INK, width=2))
        short = size.compact
        if pace_me is not None and pace_me <= y.hi:
            text = f"{num(pace_me, 2)} a day uses the exchanges" if not short else f"{num(pace_me, 2)} uses exchanges"
            parts.append(kit.hline(p, y, pace_me, text, kit.DIM, below=both and pace_me < pace_with_dd))
        if both and pace_with_dd <= y.hi:
            text = f"{num(pace_with_dd, 2)} a day with dining dollars" if not short else f"{num(pace_with_dd, 2)} with dining $"
            parts.append(kit.hline(p, y, pace_with_dd, text, kit.DIM, below=pace_with_dd < pace_me))
        parts.append(kit.hover_columns(p, x, tips))
        return parts

    keys = [kit.Key("exchange swipes", kit.MEALS, "box")]
    if float(daily["dd_meals"].sum()) > 0:
        keys.append(kit.Key("dining-dollar meals", kit.DOLLARS, "stripe"))
    keys.append(kit.Key("7-day average", kit.INK))
    if paces:
        keys.append(kit.Key("the pace that uses everything", kit.DIM, "dash"))
    return kit.figure(draw, label, legend=kit.legend(keys))


def weekly_buckets(daily: pd.DataFrame) -> list[tuple[date, dict[str, int]]]:
    """Per Monday-start week: how many days had 0, 1, 2 and 3+ meals."""
    weeks: dict[date, dict[str, int]] = {}
    for d, meals in daily["meals"].items():
        bucket = MIX[min(3, max(0, int(round(float(meals)))))][0]
        week = weeks.setdefault(d - timedelta(days=d.weekday()), {key: 0 for key, _, _ in MIX})
        week[bucket] += 1
    return sorted(weeks.items())


def weekly_mix_chart(daily: pd.DataFrame) -> Markup:
    """Each week's days, split by how many meals they had."""
    label = "Days in each week by number of meals"
    weeks = weekly_buckets(daily) if not daily.empty else []
    if not weeks:
        return kit.figure(lambda size: kit.empty(size, "No weeks yet."), label, heights=(120, 120, 120))
    rows, tips = [], []
    for monday, counts in weeks:
        rows.append((monday, [kit.Seg(counts[key], color) for key, _, color in MIX]))
        lines = [f"Week of {day(monday)}"] + [f"{plural(counts[key], 'day')}: {name}" for key, name, _ in MIX if counts[key]]
        tips.append((monday, "\n".join(lines)))

    def draw(size: kit.Size):
        p = kit.plot(size, left=30)
        x = kit.band_scale([monday for monday, _ in weeks], p.x0, p.x1, pad=0.3)
        y = kit.linear_scale(0, 7, p.y0, p.y1, nice=False)
        return [
            kit.grid_y(p, y, ticks=(0, 2, 4, 6)),
            kit.axis_x(p, x, labels=day),
            kit.vbars(p, x, y, rows, max_width=44, gap=2),
            kit.hover_columns(p, x, tips),
        ]

    keys = [kit.Key(name, color, "box") for _, name, color in MIX]
    return kit.figure(draw, label, heights=(220, 250, 270), legend=kit.legend(keys))


def me_places_chart(counts: pd.Series) -> Markup:
    """Exchange swipes by place, most first."""
    ordered = counts.sort_values(ascending=False)
    rows = [(str(place), [kit.Seg(float(n), kit.MEALS, f"{place}\n{plural(float(n), 'swipe')}")]) for place, n in ordered.items()]
    return kit.hbar_figure(rows, "Exchange swipes by place", empty_text="No swipes yet.")


def dd_places_chart(by_location: dict[str, dict[str, float]]) -> Markup:
    """Dining dollars by place, meals and snacks apart, most first."""
    snack = kit.tint(kit.DOLLARS, 0.5)
    ordered = sorted(by_location.items(), key=lambda item: -(item[1]["meal_amount"] + item[1]["snack_amount"]))
    rows = []
    for place, v in ordered:
        segs = [
            kit.Seg(float(v["meal_amount"]), kit.DOLLARS, f"{place}\n{cents(v['meal_amount'])} on {plural(v['meal_count'], 'meal')}"),
            kit.Seg(float(v["snack_amount"]), snack, f"{place}\n{cents(v['snack_amount'])} on {plural(v['snack_count'], 'snack')}"),
        ]
        rows.append((str(place), segs))
    keys = [kit.Key("meals", kit.DOLLARS, "box"), kit.Key("snacks", snack, "box")]
    return kit.hbar_figure(rows, "Dining dollars spent by place", fmt=lambda v: f"${v:,.0f}", legend_keys=keys, empty_text="No dining dollars spent yet.")
