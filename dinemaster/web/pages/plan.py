"""The `plan` section: what to eat today and this week, a what-if rate, and which plan size fits.

Three parts on one page. The daily plan (the next 14 days, the plan by weekday, the weekly rate
ladder, the spend-down), the what-if (`?rate=`, meals a day), and the plan sizes the school sells.
`?dd=1|0` plans with or without the meals dining dollars can buy.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from ... import budget, plans
from ... import forecast as fcast
from ... import metrics as m
from .. import fmt
from ..charts import kit
from ..charts import plan as charts
from ..context import PageContext

RATE_MIN, RATE_MAX, RATE_STEP, RATE_DEFAULT = 0.0, 4.0, 0.25, 2.0
RATES = [RATE_MIN + i * RATE_STEP for i in range(int((RATE_MAX - RATE_MIN) / RATE_STEP) + 1)]
FORTNIGHT = 14


sentences = fmt.sentences  # math-module sentences, with their dashes turned into full stops


def read_rate(raw: str | None) -> float:
    """The what-if rate from the URL: 0 to 4 in quarter steps, 2 when missing or unreadable."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return RATE_DEFAULT
    if value != value:  # NaN
        return RATE_DEFAULT
    return min(RATE_MAX, max(RATE_MIN, round(value / RATE_STEP) * RATE_STEP))


class View:
    """This page's own choices, and links that keep them."""

    def __init__(self, ctx: PageContext, request):
        q = request.query_params
        self.ctx = ctx
        self.default_dd = not ctx.cfg.dd_rollover
        self.include_dd = {"1": True, "0": False}.get(q.get("dd"), self.default_dd)
        self.rate = read_rate(q.get("rate"))

    def url(self, anchor: str, **change) -> str:
        include_dd = change.get("include_dd", self.include_dd)
        return self.ctx.url(
            f"/plan#{anchor}",
            dd=None if include_dd == self.default_dd else int(include_dd),
            rate=None if self.rate == RATE_DEFAULT else f"{self.rate:g}",
        )

    def hidden(self) -> list[tuple[str, str]]:
        """What the what-if form must carry besides the rate."""
        fields = list(self.ctx.params.query().items())
        if self.include_dd != self.default_dd:
            fields.append(("dd", str(int(self.include_dd))))
        return fields


def _daily_plan(ctx: PageContext, view: View) -> dict | None:
    """The day by day plan, or None when the data is too old to say what today should look like."""
    if ctx.stale:
        return None
    cfg, fc = ctx.cfg, ctx.forecast
    known = fc.observed_through + timedelta(days=1)
    start = max(known, m.clamp_as_of(cfg))
    me_left, dd_left = fcast.balances_on(fc, start)
    plan = budget.daily_budget(cfg, me_left, dd_left, fc.profile, start, include_dd=view.include_dd)
    info = plan.attrs
    out: dict = {"line": budget.today_line(plan, cfg.as_of), "reason": None, "plan": plan, "info": info, "start": start}
    if plan.empty or info["reason"]:
        why = info["reason"] or "no days left to plan"
        out["reason"] = sentences(why[:1].upper() + why[1:] + ".")
        return out

    parts = fmt.plural(info["me_meals"], "exchange")
    if info["include_dd"]:
        parts += f" and {fmt.plural(info['dd_meals'], 'meal')} from dining dollars"
    pattern = "spread evenly, with no weekday pattern yet" if info["uniform"] else "weighted toward your usual heavy days"
    out["summary"] = (
        f"Using everything up takes {fmt.plural(info['total'], 'meal')} ({parts}) over {fmt.plural(info['usable_days'], 'day')}, "
        f"{fmt.day(plan.index[0])} to {fmt.day(plan.index[-1])}. They are {pattern}, never more than {info['cap']} in a day."
    )
    out["surplus"] = info["surplus"]
    out["gap"] = None
    if start > known:
        assumed = fmt.span(known, start - timedelta(days=1)).replace("–", " to ").replace("  ", " ")
        out["gap"] = (
            f"The data ends {fmt.day(fc.observed_through)}. The plan starts today and assumes {assumed} went the way "
            f"{'that day' if start - known == timedelta(days=1) else 'those days'} of the week usually "
            f"{'does' if start - known == timedelta(days=1) else 'do'} (about {fmt.plural(fc.me.start_balance - me_left, 'exchange')})."
        )

    # the next 14 days, as whole weeks from Monday
    first = plan.index[0]
    shown = [d for d in (first + timedelta(days=i) for i in range(FORTNIGHT)) if d <= plan.index[-1]]
    weeks, monday = [], first - timedelta(days=first.weekday())
    while monday <= shown[-1]:
        cells = []
        for d in (monday + timedelta(days=i) for i in range(7)):
            inside = shown[0] <= d <= shown[-1]
            cells.append({
                "date": d,
                "month": d == monday or d.day == 1,  # where a phone still names the month
                "planned": inside,
                "away": inside and bool(plan.at[d, "away"]),
                "meals": int(plan.at[d, "meals"]) if inside else 0,
                "today": d == cfg.as_of,
                "is_now": d == cfg.as_of and cfg.as_of == ctx.today,
            })  # fmt: skip
        weeks.append({"label": f"Week of {fmt.day(monday)}", "cells": cells})
        monday += timedelta(days=7)
    out["weeks"] = weeks
    week = shown[:7]
    out["week_meals"] = int(sum(int(plan.at[d, "meals"]) for d in week))
    out["week_days"] = len(week)
    out["shown"] = len(shown)

    # the plan by day of the week
    usable = plan[~plan["away"]]
    per = usable.groupby("weekday")["meals"].agg(["mean", "count"]).reindex(list(budget.WEEKDAY_NAMES))
    usual = fc.profile.set_index("weekday")["meals_rate"].reindex(list(budget.WEEKDAY_NAMES)).fillna(0.0)
    planned = [None if pd.isna(x) else float(x) for x in per["mean"]]
    left = [0 if pd.isna(n) else int(n) for n in per["count"]]
    out["weekday_chart"] = charts.weekday_chart(planned, [float(x) for x in usual], left)
    out["weekdays"] = {
        "columns": [("Day", False), ("Planned meals a day", True), ("Your usual meals a day", True), ("Days left", True)],
        "rows": [
            [name, "none left" if planned[i] is None else fmt.num(planned[i], 1), f"{usual.iloc[i]:.2f}", str(left[i])]
            for i, name in enumerate(budget.WEEKDAY_NAMES)
        ],
    }
    return out


def _exchanges(ctx: PageContext) -> dict | None:
    """Using the exchanges before they expire: only when a real number is set to go unused."""
    cfg, fc = ctx.cfg, ctx.forecast
    if not fcast.going_unused(cfg, fc.me):
        return None
    known = fc.observed_through + timedelta(days=1)
    end, each = cfg.semester_end, cfg.exchange_price
    ladder = budget.rate_ladder(cfg, fc.me.start_balance, float(fc.me.expected.iloc[-1]), known)
    lines = [sentences(line) for line in budget.ladder_lines(ladder)]
    worth = f", about {fmt.money(round(fc.me.leftover_p50) * each)} of the plan" if each else ""
    out: dict = {
        "lead": f"You are on pace to leave {fmt.num(fc.me.leftover_p50, 0)} exchanges unused on {fmt.day(end)} "
        f"(likely {fmt.between(fc.me.leftover_lo, fc.me.leftover_hi)}){worth}.",
        "lines": lines,
        "ladder": ladder,
        "chart": None,
        "table": None,
        "gaps": None,
    }
    if ladder.rungs:
        columns = [("Exchanges a week", False), (f"Unused on {fmt.day(end)}", True)] + ([("Worth", True)] if each else [])
        rows = [[str(rate), str(unused)] + ([fmt.money(unused * each)] if each else []) for rate, unused in ladder.rungs]
        out["chart"] = charts.ladder_chart(ladder, end, each)
        out["table"] = {"columns": columns, "rows": rows}
    gaps = fcast.swipe_gaps(ctx.df, cfg)
    if gaps is not None and gaps.reason is None:
        light, other = sentences(gaps.light), sentences(gaps.other)
        out["gaps"] = (
            f"{fmt.plural(gaps.zero_days, 'day')} so far had no swipe, and {gaps.light_zero} of them were {light} days. "
            f"On {light} you swiped on {gaps.light_swiped} of {fmt.plural(gaps.light_days, 'day')} ({gaps.light_rate:.2f} a day). "
            f"On {other}, {gaps.other_swiped} of {gaps.other_days} ({gaps.other_rate:.2f} a day). "
            f"{gaps.left_light} {light} days are left."
        )
    return out


def _spend_down(ctx: PageContext) -> dict:
    cfg, fc = ctx.cfg, ctx.forecast
    known = fc.observed_through + timedelta(days=1)
    sd = budget.spend_down(cfg, fc.me.start_balance, fc.dd.start_balance, fc.profile, known)
    out: dict = {"sd": sd, "sentence": sentences(sd.sentence), "figures": [], "chart": None, "fine": None}
    if sd.reason is not None:
        return out
    out["figures"] = [
        {"value": fmt.num(sd.exchanges_per_week, 1), "label": "exchanges a week", "kind": kit.MEALS},
        {"value": fmt.num(sd.dd_meals_per_week, 1), "label": "dining-dollar meals a week", "kind": kit.DOLLARS},
        {"value": fmt.num(sd.meals_per_day, 2), "label": "meals a day", "kind": kit.MEALS},
        {"value": fmt.money(sd.snack_reserve), "label": "set aside for snacks", "kind": kit.DOLLARS},
        {"value": fmt.cents(sd.dd_unallocated), "label": "dining dollars left unplanned", "kind": kit.DOLLARS},
    ]
    if cfg.dd_rollover:
        detail = "They carry into spring, so no dining-dollar meals are planned."
    else:
        detail = (
            f"That leaves {fmt.cents(sd.dd_for_meals)} for {fmt.plural(sd.dd_meals, 'meal')} at {fmt.cents(cfg.meal_price)}. "
            "The daily plan turns every dining dollar into meals, so it counts more dining-dollar meals than this does."
        )
    out["fine"] = (
        f"{fmt.plural(sd.usable_days, 'usable day')} ({sd.weeks:.1f} weeks) from {fmt.day(known)} through {fmt.day(sd.end)}. "
        f"Of {fmt.cents(fc.dd.start_balance)} in dining dollars, about {fmt.money(sd.snack_reserve)} is expected to go to snacks "
        f"at your usual weekday rates. {detail}"
    )
    if fc.dd.start_balance > 0:
        out["chart"] = charts.dollars_chart(sd, cfg.meal_price, cfg.dd_rollover)
    return out


def _what_if(ctx: PageContext, view: View) -> dict:
    cfg, bal, a = ctx.cfg, ctx.metrics.balances, ctx.counts.a
    end = fmt.day(cfg.semester_end)

    def at(rate: float) -> m.WhatIfResult:
        return m.what_if(cfg, a, bal.me_left, bal.dd_left, rate, cfg.exclude_away)

    wi = at(view.rate)
    sweep = [(rate, at(rate)) for rate in RATES]

    def ran(when, plural_noun: bool) -> str:
        if when is None:
            return "They last to the end." if plural_noun else "It lasts to the end."
        return f"{'They run' if plural_noun else 'It runs'} out on {fmt.day(when)}."

    def tip(amount, noun: str):
        def text(rate: float, value: float) -> str:
            r = next(res for x, res in sweep if x == rate)
            when = r.me_runout if noun == "exchanges" else r.dd_runout
            last = f"out on {fmt.day(when)}" if when else "lasts to the end"
            return f"{rate:g} {'meal' if rate == 1 else 'meals'} a day\n{amount(value)} left on {end}\n{last}"

        return text

    return {
        "rate": view.rate,
        "rate_text": f"{view.rate:g} {'meal' if view.rate == 1 else 'meals'} a day",
        "min": RATE_MIN, "max": RATE_MAX, "step": RATE_STEP,
        "hidden": view.hidden(),
        "lead": (
            f"Projects from {fmt.day(a + timedelta(days=1))}, the day after your data ends, through {end}. "
            f"Meals use exchanges first, then dining dollars at {fmt.cents(cfg.meal_price)} a meal."
            if a < cfg.semester_end
            else "The semester is over, so no rate changes what is left."
        ),
        "reason": "Nothing is used at 0 meals a day, so both pots stay as they are." if wi.reason else None,
        "figures": [
            {"value": fmt.num(wi.me_end, 1), "label": f"exchanges left on {end}", "detail": ran(wi.me_runout, True), "kind": kit.MEALS},
            {"value": fmt.cents(wi.dd_end), "label": f"dining dollars left on {end}", "detail": ran(wi.dd_runout, False), "kind": kit.DOLLARS},
        ],
        "me_chart": charts.whatif_chart(
            [(r, res.me_end) for r, res in sweep], view.rate, kit.MEALS, f"Exchanges left on {end} at each meals-a-day rate",
            lambda v: fmt.num(v, 0), tip(lambda v: fmt.num(v, 1), "exchanges"),
        ),
        "dd_chart": charts.whatif_chart(
            [(r, res.dd_end) for r, res in sweep], view.rate, kit.DOLLARS, f"Dining dollars left on {end} at each meals-a-day rate",
            fmt.money, tip(fmt.cents, "dollars"),
        ),
    }  # fmt: skip


def _plan_size(ctx: PageContext) -> dict:
    cfg = ctx.cfg
    fits = plans.plan_fits(cfg, ctx.forecast.me)
    if fits.reason:
        return {"reason": sentences(f"Nothing to compare: {fits.reason}.")}
    rows = []
    for r in fits.rows:
        if r.exchanges is None:
            fit = "unlimited"
        elif r.short:
            fit = f"{fmt.num(r.short, 0)} short ({fmt.money(r.extra)} at {fmt.money(cfg.meal_price)} a meal)"
        else:
            fit = f"{fmt.num(r.unused, 0)} unused"
        if r.versus_current is None or r.current:
            versus = ""
        else:
            versus = f"{fmt.money(abs(r.versus_current))} {'less' if r.versus_current < 0 else 'more'}"
        rows.append([
            r.name + (" (yours)" if r.current else ""),
            fmt.money(r.price),
            "unlimited" if r.exchanges is None else fmt.num(r.exchanges, 0),
            fmt.money(r.dining_dollars),
            fit,
            fmt.money(r.food_cost),
            "" if r.per_meal is None else fmt.cents(r.per_meal),
            versus,
        ])  # fmt: skip
    best = min(fits.rows, key=lambda r: r.food_cost)
    if best.current:
        cheapest = "At that usage your plan costs the least."
    elif best.versus_current is not None:
        cheapest = f"At that usage {best.name} costs the least: {fmt.money(abs(best.versus_current))} less than yours."
    else:
        cheapest = f"At that usage {best.name} costs the least."
    return {
        "reason": None,
        "lead": f"This semester you are on pace to use about {fmt.plural(fits.used, 'exchange')} (likely {fmt.between(fits.used_lo, fits.used_hi)}).",
        "cheapest": cheapest,
        "chart": charts.plan_cost_chart(fits, cfg.meal_price),
        "table": {
            "columns": [("Plan", False), ("Price", True), ("Exchanges", True), ("Dining dollars", True), ("At your usage", False),
                        ("Food cost", True), ("Per meal eaten", True), ("Against yours", True)],
            "rows": rows,
        },
        "fine": (
            "Food cost is the plan price, less the dining dollars it comes with, plus the meals it would not cover bought at "
            f"{fmt.cents(cfg.meal_price)} each (the meal price in your settings). It rests on this semester's usage alone, "
            "so it is only as good as this semester is typical."
        ),
    }  # fmt: skip


def _band(ctx: PageContext, daily: dict | None, exchanges: dict | None, spend: dict) -> list[dict]:
    """The few numbers behind "what should I do this week"."""
    cfg, fc, sd = ctx.cfg, ctx.forecast, spend["sd"]
    end = fmt.day(cfg.semester_end)
    known = fc.observed_through + timedelta(days=1)
    stats: list[dict] = []
    if daily and not daily["reason"]:
        days = daily["week_days"]
        stats.append({
            "value": str(daily["week_meals"]),
            "label": "meals in the next 7 days" if days == 7 else f"meals in the last {fmt.plural(days, 'day')}",
            "detail": "Exchanges and dining-dollar meals." if daily["info"]["include_dd"] else "Exchanges only.",
            "kind": kit.MEALS,
        })  # fmt: skip
    if sd.reason is None:
        ladder = exchanges["ladder"] if exchanges else None
        usual = f"The forecast has you at about {fmt.num(ladder.usual_per_week, 1)} a week." if ladder and ladder.reason is None else f"Uses every exchange by {end}."
        stats.append({"value": fmt.num(sd.exchanges_per_week, 1), "label": "exchanges a week uses them all", "detail": usual, "kind": kit.MEALS})
    if exchanges:
        each = cfg.exchange_price
        worth = f"Worth about {fmt.money(round(fc.me.leftover_p50) * each)} of the plan." if each else "They expire."
        stats.append({"value": fmt.num(fc.me.leftover_p50, 0), "label": f"unused on {end} at your usual pace", "detail": worth, "kind": kit.LOOK})
    stats.append({
        "value": str(sd.usable_days),
        "label": "usable days left" if cfg.exclude_away else "days left",
        "detail": f"{fmt.day(known)} to {fmt.day(sd.end)}." if sd.reason is None else sentences(sd.reason[:1].upper() + sd.reason[1:] + "."),
        "kind": kit.DAYS,
    })  # fmt: skip
    if len(stats) < 3:
        stats.insert(0, {"value": fmt.num(max(fc.me.start_balance, 0), 0), "label": "exchanges left", "kind": kit.MEALS})
        stats.insert(1, {"value": fmt.money(max(fc.dd.start_balance, 0)), "label": "dining dollars left", "kind": kit.DOLLARS})
    return stats[:5]


def build(ctx: PageContext, request) -> dict:
    if not ctx.has_data:
        return {"empty": True}
    cfg, fc = ctx.cfg, ctx.forecast
    view = View(ctx, request)
    daily = _daily_plan(ctx, view)
    exchanges = _exchanges(ctx)
    spend = _spend_down(ctx)
    data = {"href": ctx.url("/data"), "link": "Go to data"}

    notes: list[dict] = []
    if daily is None:
        headline = f"Your data ends {fmt.day(fc.observed_through)}, {ctx.behind} days ago."
        sub = "That is too old to say what today should look like. The rates below still hold for the days your data covers."
        notes.append({"kind": kit.PROBLEM, "text": "The day by day plan is left out until there is new data.", **data})
    elif daily["reason"]:  # "The semester is over. No days left to plan."
        headline, _, sub = daily["reason"].partition(". ")
        headline, sub = headline.rstrip(".") + ".", sub or "Nothing to plan."
    else:
        headline, sub = daily["line"] + ".", daily["summary"]
        if daily["surplus"]:
            notes.append({
                "kind": kit.LOOK,
                "text": f"Even at {daily['info']['cap']} meals every day, {fmt.plural(daily['surplus'], 'meal')} would go unused by semester end.",
            })  # fmt: skip
        if daily["gap"]:
            notes.append({"kind": kit.DAYS, "text": daily["gap"] + " Get new data for a sharper plan.", **data})

    modes = [
        {"label": "Exchanges only", "href": view.url("days", include_dd=False), "on": not view.include_dd},
        {"label": "With dining dollars", "href": view.url("days", include_dd=True), "on": view.include_dd},
    ]
    jump = [("Day by day", view.url("days"))] if daily and not daily["reason"] else []
    if exchanges:
        jump.append(("Using the exchanges", view.url("exchanges")))
    jump += [("Spend-down", view.url("spend-down")), ("What if", view.url("what-if")), ("Plan size", view.url("plan-size"))]

    return {
        "empty": False,
        "headline": headline,
        "sub": sub,
        "notes": notes,
        "band": _band(ctx, daily, exchanges, spend),
        "jump": jump,
        "daily": daily if daily and not daily["reason"] else None,
        "modes": modes,
        "weekday_names": charts.SHORT_DAYS,
        "exchanges": exchanges,
        "spend": spend,
        "whatif": _what_if(ctx, view),
        "size": _plan_size(ctx),
    }
