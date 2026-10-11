"""The `forecast` section: where each pot is headed, and the week the forecast is built on.

One headline about the meal exchanges, the few numbers behind it, a chart for each pot (what the
data shows, then the expected line and the likely range), and the day-of-week view last.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from ... import forecast as fcast
from .. import fmt
from ..charts import forecast as charts
from ..charts import kit
from ..context import PageContext


def _sentence(reason: str) -> str:
    """A reason from the forecast module as a sentence of its own."""
    text = fmt.sentences(reason.strip())
    return text[:1].upper() + text[1:].rstrip(".") + "."


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def headline(ctx: PageContext) -> tuple[str, str | None]:
    """Where the meal exchanges are headed in one sentence, and the dining dollars in a quieter one."""
    cfg, fc = ctx.cfg, ctx.forecast
    f, d = fc.me, fc.dd
    end = fmt.day(cfg.semester_end)
    if not f.available:
        return "No forecast yet.", _sentence(f.reason or fc.profile.attrs.get("reason") or "no observed days yet")
    if len(f.expected) <= 1:
        left = f"{fmt.plural(max(f.start_balance, 0), 'meal exchange')} and {fmt.cents(max(d.start_balance, 0))} in dining dollars were left."
        return "The semester is over. Nothing is left to forecast.", left
    if f.start_balance <= 0:
        title = "The meal exchanges are all used up."
    elif fcast.going_unused(cfg, f):
        title = f"The forecast leaves about {fmt.num(f.leftover_p50, 0)} meal exchanges unused on {end}."
    elif f.prob_lasts >= fcast.SAFE_CHANCE:
        title = f"Meal exchanges should last to {end}, with about {fmt.num(f.leftover_p50, 0)} to spare."
    elif f.prob_lasts >= 0.5 or f.runout_p50 is None:
        title = f"Meal exchanges will probably last to {end}, but it is close."
    else:
        title = f"Meal exchanges are likely to run out around {fmt.day(f.runout_p50)}."
    if not d.available:
        sub = None
    elif d.start_balance <= 0:
        sub = "The dining dollars are all used up."
    elif d.prob_lasts >= fcast.SAFE_CHANCE:
        sub = f"Dining dollars should last too, with about {fmt.money(d.leftover_p50)} left on {end}."
        if title.startswith(("Meal exchanges are likely", "The meal exchanges are all")):
            sub = f"Dining dollars should last, with about {fmt.money(d.leftover_p50)} left on {end}."
    elif d.prob_lasts >= 0.5 or d.runout_p50 is None:
        sub = f"Dining dollars will probably last to {end}, but it is close."
    else:
        sub = f"Dining dollars are likely to run out around {fmt.day(d.runout_p50)}."
    return title, sub


def notes(ctx: PageContext) -> list[dict]:
    """What to know before trusting the forecast."""
    cfg, fc = ctx.cfg, ctx.forecast
    end = fmt.day(cfg.semester_end)
    data = {"href": ctx.url("/data"), "link": "Go to data"}
    out: list[dict] = []
    if ctx.now.notice:
        out.append({"kind": kit.LOOK, "text": ctx.now.notice})
    if ctx.stale:
        out.append({
            "kind": kit.PROBLEM,
            "text": f"Your data is {ctx.behind} days old. It ends {fmt.day(fc.observed_through)}. Every day after that is forecast, not known.",
            **data,
        })  # fmt: skip
    elif ctx.behind:
        out.append({"kind": kit.DAYS, "text": f"The data ends {fmt.day(fc.observed_through)}. Every day after that is forecast."})
    if fcast.going_unused(cfg, fc.me):
        worth = f" They are worth about {fmt.money(round(fc.me.leftover_p50) * cfg.exchange_price)} of the plan." if cfg.exchange_price else ""
        out.append({
            "kind": kit.LOOK,
            "text": f"Running out is not the risk. The exchanges left on {end} expire unused.{worth}",
            "href": ctx.url("/plan"), "link": "See how to use them",
        })  # fmt: skip
    if fc.dd.available and len(fc.dd.expected) > 1 and fc.dd.leftover_p50 is not None and fc.dd.leftover_p50 >= cfg.dd_leftover_flag:
        fate = "It carries into spring." if cfg.dd_rollover else "It will be lost."
        out.append({"kind": kit.DOLLARS, "text": f"About {fmt.money(fc.dd.leftover_p50)} in dining dollars is likely to be left on {end}. {fate}"})
    return out


def _verdict(prob: float) -> kit.Tone:
    return kit.GOOD if prob >= fcast.SAFE_CHANCE else kit.LOOK if prob >= 0.5 else kit.PROBLEM


def _runout(pf: fcast.PotForecast) -> str:
    if pf.runout_p50 is None:
        return "Running out early is unlikely."
    return f"If they run out, most often around {fmt.day(pf.runout_p50)}."


def band(ctx: PageContext, heavy: dict | None) -> list[dict]:
    """The big numbers: for each pot the chance it lasts and what is likely left, then the heaviest day."""
    cfg, fc = ctx.cfg, ctx.forecast
    end = fmt.day(cfg.semester_end)
    stats: list[dict] = []
    f = fc.me
    if f.available and len(f.expected) > 1:
        unused = fcast.going_unused(cfg, f)
        stats.append({"value": fmt.chance(f.prob_lasts), "label": "chance exchanges last", "detail": _runout(f), "kind": _verdict(f.prob_lasts)})
        stats.append({
            "value": fmt.num(f.leftover_p50, 0),
            "label": f"exchanges {'unused' if unused else 'left'} on {end}",
            "detail": f"Likely {fmt.between(f.leftover_lo, f.leftover_hi)}." + (f" {fmt.chance(1 - f.prob_lasts)} chance you use them all." if unused else ""),
            "kind": kit.LOOK if unused else kit.MEALS,
        })  # fmt: skip
    d = fc.dd
    if d.available and len(d.expected) > 1:
        stats.append({"value": fmt.chance(d.prob_lasts), "label": "chance dining dollars last", "detail": _runout(d), "kind": _verdict(d.prob_lasts)})
        stats.append({
            "value": fmt.money(d.leftover_p50),
            "label": f"dining dollars left on {end}",
            "detail": f"Likely {fmt.between(d.leftover_lo, d.leftover_hi, fmt.money)}.",
            "kind": kit.DOLLARS,
        })  # fmt: skip
    if heavy:
        stats.append({"value": heavy["short"], "label": "your heaviest day" if heavy["one"] else "your heaviest days", "detail": heavy["detail"], "kind": kit.DAYS})
    return stats


def week_pattern(profile: pd.DataFrame) -> tuple[str | None, dict | None]:
    """The heaviest and lightest weekdays as a sentence, and the same as a band item."""
    if profile.attrs.get("reason"):
        return None, None
    rates = profile.set_index("weekday")["meals_rate"]
    if rates.sum() <= 0 or rates.max() - rates.min() <= 0.01:
        return None, None
    heavy = [name for name, r in rates.items() if r >= rates.max() - 0.005]
    light = [name for name, r in rates.items() if r <= rates.min() + 0.005]
    line = (
        f"{_join(heavy)} {'is' if len(heavy) == 1 else 'are'} your heaviest, at about {rates.max():.2f} meals a day. "
        f"{_join(light)} {'is' if len(light) == 1 else 'are'} the lightest, at {rates.min():.2f}. "
        "The forecast and the daily plan both follow this pattern."
    )
    item = {
        "short": _join([name[:3] for name in heavy]),
        "one": len(heavy) == 1,
        "detail": f"About {rates.max():.2f} meals. {_join([name[:3] for name in light])} {'is' if len(light) == 1 else 'are'} lightest, at {rates.min():.2f}.",
    }
    return line, item if len(heavy) <= 2 else None


def _week_table(profile: pd.DataFrame) -> dict:
    two = lambda x: f"{float(x):.2f}"  # noqa: E731
    rows = []
    for _, r in profile.iterrows():
        seen = int(r["days"]) > 0
        rows.append([
            r["weekday"],
            str(int(r["days"])),
            two(r["meals_avg"]) if seen else "",
            two(r["me_avg"]) if seen else "",
            two(r["dd_meals_avg"]) if seen else "",
            fmt.cents(float(r["dd_spend_avg"])) if seen else "",
            fmt.cents(float(r["dd_snack_avg"])) if seen else "",
            two(r["meals_rate"]),
            fmt.cents(float(r["dd_spend_rate"])),
            fmt.pct(float(r["share"])),
        ])  # fmt: skip
    return {
        "columns": [
            ("Day", False), ("Days observed", True), ("Meals a day", True), ("Exchange swipes a day", True), ("Dining-dollar meals a day", True),
            ("Dining dollars a day", True), ("Snacks a day", True), ("Forecast meals a day", True), ("Forecast dining dollars a day", True),
            ("Share of week", True),
        ],
        "rows": rows,
    }  # fmt: skip


def _pot(ctx: PageContext, pf: fcast.PotForecast, noun: str, amount, each: str = "") -> dict:
    """The sentences that go with one pot's chart: every figure the chart shows, in words."""
    end = fmt.day(ctx.cfg.semester_end)
    if not pf.available:
        return {"lead": f"{noun}: no forecast. {_sentence(pf.reason or 'no observed days yet')}", "facts": [], "caveat": None}
    if len(pf.expected) <= 1:
        return {"lead": _sentence(pf.reason or "the semester is over"), "facts": [], "caveat": None}
    low, high = pf.band
    reaches, middle = amount(float(pf.expected.iloc[-1])), amount(pf.leftover_p50)
    # the line adds up each day's forecast rate; the headline and the band quote the middle simulated semester
    also = "" if reaches == middle else f" The middle simulated semester ends at {middle}{each}."
    lead = (
        f"The expected line reaches {end} at {reaches}{each}.{also} "
        f"The middle {high - low:g}% of simulated semesters end between {amount(pf.leftover_lo)} and {amount(pf.leftover_hi)}."
    )
    facts = [
        {"term": f"Chance {noun.lower()} last", "value": fmt.chance(pf.prob_lasts), "detail": f"The share of simulated semesters with some left after {end}."},
        {
            "term": f"Likely left on {end}",
            "value": fmt.between(pf.leftover_lo, pf.leftover_hi, amount),
            "detail": f"Middle estimate {amount(pf.leftover_p50)}{each}.",
        },
    ]
    if pf.runout_p50 is not None:
        facts.append({
            "term": "If they run out, likely",
            "value": fmt.span(pf.runout_lo, pf.runout_hi),
            "detail": f"Most often around {fmt.day(pf.runout_p50)}.",
        })  # fmt: skip
    else:
        facts.append({"term": f"Running out before {end}", "value": "Unlikely", "detail": "Under 1 in 20 simulations."})
    return {"lead": lead, "facts": facts, "caveat": _sentence(pf.reason) if pf.reason else None}


def build(ctx: PageContext, request) -> dict:
    if not ctx.has_data:
        return {"empty": True}
    cfg, fc, daily = ctx.cfg, ctx.forecast, ctx.daily
    profile, o = fc.profile, fc.observed_through
    seen = daily[daily.index <= o]
    title, sub = headline(ctx)
    week_line, heavy = week_pattern(profile)
    observed_label = "today" if ctx.behind == 0 and cfg.as_of == ctx.today else f"data ends {fmt.day(o)}"

    # dining dollars, in dollars or as meals
    in_meals = request.query_params.get("unit") == "meals" and cfg.meal_price > 0
    per = cfg.meal_price if in_meals else 1.0
    dd_amount = (lambda x: fmt.num(x / per, 1)) if in_meals else fmt.money
    units = [
        {"label": "Dollars", "href": ctx.url("/forecast#dining-dollars", unit=None), "on": not in_meals},
        {"label": "Meals", "href": ctx.url("/forecast#dining-dollars", unit="meals"), "on": in_meals},
    ]

    me = _pot(ctx, fc.me, "Meal exchanges", lambda x: fmt.num(x, 0))
    me["chart"] = charts.pot_chart(
        cfg, fc.me, seen["me_bal"], kit.MEALS, "Meal exchanges left so far, then the forecast to the semester end", observed_label=observed_label
    )
    dd = _pot(ctx, fc.dd, "Dining dollars", dd_amount, " meals" if in_meals else "")
    dd["chart"] = charts.pot_chart(
        cfg, fc.dd, seen["dd_bal"], kit.DOLLARS, "Dining dollars left so far, then the forecast to the semester end",
        fmt=(lambda x: fmt.num(x, 1)) if in_meals else fmt.money, per=per, observed_label=observed_label,
    )  # fmt: skip

    how = None
    if o < cfg.semester_end and (fc.me.available or fc.dd.available):
        low, high = fc.band
        how = (
            f"Solid line: what the data shows, through {fmt.day(o)}. Dashed line and band: the forecast for "
            f"{fmt.span(o + timedelta(days=1), cfg.semester_end)}, where each future day behaves like past days of the same weekday. "
            f"The band is the middle {high - low:g}% of {fc.simulations:,} simulated semesters."
        )

    no_history = profile.attrs.get("reason")
    week_fine = None
    if not no_history:
        away_note = " Away days are left out." if cfg.exclude_away else ""
        days = int(profile.attrs.get("observed_days", 0))
        week_fine = (
            f"Observed: {fmt.span(max(cfg.semester_start, cfg.data_cutoff), o)} ({fmt.plural(days, 'day')}).{away_note} "
            "The plain averages are over those days. The forecast columns weigh recent weeks more "
            f"(a day {cfg.forecast.half_life_days:g} days old counts half) and pull a weekday with few observations toward your overall average."
        )

    return {
        "empty": False,
        "headline": title,
        "sub": sub,
        "notes": notes(ctx),
        "band": band(ctx, heavy),
        "how": how,
        "me": me,
        "dd": {**dd, "units": units if cfg.meal_price > 0 else None, "in_meals": in_meals},
        "week": {
            "none": _sentence(f"no forecast yet: {no_history}") if no_history else None,
            "line": week_line,
            "meals": charts.week_meals_chart(profile),
            "dollars": charts.week_dollars_chart(profile),
            "table": None if no_history else _week_table(profile),
            "fine": week_fine,
        },
    }
