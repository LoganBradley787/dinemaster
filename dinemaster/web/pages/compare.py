"""The `compare` section: this semester next to each past semester in the config's history.

Everything is measured over the days the data covers (`dinemaster.compare`), so a past semester
that is only partly in the exports still gives a fair rate. Where a semester or a pot is missing,
the page says so instead of drawing a zero.
"""

from __future__ import annotations

import pandas as pd

from ... import compare as cmp
from .. import fmt
from ..charts import compare as charts
from ..charts import kit
from ..context import PageContext

POT_KIND = {"ME": kit.MEALS, "DD": kit.DOLLARS}
SAME = 0.05
"""Two rates within this share of each other read as "about the same"."""


def _rate(s: cmp.SemesterSummary, pot: str, per: str) -> float | None:
    usage = s.pots[pot]
    return None if usage is None else (usage.per_day if per == "day" else usage.per_week)


def _partner(past: list[cmp.SemesterSummary], pot: str, per: str) -> cmp.SemesterSummary | None:
    """The latest past semester that has a rate for `pot`."""
    having = [s for s in past if _rate(s, pot, per) is not None]
    return max(having, key=lambda s: s.start) if having else None


def _sentence(now: cmp.SemesterSummary, then: cmp.SemesterSummary, pot: str, per: str, over: bool) -> str:
    """One pot's rate this semester against one past semester."""
    a, b = _rate(now, pot, per), _rate(then, pot, per)
    if pot == "ME":
        lead = f"You {'used' if over else 'are using'} {charts.rate(pot, a, per)} exchanges a {per}"
        gap = fmt.num(abs(a - b), 2 if per == "day" else 1)
    else:
        lead = f"You {'spent' if over else 'are spending'} {fmt.cents(a)} a {per} in dining dollars"
        gap = fmt.cents(abs(a - b))
    if abs(a - b) <= SAME * max(a, b):
        return f"{lead}, about the same as in {then.name}."
    more = "more" if a > b else ("fewer" if pot == "ME" else "less")
    return f"{lead}, {gap} {more} than in {then.name}."


def headline(ctx: PageContext, now: cmp.SemesterSummary, past: list[cmp.SemesterSummary], per: str) -> tuple[str, str | None]:
    """The comparison in one sentence, and a quieter one for the other pot or for what is missing."""
    cfg = ctx.cfg
    over = cfg.as_of > cfg.semester_end
    if now.covered_start is None:
        return ("The semester hasn't started yet." if cfg.as_of < cfg.semester_start else "Nothing is recorded for this semester yet."), None
    lines = []
    for pot in cmp.POTS:
        if _rate(now, pot, per) is None:
            continue
        then = _partner(past, pot, per)
        if then is not None:
            lines.append(_sentence(now, then, pot, per, over))
        elif past:
            lines.append(f"{charts.WORDS[pot].capitalize()}s cannot be compared. No past semester has {charts.WORDS[pot]} records.")
    compared = [line for line in lines if line.startswith("You ")]
    if not past:
        return "There are no past semesters to compare with.", f"This page shows {now.name} on its own."
    if not compared:
        return "Past semesters have no data to compare with.", f"This page shows {now.name} on its own."
    rest = [line for line in lines if line is not compared[0]]
    return compared[0], " ".join(rest) or None


def coverage_note(s: cmp.SemesterSummary, cfg) -> dict | None:
    """What the data can and cannot say about one semester, in plain words."""
    if s.covered_start is None:
        return {"kind": kit.UNKNOWN, "text": f"{s.name}: {s.note}"}
    parts, kind = [], kit.DAYS
    if s.current:
        if s.covered_end < cfg.semester_end:
            parts.append(f"In progress. The data runs through {fmt.day(s.covered_end)}, {s.covered_days} of {s.total_days} days.")
    else:
        late = (s.covered_start - s.start).days > cmp.COVERAGE_NOTE_SLACK_DAYS
        early = (s.end - s.covered_end).days > cmp.COVERAGE_NOTE_SLACK_DAYS
        if late or early:
            kind = kit.LOOK
            parts.append(
                f"The data covers only {fmt.day(s.covered_start)} to {fmt.day(s.covered_end)}, "
                f"{s.covered_days} of {s.total_days} days. Rates use those days."
            )
    for pot, usage in s.pots.items():
        if usage is None:
            kind = kit.LOOK
            parts.append(f"No {charts.WORDS[pot]} records.")
    away = s.covered_days - s.rate_days
    if away:
        parts.append(f"{fmt.plural(away, 'away day')} left out of the rates.")
    return {"kind": kind, "text": f"{s.name}: {' '.join(parts)}"} if parts else None


def notes(ctx: PageContext, summaries: list[cmp.SemesterSummary]) -> list[dict]:
    cfg = ctx.cfg
    data = {"href": ctx.url("/data"), "link": "Go to data"}
    out: list[dict] = []
    if ctx.stale:
        out.append({
            "kind": kit.PROBLEM,
            "text": f"Your data is {ctx.behind} days old. It ends {fmt.day(ctx.counts.a)}. This semester's figures stop on that day.",
            **data,
        })  # fmt: skip
    if not cfg.history:
        out.append({"kind": kit.DAYS, "text": "No past semesters are set up. Add [[history.semesters]] entries to config.toml to compare."})
    for s in sorted(summaries, key=lambda s: not s.current):
        note = coverage_note(s, cfg)
        if note:
            out.append(note)
    return out


def band(now: cmp.SemesterSummary, past: list[cmp.SemesterSummary], per: str) -> list[dict]:
    """This semester's two rates and how much of it the data covers."""
    stats = []
    for pot, label in (("ME", f"exchanges a {per}"), ("DD", f"dining dollars a {per}")):
        value = _rate(now, pot, per)
        then = _partner(past, pot, per)
        if then is not None:
            detail = f"{then.name}: {charts.rate(pot, _rate(then, pot, per), per)}."
        elif past:
            detail = f"No past semester has {charts.WORDS[pot]} records."
        else:
            detail = None
        if value is None:
            stats.append({"value": "none", "label": label, "detail": f"No {charts.WORDS[pot]} records this semester.", "kind": POT_KIND[pot]})
        else:
            stats.append({"value": charts.rate(pot, value, per), "label": label, "detail": detail, "kind": POT_KIND[pot]})
    covered = [s for s in past if s.covered_start is not None]
    detail = f"Of {now.total_days} this semester."
    if covered:
        then = max(covered, key=lambda s: s.start)
        detail += f" {then.name}: {then.covered_days} of {then.total_days}."
    stats.append({"value": str(now.covered_days), "label": "days in the data", "detail": detail, "kind": kit.DAYS})
    return stats


def _cell(s: cmp.SemesterSummary, pot: str, field: str) -> str:
    """One figure of the summary table, or why it is missing."""
    if s.covered_start is None:
        return "no data"
    usage = s.pots[pot]
    if usage is None:
        return "no records"
    value = getattr(usage, field)
    if value is None:
        return "no days to count"
    if pot == "DD":
        return fmt.cents(value)
    return fmt.num(value, 0) if field == "used" else fmt.num(value, 2)


def summary_table(ordered: list[cmp.SemesterSummary]) -> dict:
    """Every figure of every semester: one column per semester, one row per measure."""

    def row(label: str, each) -> list[str]:
        return [label, *[each(s) for s in ordered]]

    def split(s: cmp.SemesterSummary, kind: str) -> str:
        if s.dd is None:
            return _cell(s, "DD", "used")
        return f"{getattr(s, f'dd_{kind}_count')} ({fmt.cents(getattr(s, f'dd_{kind}_amount'))})"

    rows = [
        row("Data covers", lambda s: f"{fmt.day(s.covered_start)} to {fmt.day(s.covered_end)}" if s.covered_start else "no data"),
        row("Days covered", lambda s: f"{s.covered_days} of {s.total_days}"),
        row("Days the rates use", lambda s: str(s.rate_days)),
        row("Exchange swipes", lambda s: _cell(s, "ME", "used")),
        row("Exchanges a day", lambda s: _cell(s, "ME", "per_day")),
        row("Exchanges a week", lambda s: _cell(s, "ME", "per_week")),
        row("Dining dollars spent", lambda s: _cell(s, "DD", "used")),
        row("Dining dollars a day", lambda s: _cell(s, "DD", "per_day")),
        row("Dining dollars a week", lambda s: _cell(s, "DD", "per_week")),
        row("Dining-dollar meals", lambda s: split(s, "meal")),
        row("Dining-dollar snacks", lambda s: split(s, "snack")),
        row("Top place", lambda s: f"{s.top_places[0].place} ({s.top_places[0].visits})" if s.top_places else "none"),
    ]
    columns = [("", False), *[(s.name + (" (now)" if s.current else ""), True) for s in ordered]]
    return {"columns": columns, "rows": rows}


def places_table(ordered: list[cmp.SemesterSummary]) -> dict:
    rows = [
        [s.name, p.place, str(p.visits), str(p.me_swipes), fmt.cents(p.dd_spend) if p.dd_spend else ""]
        for s in ordered
        for p in s.top_places
    ]
    return {"columns": [("Semester", False), ("Place", False), ("Visits", True), ("Exchange swipes", True), ("Dining dollars", True)], "rows": rows}


def _weekly_lead(weekly: pd.DataFrame, pot: str, now: cmp.SemesterSummary, past: list[cmp.SemesterSummary]) -> str:
    """The range of one pot's full weeks, this semester and the latest past one that has any."""
    rows = weekly[(weekly["pot"] == pot) & weekly["complete"]] if not weekly.empty else weekly
    each = (lambda v: fmt.num(v, 0)) if pot == "ME" else fmt.cents
    unit = " exchanges" if pot == "ME" else ""
    out = []
    for s in [now, *sorted(past, key=lambda s: s.start, reverse=True)]:
        mine = rows[rows["semester"] == s.name]["amount"] if not rows.empty else rows
        if len(mine) == 0:
            continue
        lo, hi = float(mine.min()), float(mine.max())
        spread = each(lo) if each(lo) == each(hi) else f"between {each(lo)} and {each(hi)}"
        out.append(f"A full week {'this semester' if s.current else f'in {s.name}'} took {spread}{unit}.")
        if len(out) == 2:
            break
    return " ".join(out) or "No full week on record yet."


def build(ctx: PageContext, request) -> dict:
    if not ctx.has_data:
        return {"empty": True}
    cfg = ctx.cfg
    per = "day" if request.query_params.get("per") == "day" else "week"
    summaries = cmp.semester_summaries(ctx.df, cfg)
    now, past = summaries[-1], summaries[:-1]
    weekly = cmp.weekly_usage(ctx.df, cfg)
    names = [s.name for s in summaries]
    ordered = [now, *sorted(past, key=lambda s: s.start, reverse=True)]  # this semester first, then newest to oldest
    title, sub = headline(ctx, now, past, per)

    return {
        "empty": False,
        "headline": title,
        "sub": sub,
        "notes": notes(ctx, summaries),
        "band": band(now, past, per),
        "per": per,
        "per_options": [
            {"label": "A week", "href": ctx.url("/compare#rates", per=None), "on": per == "week"},
            {"label": "A day", "href": ctx.url("/compare#rates", per="day"), "on": per == "day"},
        ],
        "rates": {pot: charts.rate_chart(summaries, pot, per) for pot in cmp.POTS},
        "weekly": {
            pot: {"chart": charts.weekly_chart(weekly, pot, names), "lead": _weekly_lead(weekly, pot, now, past)} for pot in cmp.POTS
        },
        "places": [
            {
                "name": s.name + (" (now)" if s.current else ""),
                "covers": f"{fmt.day(s.covered_start)} to {fmt.day(s.covered_end)}" if s.covered_start else None,
                "chart": charts.places_chart(s),
            }
            for s in ordered
        ],
        "summary": summary_table(ordered),
        "places_table": places_table(ordered),
        "several": len(summaries) > 1,
    }
