"""The `habits` section: when and where meals happen, streaks, late nights, and a week-by-week recap.

Everything is worked out by `dinemaster.habits` and `dinemaster.recap`; this module words it.
The picked recap week travels as `?week=YYYY-MM-DD` (a Monday); without it the latest week shows.
"""

from __future__ import annotations

from datetime import date, timedelta

from ... import habits as h
from ... import recap as rc
from .. import fmt
from ..charts import habits as charts
from ..charts import kit
from ..context import PageContext


def dates(start: date, end: date) -> str:
    """"Sep 7" or "Sep 7 to Sep 13"."""
    return fmt.day(start) if start == end else f"{fmt.day(start)} to {fmt.day(end)}"


def window(ln: h.LateNight) -> str:
    """The late-night hours in words: "9 PM to 4 AM"."""
    return f"{h.format_hour(ln.start_hour)} to {h.format_hour(ln.end_hour)}"


def notes(ctx: PageContext) -> list[dict]:
    """How fresh the habits are."""
    if ctx.stale:
        return [{
            "kind": kit.PROBLEM,
            "text": f"Your data is {ctx.behind} days old. It ends {fmt.day(ctx.counts.a)}. These habits stop on that day.",
            "href": ctx.url("/data"), "link": "Go to data",
        }]  # fmt: skip
    if ctx.behind:
        return [{"kind": kit.DAYS, "text": f"These habits run through {fmt.day(ctx.counts.a)}, the last day in your data."}]
    return []


def streak_band(sk: h.Streaks) -> list[dict]:
    """The four runs as big numbers. A run's length is a number of days."""

    def item(label: str, run: h.Run, fallback: str) -> dict:
        if run.length:
            detail = f"{run.place}, {dates(run.start, run.end)}." if run.place else f"{dates(run.start, run.end)}."
        else:
            detail = fallback
        noun = "day" if run.length == 1 else "days"
        return {"value": str(run.length), "label": f"{noun} in your {label}", "detail": detail, "kind": kit.DAYS}

    return [
        item("current meal streak", sk.current, f"No meal on {fmt.day(sk.observed_through)}."),
        item("longest meal streak", sk.meal_streak, "None yet."),
        item("longest gap without a meal", sk.gap, "No day without a meal."),
        item("longest run at one place", sk.same_place, "None yet."),
    ]


def when(matrix) -> dict:
    """The busiest slot, weekday and hour, in sentences that state what the heatmap shows."""
    slot = h.busiest_slot(matrix)
    if not slot:
        return {"slot": None, "lead": "No meals yet.", "totals": None}
    wd, hour, n = slot
    lead = f"Busiest slot: {charts.WEEKDAY_NAMES[wd]} around {h.format_hour(hour)} ({fmt.plural(n, 'meal')} so far)."
    by_day, by_hour = matrix.sum(axis=1), matrix.sum(axis=0)
    top_day, top_hour = by_day.idxmax(), int(by_hour.idxmax())
    totals = (
        f"Over the whole week, {charts.WEEKDAY_NAMES[top_day]} has the most meals ({int(by_day[top_day])}) "
        f"and {h.format_hour(top_hour)} is the busiest hour ({int(by_hour[top_hour])})."
    )
    return {"slot": slot, "lead": lead, "totals": totals}


def late(ln: h.LateNight) -> dict:
    """Late night: three figures and a sentence, or the sentence that says why there is nothing."""
    if ln.reason:
        return {"band": None, "line": f"Nothing to show: {ln.reason}."}
    if ln.count == 0:
        return {"band": None, "line": f"Nothing bought late at night ({window(ln)}) so far."}
    band = [
        {
            "value": str(ln.count),
            "label": "late-night visit" if ln.count == 1 else "late-night visits",
            "detail": f"On {fmt.plural(ln.nights, 'night')}.",
            "kind": kit.DAYS,  # a count by time of day
        },
        {"value": fmt.cents(ln.dd_dollars), "label": "dining dollars spent late", "kind": kit.DOLLARS},
    ]
    if ln.dd_share is not None:
        band.append({"value": fmt.pct(ln.dd_share), "label": "of all dining-dollar spending", "kind": kit.DOLLARS})
    line = (
        f"Late night is {window(ln)}: {fmt.plural(ln.me_swipes, 'exchange swipe')} and "
        f"{fmt.plural(ln.dd_purchases, 'dining-dollar purchase')}, most often at {ln.top_place} ({ln.top_place_count})."
    )
    return {"band": band, "line": line}


def places_table(places) -> dict:
    rows = [
        [
            str(place),
            str(int(r["visits"])),
            str(int(r["swipes"])),
            str(int(r["dd_meals"])),
            str(int(r["dd_snacks"])),
            fmt.cents(float(r["dd_spend"])),
            h.format_hour(r["typical_hour"]),
            charts.WEEKDAY_NAMES[r["favorite_weekday"]],
        ]
        for place, r in places.iterrows()
    ]
    columns = [
        ("Place", False), ("Visits", True), ("Exchange swipes", True), ("Dining-dollar meals", True), ("Snacks", True),
        ("Dining dollars spent", True), ("Usual time", False), ("Favorite day", False),
    ]  # fmt: skip
    return {"columns": columns, "rows": rows}


def picked_week(weeks: list[date], raw: str | None) -> date:
    """The week the URL asks for (any day of it), or the latest when it asks for none or a bad one."""
    try:
        d = date.fromisoformat(raw) if raw else None
    except ValueError:
        d = None
    monday = d - timedelta(days=d.weekday()) if d else None
    return monday if monday in weeks else weeks[-1]


def weekly(ctx: PageContext, raw: str | None) -> dict:
    """The recap part: the weeks to pick from, the picked one's sentences and its chart."""
    cfg, df = ctx.cfg, ctx.df
    weeks = rc.weeks_available(df, cfg)
    if not weeks:
        return {"weeks": [], "empty": "No observed weeks yet."}
    week = picked_week(weeks, raw)
    i = weeks.index(week)
    options = [
        {"value": d.isoformat(), "label": f"Week of {fmt.day(d)}" + (" (latest)" if d == weeks[-1] else ""), "on": d == week}
        for d in reversed(weeks)
    ]

    def step(j: int) -> str | None:
        if not 0 <= j < len(weeks):
            return None
        return ctx.url("/habits#recap", week=None if j == len(weeks) - 1 else weeks[j])

    out = {
        "weeks": options,
        "week": week,
        "action": "/habits#recap",  # a GET form drops the action's query, so the view params go in hidden fields
        "keep": ctx.params.query(),
        "earlier": step(i - 1),
        "later": step(i + 1),
    }
    r = rc.weekly_recap(df, cfg, week)
    if r.reason:
        return {**out, "empty": f"Nothing to show: {r.reason}."}
    return {
        **out,
        "empty": None,
        "first": r.lines[0] if r.lines else None,
        "lines": r.lines[1:],
        "covers": f"{dates(r.start, r.end)} ({r.days} of 7 days in your data).",
        "chart": charts.week_chart(ctx.daily, week),
    }


def build(ctx: PageContext, request) -> dict:
    if not ctx.has_data:
        return {"empty": True}
    cfg, df = ctx.cfg, ctx.df
    matrix = h.hour_weekday_matrix(df, cfg)
    w = when(matrix)
    sk = h.streaks(df, cfg)
    ln = h.late_night(df, cfg)
    places = h.place_stats(df, cfg)

    if w["slot"]:
        wd, hour, n = w["slot"]
        headline = f"You eat most often on {charts.WEEKDAY_NAMES[wd]} around {h.format_hour(hour)}."
    else:
        headline = "No meals yet."
    sub = None
    if not places.empty:
        top = places.iloc[0]
        sub = f"{places.index[0]} is where you go most, with {fmt.plural(int(top['visits']), 'visit')} so far."

    return {
        "empty": False,
        "headline": headline,
        "sub": sub,
        "notes": notes(ctx),
        "streaks": None if sk.reason else streak_band(sk),
        "streaks_empty": f"No streaks to show: {sk.reason}." if sk.reason else None,
        "when": w,
        "heat": charts.heat_chart(matrix, cfg.day_rollover_hour, h.late_hours(ln.start_hour, ln.end_hour)),
        "late": late(ln),
        "late_window": window(ln),
        "places_chart": charts.places_chart(places),
        "places": places_table(places) if not places.empty else None,
        "recap": weekly(ctx, request.query_params.get("week")),
    }
