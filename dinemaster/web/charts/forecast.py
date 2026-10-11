"""Charts for the `forecast` section: a typical week, and where each pot is headed."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd
from markupsafe import Markup

from ...config import Config
from ...forecast import PotForecast
from ...tiles import cents, day, money, num
from . import kit
from .kit import _n, _tip  # the kit's own number and tooltip writers, for the one local mark below

SHORT_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
#: The share of dining dollars that goes on snacks: the same meaning as dining dollars, lighter.
SNACK = kit.tint(kit.DOLLARS, 0.5)


def rate_ticks(x: kit.Band, y: kit.Linear, values: list[tuple[object, float, str]], max_width: float) -> Markup:
    """A short level mark across each bar: the rate the forecast uses for that key. values = [(key, value, tip)].

    Local to this section (the kit has no bullet-style target mark). A reference mark, so it is
    drawn in the ink colour with a ring in the ground colour to stay clear of the bar under it.
    """
    half = min(max_width, x.bw) / 2 + 5
    out = []
    for key, value, tip in values:
        cx, cy = x(key), y(value)
        out.append(f'<rect x="{_n(cx - half - 1.5)}" y="{_n(cy - 3)}" width="{_n(2 * half + 3)}" height="6" fill="{kit.BG}"/>')
        out.append(f'<rect class="pt" x="{_n(cx - half)}" y="{_n(cy - 1.5)}" width="{_n(2 * half)}" height="3" fill="{kit.INK}"{_tip(tip)}/>')
    return Markup("".join(out))


def _has_days(profile: pd.DataFrame | None) -> bool:
    return profile is not None and not profile.empty and "days" in profile and int(profile["days"].sum()) > 0


def _week_figure(rows, ticks, tips, top: float, fmt: Callable[[float], str], label: str, keys: list[kit.Key]) -> Markup:
    def draw(size: kit.Size):
        p = kit.plot(size, left=44, top=14)
        x = kit.band_scale(list(SHORT_DAYS), p.x0, p.x1, pad=0.42)
        y = kit.linear_scale(0, top, p.y0, p.y1, count=4)
        return [
            kit.grid_y(p, y, fmt),
            kit.axis_x(p, x),
            kit.vbars(p, x, y, rows, max_width=46, gap=2),
            rate_ticks(x, y, ticks, 46),
            kit.hover_columns(p, x, tips),
        ]

    return kit.figure(draw, label, heights=(230, 250, 270), legend=kit.legend(keys))


def week_meals_chart(profile: pd.DataFrame) -> Markup:
    """Meals on a typical Monday to Sunday: exchange swipes with dining-dollar meals stacked on top
    (plain averages), and a level mark at the rate the forecast uses for that weekday."""
    label = "Meals on a typical day of the week, with the rate the forecast uses"
    if not _has_days(profile):
        return kit.figure(lambda size: kit.empty(size, "No observed days yet."), label, heights=(120, 120, 120))
    rows, ticks, tips = [], [], []
    for short, (_, r) in zip(SHORT_DAYS, profile.iterrows()):
        days = int(r["days"])
        rows.append((short, [kit.Seg(float(r["me_avg"]), kit.MEALS), kit.Seg(float(r["dd_meals_avg"]), kit.DOLLARS, striped=True)]))
        ticks.append((short, float(r["meals_rate"]), ""))
        seen = f"{days} observed {'day' if days == 1 else 'days'}"
        lines = [f"{r['weekday']}, {seen}"]
        if days:
            lines += [f"{num(float(r['me_avg']), 2)} exchange swipes a day", f"{num(float(r['dd_meals_avg']), 2)} dining-dollar meals a day"]
        lines.append(f"The forecast uses {num(float(r['meals_rate']), 2)} meals a day")
        tips.append((short, "\n".join(lines)))
    top = max(float((profile["me_avg"] + profile["dd_meals_avg"]).max()), float(profile["meals_rate"].max()), 1.0)
    keys = [kit.Key("exchange swipes", kit.MEALS, "box")]
    if float(profile["dd_meals_avg"].sum()) > 0:
        keys.append(kit.Key("dining-dollar meals", kit.DOLLARS, "stripe"))
    keys.append(kit.Key("the rate the forecast uses", kit.INK))
    return _week_figure(rows, ticks, tips, top * 1.08, lambda v: num(v, 1), label, keys)


def week_dollars_chart(profile: pd.DataFrame) -> Markup:
    """Dining dollars spent on a typical Monday to Sunday (meals, with snacks lighter on top), and
    a level mark at the spending rate the forecast uses."""
    label = "Dining dollars spent on a typical day of the week, with the rate the forecast uses"
    if not _has_days(profile):
        return kit.figure(lambda size: kit.empty(size, "No observed days yet."), label, heights=(120, 120, 120))
    if float(profile["dd_spend_avg"].abs().sum()) <= 0 and float(profile["dd_spend_rate"].abs().sum()) <= 0:
        return kit.figure(lambda size: kit.empty(size, "No dining dollars spent yet."), label, heights=(120, 120, 120))
    rows, ticks, tips = [], [], []
    for short, (_, r) in zip(SHORT_DAYS, profile.iterrows()):
        days = int(r["days"])
        spend, snack = float(r["dd_spend_avg"]), float(r["dd_snack_avg"])
        rows.append((short, [kit.Seg(max(spend - snack, 0.0), kit.DOLLARS), kit.Seg(max(snack, 0.0), SNACK)]))
        ticks.append((short, max(float(r["dd_spend_rate"]), 0.0), ""))
        lines = [f"{r['weekday']}, {days} observed {'day' if days == 1 else 'days'}"]
        if days:
            lines += [f"{cents(spend)} a day", f"{cents(snack)} of it on snacks"]
        lines.append(f"The forecast uses {cents(float(r['dd_spend_rate']))} a day")
        tips.append((short, "\n".join(lines)))
    top = max(float(profile["dd_spend_avg"].max()), float(profile["dd_spend_rate"].max()), 1.0)
    keys = [kit.Key("meals", kit.DOLLARS, "box")]
    if float(profile["dd_snack_avg"].sum()) > 0:
        keys.append(kit.Key("snacks", SNACK, "box"))
    keys.append(kit.Key("the rate the forecast uses", kit.INK))
    return _week_figure(rows, ticks, tips, top * 1.08, money, label, keys)


def pot_chart(
    cfg: Config,
    pf: PotForecast,
    actual: pd.Series,
    color: kit.Tone,
    label: str,
    fmt: Callable[[float], str] = lambda v: num(v, 0),
    per: float = 1.0,
    observed_label: str = "today",
) -> Markup:
    """What is left so far (solid, through the last observed day), the expected balance from there
    to the semester end (dashed), and the likely range (shaded), over the whole semester.

    `per` divides every value (dining dollars shown as meals). With no forecast only the solid
    line is drawn and the chart says so.
    """
    known = {d: float(v) / per for d, v in actual.items()}
    expected = {d: float(v) / per for d, v in pf.expected.items()} if pf.available else {}
    lo = {d: float(v) / per for d, v in pf.lo.items()} if pf.available else {}
    hi = {d: float(v) / per for d, v in pf.hi.items()} if pf.available else {}
    ahead = len(expected) > 1
    o = next(iter(expected)) if expected else (list(known)[-1] if known else None)
    top = max([*known.values(), *hi.values(), *expected.values(), 0.0]) or 1.0
    low_pct, high_pct = pf.band

    def tip(d: date) -> str:
        lines = [f"{d:%a}, {day(d)}"]
        if d in known:
            lines.append(f"{fmt(known[d])} left")
        elif d in expected:
            lines.append(f"{fmt(expected[d])} expected")
            lines.append(f"likely {fmt(lo[d])} to {fmt(hi[d])}")
        if cfg.exclude_away and cfg.is_away(d):
            lines.append("away")
        return "\n".join(lines)

    def draw(size: kit.Size):
        p = kit.plot(size, lanes=2, left=46)
        x = kit.time_scale(cfg.semester_start, cfg.semester_end, p.x0, p.x1)
        y = kit.linear_scale(0, top * 1.04, p.y0, p.y1, count=4, nice=False)
        parts = [kit.notes(p, x, kit.semester_notes(cfg, o, observed_label)), kit.grid_y(p, y, fmt), kit.axis_x(p, x)]
        if ahead:
            parts.append(kit.area_band(x, y, list(lo.items()), list(hi.items()), color))
            parts.append(kit.dashed_line(x, y, list(expected.items()), color))
        if known:
            parts.append(kit.line(x, y, list(known.items()), color, width=3))
        if ahead:
            end = cfg.semester_end
            parts.append(kit.end_label(p, x, y, end, expected[end], fmt(expected[end]), color))
        if known:
            last = o if o in known else list(known)[-1]
            parts.append(kit.end_label(p, x, y, last, known[last], fmt(known[last]), color))
        if not pf.available:
            parts.append(kit.text((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2, "No forecast yet.", anchor="middle", cls="ax empty"))
        days = [cfg.semester_start + timedelta(days=i) for i in range(x.days)]
        parts.append(kit.hover_columns(p, x, [(d, tip(d)) for d in days if d in known or d in expected]))
        return parts

    keys = [kit.Key("what the data shows", color)]
    if ahead:
        keys.append(kit.Key("expected", color, "dash"))
        keys.append(kit.Key(f"likely range, the middle {high_pct - low_pct:g}%", color, "box range"))
    return kit.figure(draw, label, legend=kit.legend(keys) if len(keys) > 1 else None)
