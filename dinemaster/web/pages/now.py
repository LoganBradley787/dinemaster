"""The `now` section: where both pots stand today, and how they have been used so far.

`build` returns the template's view model. It is the reference for the other sections: one
headline sentence, short notes, a band of big numbers, then charts, then tables.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from ... import forecast as fcast
from ... import metrics as m
from .. import fmt
from ..charts import kit
from ..charts import now as charts
from ..context import PageContext

def headline(ctx: PageContext) -> tuple[str, str]:
    """The verdict in one sentence, and a second, quieter sentence with the figures behind it."""
    cfg, bal, f = ctx.cfg, ctx.metrics.balances, ctx.forecast.me
    end = fmt.day(cfg.semester_end)
    days_left = ctx.now.dr_away if cfg.exclude_away else ctx.now.dr
    usable = "usable " if cfg.exclude_away and ctx.now.away_rem else ""
    left = (
        f"{fmt.plural(bal.me_left, 'exchange')} and {fmt.cents(bal.dd_left)} in dining dollars are left, "
        f"with {days_left} {usable}{'day' if days_left == 1 else 'days'} to go."
    )
    if bal.me_used <= 0 and bal.dd_used <= 0:
        return "Nothing used yet.", left
    if not f.available:
        return "No forecast yet.", left
    if len(f.expected) <= 1:
        return f"The semester is over. {fmt.plural(max(f.start_balance, 0), 'meal exchange')} went unused.", left
    if f.start_balance <= 0:
        return "The meal exchanges are all used up.", left
    spare = fmt.num(f.leftover_p50, 0)
    if fcast.going_unused(cfg, f):
        return f"You are on pace to leave {spare} meal exchanges unused on {end}.", left
    if f.prob_lasts >= fcast.SAFE_CHANCE:
        return f"You are on track. Meal exchanges should last to {end} with about {spare} to spare.", left
    if f.prob_lasts >= 0.5 or f.runout_p50 is None:
        return f"Meal exchanges will probably last to {end}, but it is close.", left
    early = (cfg.semester_end - f.runout_p50).days
    return f"Meal exchanges are on pace to run out around {fmt.day(f.runout_p50)}, {fmt.plural(early, 'day')} early.", left


def notes(ctx: PageContext) -> list[dict]:
    """Things to know before trusting the numbers. kind: PROBLEM or LOOK when it warns, else what the note is about."""
    cfg, bal, fc = ctx.cfg, ctx.metrics.balances, ctx.forecast
    end = fmt.day(cfg.semester_end)
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
    out += [{"kind": kit.LOOK, "text": f"Reading the files: {w}", **data} for w in ctx.report.warnings]
    jumps = [b for b in ctx.report.chain_breaks if b["timestamp"].date() >= cfg.data_cutoff]
    if jumps:
        times = "once" if len(jumps) == 1 else f"{len(jumps)} times"
        out.append({"kind": kit.LOOK, "text": f"The dining site changed a balance {times} this semester with no transaction to explain it.", **data})
    if fcast.going_unused(cfg, fc.me) and cfg.exchange_price:
        worth = round(fc.me.leftover_p50) * cfg.exchange_price
        out.append({
            "kind": kit.LOOK,
            "text": f"The unused exchanges are worth about {fmt.money(worth)} of the plan.",
            "href": ctx.url("/plan"), "link": "See how to use them",
        })  # fmt: skip
    if fc.dd.available and fc.dd.leftover_p50 is not None and fc.dd.leftover_p50 >= cfg.dd_leftover_flag:
        fate = "It carries into spring." if cfg.dd_rollover else "It will be lost."
        out.append({"kind": kit.DOLLARS, "text": f"About {fmt.money(fc.dd.leftover_p50)} in dining dollars is likely to be left on {end}. {fate}"})
    return out


def band(ctx: PageContext) -> list[dict]:
    """The big numbers under the headline."""
    cfg, bal, fc = ctx.cfg, ctx.metrics.balances, ctx.forecast
    targets = ctx.metrics.targets[ctx.variant]
    end = fmt.day(cfg.semester_end)
    days_left = ctx.now.dr_away if cfg.exclude_away else ctx.now.dr
    usable = "usable " if cfg.exclude_away and ctx.now.away_rem else ""
    if cfg.dd_rollover:
        dd_detail = "They carry into spring."
    else:
        dd_detail = f"About {fmt.plural(cfg.round_meals(bal.dd_left / cfg.meal_price), 'meal')}."
    stats = [
        {"value": fmt.num(bal.me_left, 0), "label": "exchanges left", "detail": f"Of {fmt.num(cfg.starting_me, 0)}.", "kind": kit.MEALS},
        {"value": fmt.money(bal.dd_left), "label": "dining dollars left", "detail": dd_detail, "kind": kit.DOLLARS},
        {
            "value": fmt.pct(ctx.now.progress),
            "label": "of the semester done",
            "detail": f"{days_left} {usable}{'day' if days_left == 1 else 'days'} to go.",
            "kind": kit.DAYS,
        },
    ]
    delta = targets.strict_delta
    unused = fcast.going_unused(cfg, fc.me)
    if delta is not None:
        if delta >= 0:
            detail = "More exchanges left than even use would leave."
            kind = kit.LOOK if unused and delta > 0 else kit.GOOD
        else:
            detail, kind = "Fewer exchanges left than even use would leave.", kit.PROBLEM
        stats.append({"value": fmt.signed(delta), "label": "against an even pace", "detail": detail, "kind": kind})
    f = fc.me
    if f.available and f.leftover_p50 is not None:
        likely = fmt.between(f.leftover_lo, f.leftover_hi)
        odds = f"{fmt.chance(1 - f.prob_lasts)} chance you use them all." if unused else f"{fmt.chance(f.prob_lasts)} chance they last."
        kind = kit.GOOD if f.prob_lasts >= fcast.SAFE_CHANCE else kit.LOOK if f.prob_lasts >= 0.5 else kit.PROBLEM
        kind = kit.LOOK if unused else kind
        stats.append({"value": fmt.num(f.leftover_p50, 0), "label": f"unused on {end}", "detail": f"Likely {likely}. {odds}", "kind": kind})
    elif not cfg.dd_rollover and targets.pooled_delta is not None:
        ahead = targets.pooled_delta >= 0
        stats.append({
            "value": fmt.signed(targets.pooled_delta),
            "label": "meals ahead" if ahead else "meals behind",
            "detail": "Counting dining dollars as meals.",
            "kind": kit.GOOD if ahead else kit.PROBLEM,
        })  # fmt: skip
    return stats


def _pace_sentence(burn: m.BurnResult, cfg, rate, amount, each: str) -> str:
    """One pot's pace so far and where it leads, in a sentence."""
    end = fmt.day(cfg.semester_end)
    if burn.rate is None:
        return "Not enough data yet."
    if burn.rate <= 0:
        return "None used yet."
    lead = f"You use {rate(burn.rate)} {each}."
    if burn.lasts_past_end:
        return f"{lead} At that pace you reach {end} with {amount(burn.leftover_display)} left."
    return f"{lead} At that pace this pot is empty on {fmt.day(burn.runout_date)}."


def _by_week(ctx: PageContext) -> dict:
    daily = ctx.daily
    rows = []
    for monday, counts in reversed(charts.weekly_buckets(daily)):
        week = daily[(daily.index >= monday) & (daily.index < monday + timedelta(days=7))]
        rows.append([
            f"Week of {fmt.day(monday)}",
            fmt.num(week["meals"].sum(), 0),
            fmt.num(week["me_swipes"].sum(), 0),
            fmt.num(week["dd_meals"].sum(), 0),
            fmt.cents(week["dd_spend"].sum()),
            str(counts["0"]),
            str(len(week)),
        ])  # fmt: skip
    return {
        "columns": [("Week", False), ("Meals", True), ("Exchange swipes", True), ("Dining-dollar meals", True), ("Dining dollars spent", True),
                    ("Days with no meal", True), ("Days counted", True)],
        "rows": rows,
    }  # fmt: skip


def _places(me_counts: pd.Series, by_location: dict) -> dict:
    names = sorted(set(me_counts.index) | set(by_location), key=lambda p: -(float(me_counts.get(p, 0)) + by_location.get(p, {}).get("meal_count", 0)))
    rows = []
    for place in names:
        v = by_location.get(place, {})
        spent = v.get("meal_amount", 0.0) + v.get("snack_amount", 0.0)
        rows.append([
            str(place),
            fmt.num(float(me_counts.get(place, 0)), 0),
            str(v.get("meal_count", 0)),
            str(v.get("snack_count", 0)),
            fmt.cents(spent) if spent else "",
        ])  # fmt: skip
    return {"columns": [("Place", False), ("Exchange swipes", True), ("Dining-dollar meals", True), ("Snacks", True), ("Dining dollars", True)], "rows": rows}


def build(ctx: PageContext, request) -> dict:
    if not ctx.has_data:
        return {"empty": True}
    cfg, metrics, daily = ctx.cfg, ctx.metrics, ctx.daily
    bal, a, v = metrics.balances, ctx.counts.a, ctx.variant
    away = cfg.exclude_away
    end = fmt.day(cfg.semester_end)
    today_label = "today" if ctx.behind == 0 and cfg.as_of == ctx.today else fmt.day(a)
    title, sub = headline(ctx)

    # meal exchanges
    me_burn = metrics.burn[v]["me"]
    me_proj = m.projection_series(cfg, a, bal.me_left, me_burn.rate, away) if me_burn.rate else None
    me_chart = charts.balance_chart(
        cfg, a, daily["me_bal"], m.ideal_series(cfg, cfg.starting_me, away), me_proj, kit.MEALS,
        "Meal exchanges left, day by day, against an even pace", today_label=today_label,
    )  # fmt: skip

    # dining dollars, in dollars or as meals
    in_meals = request.query_params.get("unit") == "meals" and cfg.meal_price > 0
    per = cfg.meal_price if in_meals else 1.0
    dd_burn = metrics.burn[v]["dd"]
    dd_proj = m.projection_series(cfg, a, bal.dd_left / per, dd_burn.rate / per, away) if dd_burn.rate else None
    dd_chart = charts.balance_chart(
        cfg, a, daily["dd_bal"] / per, m.ideal_series(cfg, cfg.starting_dd / per, away), dd_proj, kit.DOLLARS,
        "Dining dollars left, day by day, against an even pace",
        fmt=(lambda x: fmt.num(x, 1)) if in_meals else fmt.money, today_label=today_label,
    )  # fmt: skip
    units = [
        {"label": "Dollars", "href": ctx.url("/now#dining-dollars", unit=None), "on": not in_meals},
        {"label": "Meals", "href": ctx.url("/now#dining-dollars", unit="meals"), "on": in_meals},
    ]

    # both pots as meals (only when dining dollars do not carry over)
    pooled = None
    if not cfg.dd_rollover and cfg.meal_price > 0:
        total = metrics.targets[v].pooled_total
        ideal = m.ideal_series(cfg, total, away) if total is not None else pd.Series(dtype=float)
        pooled = charts.balance_chart(
            cfg, a, daily["me_bal"] + daily["dd_bal"] / cfg.meal_price, ideal, None, kit.MEALS,  # both pots counted as meals: the number is meals
            "Meals left counting both pots, day by day", fmt=lambda x: fmt.num(x, 0), today_label=today_label,
        )  # fmt: skip

    # usage
    pace = metrics.pace[v]
    mix = metrics.day_mix[v]["me_only" if cfg.dd_rollover else "with_dd"]
    mix_line = None
    if mix.two_per_week is not None and mix.one_per_week is not None:
        mix_line = (
            f"From here, a week that uses everything up has about {fmt.num(mix.two_per_week)} two-meal days "
            f"and {fmt.num(mix.one_per_week)} one-meal days."
        )
    elif mix.note:
        mix_line = mix.note
    pace_line = None
    if pace.me_only is not None:
        pace_line = f"To use every exchange by {end} you can eat {fmt.num(pace.me_only, 2)} meals a day"
        pace_line += "." if cfg.dd_rollover or pace.with_dd is None else f", or {fmt.num(pace.with_dd, 2)} counting dining dollars."

    rows = ctx.df[
        (ctx.df["pot"] == "ME") & ctx.df["kind"].isin(["usage", "reversal"]) & (ctx.df["date"] >= cfg.data_cutoff) & (ctx.df["date"] <= a)
    ]  # net of refunds: a refunded swipe is not a visit
    me_counts = -rows.groupby("description")["amount"].sum()
    me_counts = me_counts[me_counts > 0]
    bd = metrics.dd_breakdown

    return {
        "empty": False,
        "headline": title,
        "sub": sub,
        "notes": notes(ctx),
        "band": band(ctx),
        "me": {"chart": me_chart, "lead": _pace_sentence(me_burn, cfg, lambda x: fmt.num(x, 2), lambda x: fmt.num(x, 0), "exchanges a day")},
        "dd": {"chart": dd_chart, "lead": _pace_sentence(dd_burn, cfg, fmt.cents, fmt.cents, "a day"), "units": units, "in_meals": in_meals},
        "pooled": pooled,
        "daily_chart": charts.daily_meals_chart(cfg, daily, pace.me_only, None if cfg.dd_rollover else pace.with_dd),
        "pace_line": pace_line,
        "mix_chart": charts.weekly_mix_chart(daily),
        "mix_line": mix_line,
        "me_places": charts.me_places_chart(me_counts),
        "dd_places": charts.dd_places_chart(bd.by_location),
        "dd_split": (
            f"{fmt.cents(bd.meal_amount)} went on {fmt.plural(bd.meal_count, 'meal')} and "
            f"{fmt.cents(bd.snack_amount)} on {fmt.plural(bd.snack_count, 'snack')}."
            if bd.meal_count or bd.snack_count else None
        ),  # fmt: skip
        "weeks": _by_week(ctx),
        "places": _places(me_counts, bd.by_location),
    }
