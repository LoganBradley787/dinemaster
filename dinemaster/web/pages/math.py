"""The `math` section: every formula's result, counting every day and leaving out days away, side by side.

This is the page for checking the numbers. `build` returns a headline, notes, a number band, one
small chart and six blocks. A block is a table: groups of rows, and for each row the result in
both columns with the arithmetic that gave it. The column the rest of the app uses is marked.

View values of its own: `working=0` hides the formulas and the arithmetic.
"""

from __future__ import annotations

from datetime import date

from ... import forecast as fcast
from ... import metrics as m
from .. import fmt
from ..charts import kit
from ..charts import math as charts
from ..context import PageContext

VARIANTS = ("plain", "away")
COLUMN = {"plain": "Counting every day", "away": "Leaving out days away"}
OVER = "The semester is over."
NO_RATE = "Not enough data yet."


# --- small formatters: fixed decimals, so the two columns line up ----------------------------------


def f1(x: float) -> str:
    return f"{round(x, 1) + 0.0:,.1f}"  # + 0.0 so a net of -0.0 prints as 0.0


def f2(x: float) -> str:
    return f"{round(x, 2) + 0.0:,.2f}"


def n(x: float) -> str:
    """An input as it is: 104, 17, 1.5."""
    return fmt.num(x, 2)


def signed1(x: float) -> str:
    return "0.0" if abs(x) < 0.05 else ("+" if x > 0 else "-") + f1(abs(x))


def full_day(d: date, cfg) -> str:
    """"Dec 1", with the year when it is not the year the semester ends in."""
    return fmt.day(d) if d.year == cfg.semester_end.year else f"{fmt.day(d)}, {d.year}"


def cell(text: str, calc: str | None = None, sub: str | None = None) -> dict:
    """One result: the value, a word under it (ahead, short), and the arithmetic that gave it."""
    return {"text": text, "calc": calc, "sub": sub, "words": False}


def words(text: str) -> dict:
    """A cell that holds a sentence where a number cannot be worked out."""
    return {"text": text, "calc": None, "sub": None, "words": True}


def row(label: str, how: str, plain: dict, away: dict) -> dict:
    return {"label": label, "how": how, "cells": [plain, away], "both": None}


def same(label: str, how: str, both: dict) -> dict:
    """A row whose value does not depend on days away: one cell across both columns."""
    return {"label": label, "how": how, "cells": [], "both": both}


def group(title: str, kind, rows: list[dict]) -> dict:
    return {"title": title, "kind": kind, "rows": rows}


def rounding_words(cfg) -> str:
    how = {"ceil": "rounded up", "round": "rounded"}.get(cfg.rounding_mode, "rounded down")
    g = cfg.rounding_granularity or 1.0
    return f"{how} to whole meals" if g == 1 else f"{how} to the nearest {n(g)}"


def mix_note(note: str | None) -> str:
    if not note:
        return "None."
    if note.startswith("surplus"):
        return "More than two a day. Using it all needs some three-meal days."
    if note.startswith("shortfall"):
        return "Less than one a day."
    return OVER


# --- the blocks -----------------------------------------------------------------------------------


def days_block(ctx: PageContext) -> dict:
    cfg, dc, bal = ctx.cfg, ctx.counts, ctx.metrics.balances
    a, s, e = fmt.day(dc.a), fmt.day(dc.s), fmt.day(dc.e)

    def share(x: float | None, top: int, bottom: int) -> dict:
        return words("The semester has no days.") if x is None else cell(f"{x * 100:.1f}%", f"{top} ÷ {bottom}")

    calendar = [
        row("Days in the semester", f"{s} to {e}, both ends counted.",
            cell(str(dc.t)), cell(str(dc.t_away), f"{dc.t} − {dc.away_tot} away")),
        row("Days gone by", f"Through {a}. That day counts as gone by.",
            cell(str(dc.de)), cell(str(dc.de_away), f"{dc.de} − {dc.away_el} away")),
        row("Days to go", f"From the day after {a} through {e}.",
            cell(str(dc.dr)), cell(str(dc.dr_away), f"{dc.dr} − {dc.away_rem} away")),
        row("Share of the semester to go", "Days to go ÷ days in the semester.",
            share(dc.frac_remaining, dc.dr, dc.t), share(dc.frac_remaining_away, dc.dr_away, dc.t_away)),
    ]  # fmt: skip
    dd_meals = cfg.round_meals(bal.dd_left / cfg.meal_price)
    left = [
        same("Meal exchanges left", "Starting exchanges − exchanges used.", cell(n(bal.me_left), f"{n(cfg.starting_me)} − {n(bal.me_used)}")),
        same("Dining dollars left", "Starting dining dollars − dining dollars spent.",
             cell(fmt.cents(bal.dd_left), f"{fmt.cents(cfg.starting_dd)} − {fmt.cents(bal.dd_used)}")),
        same("Dining dollars as meals", f"Dining dollars left ÷ the price of a meal, {rounding_words(cfg)}.",
             cell(n(dd_meals), f"{fmt.cents(bal.dd_left)} ÷ {fmt.cents(cfg.meal_price)}")),
    ]  # fmt: skip
    periods = [p for p in cfg.away_periods if p.enabled and p.end >= cfg.semester_start and p.start <= cfg.semester_end]
    if periods:
        listed = ", ".join(f"{p.name} ({fmt.day(p.start)} to {fmt.day(p.end)})" for p in periods)
        away_line = f"Days away: {listed}."
    else:
        away_line = "No days away are set, so the two columns are the same."
    return {
        "id": "days",
        "title": "the days",
        "answers": "How many days each formula counts, and what is left to spread over them.",
        "groups": [group("The calendar, in days", kit.DAYS, calendar), group(f"What is left on {a}", None, left)],
        "fine": [
            f"The day the figures are for counts as gone by. Days to go start the day after it and include the last day, {e}.",
            away_line,
        ],
    }


def targets_block(ctx: PageContext) -> dict:
    cfg, dc, bal, t = ctx.cfg, ctx.counts, ctx.metrics.balances, ctx.metrics.targets
    share = {"plain": dc.frac_remaining, "away": dc.frac_remaining_away}
    start_meals = cfg.round_meals(cfg.starting_dd / cfg.meal_price)
    left_meals = cfg.round_meals(bal.dd_left / cfg.meal_price)
    none = words("The semester has no days.")

    def pct(v: str) -> str:
        return f"{share[v] * 100:.1f}%"

    def target(v: str, value: float | None, calc) -> dict:
        return none if value is None else cell(f1(value), calc(v))

    def against(v: str, delta: float | None, goal: float | None) -> dict:
        if delta is None:
            return none
        sub = "on the target" if abs(delta) < 0.05 else "more left" if delta > 0 else "fewer left"
        return cell(signed1(delta), f"{n(bal.me_left)} − {f1(goal)}", sub)

    strict = [
        row("Target for today", "Starting exchanges × share of the semester to go.",
            *(target(v, t[v].strict_target, lambda v: f"{n(cfg.starting_me)} × {pct(v)}") for v in VARIANTS)),
        row("Left against the target", "Exchanges left − target. Plus means more are left than an even pace would leave.",
            *(against(v, t[v].strict_delta, t[v].strict_target) for v in VARIANTS)),
    ]  # fmt: skip
    pooled = [
        row("All the meals in the plan", "Starting exchanges + starting dining dollars as meals.",
            *(target(v, t[v].pooled_total, lambda v: f"{n(cfg.starting_me)} + {n(start_meals)}") for v in VARIANTS)),
        row("Target for today", "All the meals × share to go − dining dollars left as meals.",
            *(target(v, t[v].pooled_target, lambda v: f"{n(t[v].pooled_total)} × {pct(v)} − {n(left_meals)}") for v in VARIANTS)),
        row("Left against the target", "Exchanges left − target.",
            *(against(v, t[v].pooled_delta, t[v].pooled_target) for v in VARIANTS)),
    ]  # fmt: skip
    return {
        "id": "targets",
        "title": "targets",
        "answers": "How many exchanges an even pace would leave today, and how far from that you are.",
        "groups": [group("Exchanges alone, in meals", kit.MEALS, strict), group("Both pots together, in meals", kit.MEALS, pooled)],
        "fine": [
            "The first target looks at exchanges only. The second counts dining dollars as meals, "
            f"at {fmt.cents(cfg.meal_price)} a meal, {rounding_words(cfg)}."
        ],
    }


def pace_block(ctx: PageContext) -> dict:
    cfg, dc, bal, pace = ctx.cfg, ctx.counts, ctx.metrics.balances, ctx.metrics.pace
    days = {"plain": dc.dr, "away": dc.dr_away}
    dd_meals = cfg.round_meals(bal.dd_left / cfg.meal_price)

    def one(v: str, value: float | None, top: str) -> dict:
        return words(OVER) if value is None else cell(f2(value), f"{top} ÷ {days[v]}")

    rows = [
        row("Exchanges only", "Exchanges left ÷ days to go.", *(one(v, pace[v].me_only, n(bal.me_left)) for v in VARIANTS)),
        row("Counting dining dollars", "(Exchanges left + dining dollars as meals) ÷ days to go.",
            *(one(v, pace[v].with_dd, f"({n(bal.me_left)} + {n(dd_meals)})") for v in VARIANTS)),
    ]  # fmt: skip
    return {
        "id": "pace",
        "title": "allowed pace",
        "answers": f"How many meals a day use everything up by {fmt.day(cfg.semester_end)}.",
        "groups": [group("Meals a day", kit.MEALS, rows)],
        "fine": [],
    }


def mix_block(ctx: PageContext) -> dict:
    cfg, dc, bal, mix = ctx.cfg, ctx.counts, ctx.metrics.balances, ctx.metrics.day_mix
    days = {"plain": dc.dr, "away": dc.dr_away}
    pooled = bal.me_left + cfg.round_meals(bal.dd_left / cfg.meal_price)
    groups = []
    for title, key, meals in (("Exchanges only, in days", "me_only", bal.me_left), ("Counting dining dollars, in days", "with_dd", pooled)):
        dm = {v: mix[v][key] for v in VARIANTS}

        def count(v: str, value: float, calc: str) -> dict:
            return words(OVER) if days[v] <= 0 else cell(f1(value), calc)

        def weekly(v: str, value: float | None, count_: float) -> dict:
            return words(OVER) if value is None else cell(f1(value), f"{f1(count_)} × 7 ÷ {days[v]}")

        rows = [
            row("One-meal days", "2 × days to go − meals left, kept between 0 and the days to go.",
                *(count(v, dm[v].one_meal_days, f"2 × {days[v]} − {n(meals)}") for v in VARIANTS)),
            row("Two-meal days", "Days to go − one-meal days.",
                *(count(v, dm[v].two_meal_days, f"{days[v]} − {f1(dm[v].one_meal_days)}") for v in VARIANTS)),
            row("One-meal days a week", "One-meal days × 7 ÷ days to go.",
                *(weekly(v, dm[v].one_per_week, dm[v].one_meal_days) for v in VARIANTS)),
            row("Two-meal days a week", "Two-meal days × 7 ÷ days to go.",
                *(weekly(v, dm[v].two_per_week, dm[v].two_meal_days) for v in VARIANTS)),
        ]  # fmt: skip
        if any(dm[v].note and days[v] > 0 for v in VARIANTS):
            rows.append(row("Does not fit one or two a day", "Said when the meals left are more than two a day or fewer than one.",
                            *(words(mix_note(dm[v].note)) for v in VARIANTS)))  # fmt: skip
        groups.append(group(title, kit.DAYS, rows))
    return {
        "id": "day-mix",
        "title": "day mix",
        "answers": "How many of the days to go can be one-meal days and how many two-meal days, if every meal is used.",
        "groups": groups,
        "fine": ["Meals left are the exchanges left, or the exchanges plus dining dollars as meals."],
    }


def run_out_block(ctx: PageContext) -> dict:
    cfg, dc, bal, out = ctx.cfg, ctx.counts, ctx.metrics.balances, ctx.metrics.run_out
    pooled = bal.me_left + cfg.round_meals(bal.dd_left / cfg.meal_price)

    def one(r: m.RunOut, meals: float) -> dict:
        if r.date is None:
            return words("Fewer than 2 are left, so there is no two-meal day.")
        spare = r.spare_days or 0
        sub = "on the last day" if spare == 0 else f"{fmt.plural(abs(spare), 'day')} {'spare' if spare > 0 else 'short'}"
        return cell(full_day(r.date, cfg), f"{int(meals // 2)} days of two", sub)

    rows = [
        row("Exchanges only", f"Take 2 from the exchanges left on each day after {fmt.day(dc.a)}, while 2 or more remain.",
            *(one(out[v]["me_only"], bal.me_left) for v in VARIANTS)),
        row("Counting dining dollars", "The same, starting from the exchanges plus dining dollars as meals.",
            *(one(out[v]["with_dd"], pooled) for v in VARIANTS)),
    ]  # fmt: skip
    return {
        "id": "run-out",
        "title": "run-out at two meals a day",
        "answers": "The last day you could eat two meals, if you ate two every day from here on.",
        "groups": [group("The last two-meal day", kit.DAYS, rows)],
        "fine": [
            f"Spare and short count days against the last day, {fmt.day(cfg.semester_end)}. "
            "The column that leaves out days away skips them: nothing is eaten on those days."
        ],
    }


def leftover_note(cfg, b: m.BurnResult) -> str:
    """What becomes of dining dollars left at the end, when it is enough to mention."""
    if b.leftover_display is None or b.leftover_display < cfg.dd_leftover_flag:
        return "Too little to mention."
    fate = "carries into spring" if cfg.dd_rollover else "unspent will be lost"
    return f"About {fmt.money(b.leftover_display)} {fate}."


def burn_block(ctx: PageContext) -> dict:
    cfg, dc, bal, burn = ctx.cfg, ctx.counts, ctx.metrics.balances, ctx.metrics.burn
    gone = {"plain": dc.de, "away": dc.de_away}
    days = {"plain": dc.dr, "away": dc.dr_away}
    groups = []
    for title, key, kind, used, left, amount, rate_text in (
        ("Meal exchanges", "me", kit.MEALS, bal.me_used, bal.me_left, f1, f2),
        ("Dining dollars", "dd", kit.DOLLARS, bal.dd_used, bal.dd_left, fmt.cents, fmt.cents),
    ):
        b = {v: burn[v][key] for v in VARIANTS}
        show = n if key == "me" else fmt.cents

        def rate(v: str) -> dict:
            if b[v].rate is None:
                return words(NO_RATE)
            return cell(rate_text(b[v].rate), f"{show(used)} ÷ {gone[v]}", "a day")

        def runs_out(v: str) -> dict:
            if b[v].rate is None:
                return words(NO_RATE)
            if b[v].runout_date is None:
                return words("Never. None is being used.")
            when = b[v].runout_date
            sub = f"after {fmt.day(cfg.semester_end)}" if b[v].lasts_past_end else "before the end"
            return cell(f"{fmt.day(when)}, {when.year}", None, sub)

        def spare(v: str) -> dict:
            d = b[v].spare_days
            if b[v].rate is None:
                return words(NO_RATE)
            if d is None:
                return words("Never runs out.")
            calc = f"{show(left)} ÷ {rate_text(b[v].rate)} − {days[v]}"
            if abs(d) < 0.05:
                return cell("0.0", calc, "exactly at the end")
            return cell(f1(abs(d)), calc, "days spare" if d > 0 else "days short")

        def left_over(v: str) -> dict:
            if b[v].rate is None:
                return words(NO_RATE)
            sub = f"short {amount(b[v].shortfall)}" if b[v].shortfall else None
            return cell(amount(b[v].leftover_display), f"{show(left)} − {rate_text(b[v].rate)} × {days[v]}", sub)

        rows = [
            row("Rate so far", "Used ÷ days gone by.", *(rate(v) for v in VARIANTS)),
            row("Runs out", "Take the rate from what is left on each day to come, until nothing remains.", *(runs_out(v) for v in VARIANTS)),
            row("Days spare or short", "What is left ÷ rate − days to go.", *(spare(v) for v in VARIANTS)),
            row("Left on the last day", "What is left − rate × days to go. Never shown below zero.", *(left_over(v) for v in VARIANTS)),
        ]  # fmt: skip
        if key == "dd" and any(b[v].leftover_note for v in VARIANTS):
            rows.append(row("What happens to it", f"Said when {fmt.money(cfg.dd_leftover_flag)} or more would be left.",
                            *(words(leftover_note(cfg, b[v])) for v in VARIANTS)))  # fmt: skip
        groups.append(group(title, kind, rows))
    return {
        "id": "burn",
        "title": "burn rate and projection",
        "answers": "Where each pot ends up if you keep using it at your own pace so far.",
        "groups": groups,
        "fine": ["The rate is a plain average over the days gone by. The forecast page weighs recent days more, so its figures differ."],
    }


# --- the top of the page --------------------------------------------------------------------------


def headline(ctx: PageContext) -> tuple[str, str]:
    cfg, dc, pace, v = ctx.cfg, ctx.counts, ctx.metrics.pace, ctx.variant
    end = fmt.day(cfg.semester_end)
    other = "plain" if v == "away" else "away"
    use = f"The app uses the column marked in use: {COLUMN[v].lower()}."
    if pace[v].me_only is None:
        return "The semester is over. There is nothing left to pace.", "The blocks below still show how each figure ended up."
    if ctx.metrics.balances.me_left <= 0:
        return "The meal exchanges are all used up.", f"Every block below shows its result two ways. {use}"
    now = f2(pace[v].me_only)
    if dc.away_tot == 0:
        return (
            f"You can eat {now} meals a day and use every exchange by {end}.",
            "No days away are set, so both columns below say the same thing.",
        )
    away_days = fmt.plural(dc.away_tot, "day")
    if v == "away":
        lead = f"Leaving out {away_days} away, you can eat {now} meals a day and use every exchange by {end}."
        sub = f"Counting every day it is {f2(pace[other].me_only)}." if pace[other].me_only is not None else ""
    else:
        lead = f"Counting every day, you can eat {now} meals a day and use every exchange by {end}."
        sub = f"Leaving out {away_days} away it is {f2(pace[other].me_only)}." if pace[other].me_only is not None else ""
    return lead, f"{sub} Every block below shows its result both ways. {use}".strip()


def notes(ctx: PageContext) -> list[dict]:
    cfg, bal = ctx.cfg, ctx.metrics.balances
    data = {"href": ctx.url("/data"), "link": "Go to data"}
    out: list[dict] = []
    if ctx.now.notice:
        out.append({"kind": kit.LOOK, "text": ctx.now.notice})
    if ctx.stale:
        out.append({
            "kind": kit.PROBLEM,
            "text": f"Your data is {ctx.behind} days old. It ends {fmt.day(ctx.counts.a)}. This page describes that day, not today.",
            **data,
        })  # fmt: skip
    elif ctx.behind:
        out.append({"kind": kit.DAYS, "text": f"These figures are for {fmt.day(ctx.counts.a)}, the last day in your data."})
    for pot, warning in (("Meal exchanges", bal.me_warning), ("Dining dollars", bal.dd_warning)):
        if warning:
            out.append({"kind": kit.LOOK, "text": f"{pot} do not add up: {warning}", **data})
    if cfg.dd_rollover:
        out.append({
            "kind": kit.DOLLARS,
            "text": "Your dining dollars carry into spring. The rows that count them as meals are here for checking only.",
        })  # fmt: skip
    return out


def band(ctx: PageContext) -> list[dict]:
    cfg, dc, metrics, v = ctx.cfg, ctx.counts, ctx.metrics, ctx.variant
    end = fmt.day(cfg.semester_end)
    stats: list[dict] = []
    delta = metrics.targets[v].strict_delta
    if delta is not None:
        if delta >= 0:
            unused = fcast.going_unused(cfg, ctx.forecast.me)  # the same condition the now page colours by
            detail, kind = "More exchanges left than even use would leave.", kit.LOOK if unused and delta > 0 else kit.GOOD
        else:
            detail, kind = "Fewer exchanges left than even use would leave.", kit.PROBLEM
        stats.append({"value": fmt.signed(delta), "label": "against an even pace", "detail": detail, "kind": kind})
    pace = metrics.pace[v]
    if pace.me_only is not None:
        detail = None if cfg.dd_rollover or pace.with_dd is None else f"{f2(pace.with_dd)} counting dining dollars."
        stats.append({"value": f2(pace.me_only), "label": "meals a day allowed", "detail": detail, "kind": kit.MEALS})
    me, dd = metrics.burn[v]["me"], metrics.burn[v]["dd"]
    if me.rate is not None:
        if me.lasts_past_end:
            detail = f"Leaves {fmt.num(me.leftover_display, 0)} on {end}."
        else:
            detail = f"Runs out {fmt.day(me.runout_date)}."
        stats.append({"value": f2(me.rate), "label": "exchanges a day so far", "detail": detail, "kind": kit.MEALS})
    if dd.rate is not None:
        if dd.lasts_past_end:
            detail = f"Leaves {fmt.money(dd.leftover_display)} on {end}."
        else:
            detail = f"Runs out {fmt.day(dd.runout_date)}."
        stats.append({"value": fmt.cents(dd.rate), "label": "dining dollars a day so far", "detail": detail, "kind": kit.DOLLARS})
    days = dc.dr_away if v == "away" else dc.dr
    if v == "away" and dc.away_rem:
        label, detail = "usable days to go", f"After {fmt.day(dc.a)}. {dc.dr} counting {fmt.plural(dc.away_rem, 'day')} away."
    else:
        label, detail = "days to go", f"After {fmt.day(dc.a)}, through {end}."
    stats.append({"value": str(days), "label": label, "detail": detail, "kind": kit.DAYS})
    return stats


def build(ctx: PageContext, request) -> dict:
    if not ctx.has_data:
        return {"empty": True}
    cfg, v = ctx.cfg, ctx.variant
    working = request.query_params.get("working") != "0"
    keep = None if working else "0"
    title, sub = headline(ctx)
    pace, me = ctx.metrics.pace[v], ctx.metrics.burn[v]["me"]
    return {
        "empty": False,
        "headline": title,
        "sub": sub,
        "notes": notes(ctx),
        "band": band(ctx),
        "working": working,
        "columns": [{"key": k, "label": COLUMN[k], "on": k == v} for k in VARIANTS],
        "away_options": [
            {"label": "Left out", "href": ctx.url("/math", away="1", working=keep), "on": cfg.exclude_away},
            {"label": "Counted", "href": ctx.url("/math", away="0", working=keep), "on": not cfg.exclude_away},
        ],
        "working_options": [
            {"label": "Shown", "href": ctx.url("/math", working=None), "on": working},
            {"label": "Hidden", "href": ctx.url("/math", working="0"), "on": not working},
        ],
        "pace_chart": charts.pace_chart(me.rate, pace.me_only, pace.with_dd, COLUMN[v].lower()),
        "pace_line": (
            f"You use {f2(me.rate)} exchanges a day. The plan allows {f2(pace.me_only)}."
            if me.rate is not None and pace.me_only is not None
            else None
        ),
        "blocks": [days_block(ctx), targets_block(ctx), pace_block(ctx), mix_block(ctx), run_out_block(ctx), burn_block(ctx)],
    }
