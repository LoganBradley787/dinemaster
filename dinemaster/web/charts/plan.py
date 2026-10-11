"""Charts for the `plan` section: the plan by weekday, the weekly rate ladder, where the dining
dollars go, what each what-if rate leaves, and what each plan size would cost."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from markupsafe import Markup

from ...budget import Ladder, SpendDown
from ...plans import PlanFits
from ...tiles import cents, day, money, num, plural
from . import kit

SHORT_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LONG_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: A lighter partner of teal: dining dollars that are spoken for in a second way (meals bought on top of a plan).
DOLLARS_2 = kit.tint(kit.DOLLARS, 0.5)
#: The what-if bar for the rate that is picked: the same measure, set apart from its neighbours.
PICKED = 0.6


def weekday_chart(planned: Sequence[float | None], usual: Sequence[float], days_left: Sequence[int]) -> Markup:
    """Meals a day the plan gives each weekday, beside what that weekday usually sees."""
    label = "Planned meals a day for each weekday, beside your usual"
    if not any(days_left):
        return kit.figure(lambda size: kit.empty(size, "No days left to plan."), label, heights=(120, 120, 120))
    top = max([1.0, *[v or 0.0 for v in planned], *usual])
    rows = []
    for i, name in enumerate(SHORT_DAYS):
        plan, norm = planned[i], float(usual[i])
        left = plural(days_left[i], "day") + " left"
        if plan is None:
            first = kit.Seg(0.0, kit.MEALS)
        else:
            first = kit.Seg(float(plan), kit.MEALS, f"{LONG_DAYS[i]}\n{num(plan, 1)} planned a day\n{num(norm, 2)} usual\n{left}")
        rows.append((name, [first, kit.Seg(norm, kit.DIM, f"{LONG_DAYS[i]}\n{num(norm, 2)} usual a day\n{left}")]))

    def draw(size: kit.Size):
        p = kit.plot(size, left=30, top=18)
        x = kit.band_scale(SHORT_DAYS, p.x0, p.x1, pad=0.22)
        y = kit.linear_scale(0, top, p.y0, p.y1, count=3)
        return [kit.grid_y(p, y), kit.axis_x(p, x), kit.vbars(p, x, y, rows, grouped=True, label=lambda v: num(v, 1), max_width=34)]

    keys = [kit.Key("planned from here", kit.MEALS, "box"), kit.Key("your usual", kit.DIM, "box")]
    return kit.figure(draw, label, heights=(220, 250, 270), legend=kit.legend(keys))


def ladder_chart(ladder: Ladder, end, each: float | None) -> Markup:
    """Exchanges left unused at each weekly rate, from the usual rate up to the one that uses them all."""

    def worth(unused: float) -> str:
        if unused <= 0:
            return "uses them all"
        return f"{num(unused, 0)} unused" + (f", {money(unused * each)}" if each else "")

    rows = []
    for rate, unused in ladder.rungs:
        tip = f"{rate} exchanges a week\n{plural(unused, 'exchange')} unused on {day(end)}" + (f"\nworth {money(unused * each)}" if each else "")
        rows.append((f"{rate} a week", [kit.Seg(float(unused), kit.MEALS, tip)]))
    return kit.hbar_figure(rows, f"Exchanges unused on {day(end)} at each weekly rate", fmt=worth, empty_text="Nothing to compare.")


def dollars_chart(sd: SpendDown, meal_price: float, rollover: bool) -> Markup:
    """What the dining dollars are for: snacks, meals, and what is left over."""
    rows = [("Snacks", [kit.Seg(float(sd.snack_reserve or 0.0), kit.DOLLARS, f"Snacks\n{cents(sd.snack_reserve or 0.0)} at your usual weekday rates")])]
    if not rollover:
        spent = (sd.dd_meals or 0) * meal_price
        rows.append(("Meals", [kit.Seg(float(spent), kit.DOLLARS, f"Meals\n{cents(spent)} for {plural(sd.dd_meals or 0, 'meal')} at {cents(meal_price)}")]))
    rest = "Carries into spring" if rollover else "Left unplanned"
    rows.append((rest, [kit.Seg(float(sd.dd_unallocated or 0.0), kit.DOLLARS, f"{rest}\n{cents(sd.dd_unallocated or 0.0)}")]))
    return kit.hbar_figure(rows, "What the dining dollars are for", fmt=money, empty_text="No dining dollars to plan.")


def whatif_chart(
    points: Sequence[tuple[float, float]], picked: float, tone: kit.Tone, label: str, fmt: Callable[[float], str], tip: Callable[[float, float], str]
) -> Markup:
    """What is left at semester end at every meals-a-day rate; the picked rate is set apart and labelled."""
    top = max([v for _, v in points], default=0.0) or 1.0
    rows = [(rate, [kit.Seg(float(v), kit.tint(tone, PICKED) if rate == picked else tone, tip(rate, v))]) for rate, v in points]
    rates = [rate for rate, _ in points]
    chosen = dict(points).get(picked)

    def draw(size: kit.Size):
        p = kit.plot(size, left=44, top=22, bottom=40)
        x = kit.band_scale(rates, p.x0, p.x1, pad=0.2)
        y = kit.linear_scale(0, top, p.y0, p.y1, count=4)
        parts = [
            kit.grid_y(p, y, fmt),
            kit.axis_x(p, x, labels=lambda r: f"{r:g}"),
            kit.text((p.x0 + p.x1) / 2, p.y0 + 34, "meals a day", anchor="middle"),
            kit.vbars(p, x, y, rows, max_width=40),
        ]
        if chosen is not None:
            cx = min(max(x(picked), p.x0 + kit.text_width(fmt(chosen)) / 2), p.x1 - kit.text_width(fmt(chosen)) / 2)
            parts.append(kit.text(cx, y(chosen) - 6, fmt(chosen), anchor="middle", cls="lbl"))
        return parts

    keys = [kit.Key(f"at {picked:g} a day", kit.tint(tone, PICKED), "box"), kit.Key("at other rates", tone, "box")]
    return kit.figure(draw, label, heights=(230, 250, 270), legend=kit.legend(keys))


def plan_cost_chart(fits: PlanFits, meal_price: float) -> Markup:
    """What the food costs under each plan: its price less its dining dollars, plus the meals it would not cover."""
    rows = []
    for r in fits.rows:
        name = r.name + (" (yours)" if r.current else "")
        base = max(0.0, r.price - r.dining_dollars)
        segs = [kit.Seg(base, kit.DOLLARS, f"{name}\n{money(r.price)} less {money(r.dining_dollars)} in dining dollars\n{money(base)}")]
        if r.extra > 0:
            segs.append(kit.Seg(float(r.extra), DOLLARS_2, f"{name}\n{plural(r.short, 'meal')} it would not cover\n{money(r.extra)} at {cents(meal_price)} each", striped=True))
        rows.append((name, segs))
    keys = [kit.Key("plan price less its dining dollars", kit.DOLLARS, "box"), kit.Key("meals it would not cover", DOLLARS_2, "stripe")]
    return kit.hbar_figure(rows, "Food cost under each plan at your usage", fmt=money, legend_keys=keys, empty_text="No plans to compare.")
