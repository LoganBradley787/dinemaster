"""Weekly recap: what one Monday-to-Sunday week of dining looked like, in numbers and sentences.

Pure functions over the ingest DataFrame (`dinemaster.ingest.load_transactions`) and a `Config`.
No Streamlit, no file writes.

Vocabulary (all `datetime.date`):
    S, E    semester start / end (inclusive).
    O       `freshness.observed_through(cfg, df)`: the last day whose usage is actually known.
            Days after O are unknown, never counted as zero-meal days.
    week    Monday through Sunday. A week's *window* is the part of it inside `S..O`, so the
            semester's first week, the week in progress, and the semester's last week can all be
            shorter than 7 days.

Typical use:

    for monday in weeks_available(df, cfg):          # oldest first; the last one is the latest
        recap = weekly_recap(df, cfg, monday)
        for sentence in recap.lines: ...             # escape "$" before putting it in markdown

Counting rules (same as `metrics.daily`): meals = net meal-exchange swipes (a reversal cancels a
swipe) + dining-dollar purchases classed as meals. A split-tender dining-dollar purchase (several
ledger rows with the same timestamp and description) is one purchase. Transactions before
`cfg.data_cutoff` are ignored.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from dinemaster import metrics
from dinemaster.config import Config
from dinemaster.freshness import observed_through

# A pace delta smaller than this (in meals) is described as "right on" pace instead of ahead/behind.
ON_PACE_TOLERANCE = 0.05
# Snack totals at or above this many dollars are shown as whole dollars ("$14"), smaller ones to the cent.
WHOLE_DOLLARS_FROM = 10.0

_NO_DATA = "no transactions loaded"
_OUTSIDE = "that week has no observed days in the semester"


@dataclass(frozen=True)
class Recap:
    """One week's dining summary, as returned by `weekly_recap`.

    Fields:
        week_start: the Monday of the week (always set, even when nothing was observed).
        start, end: first and last observed day of the week, i.e. the week clipped to `S..O`
            (inclusive). Both None when the week lies entirely outside `S..O`.
        days: number of observed days, `(end - start) + 1`; 0 when there are none.
        complete: True when all 7 days are observed.
        is_latest: True when this is the week containing O (the newest week there is data for).
            `lines` say "this week" / "last week" for it and name the week otherwise.
        in_progress: True when the week is cut short by O rather than by the semester's edges
            (more days of it are still to come). `lines` then say "so far this week".
        meals: `me_swipes + dd_meals`.
        me_swipes: net meal-exchange swipes in the window (reversals subtracted).
        dd_meals: dining-dollar purchases classed as meals (split tender counted once).
        snack_spend: dollars spent on dining-dollar purchases classed as snacks (positive).
        snack_count: number of those snack purchases (split tender counted once).
        dd_spend: all dining dollars spent in the window, meals and snacks, net of refunds
            (positive). So `dd_spend - snack_spend` is roughly what the DD meals cost.
        top_spot, top_spot_count: the place (transaction description) with the most meals in the
            window and how many; ties go to the alphabetically first name. None / 0 when the week
            had no meals. Snacks don't count toward it.
        busiest_day: `(date, meals)` for the day with the most meals; ties go to the earliest
            day. None when the week had no meals.
        zero_meal_days: observed days in the window with no meal. When `cfg.exclude_away` is on,
            away days are not counted (being away isn't skipping a meal).
        prev_meals: meals in the previous week's window (which may itself be partial), or None
            when the previous week has no observed days (it was before the semester).
        prev_meals_same_days: meals on the days exactly one week before each observed day of this
            week — the like-for-like figure the comparison sentence uses. Equals `prev_meals` when
            both weeks are complete. None when any of those days falls before S.
        pace_delta: meal exchanges left minus the even-pace target at the end of `end`
            (`metrics.compute_metrics(...).targets[variant].strict_delta` with `as_of=end`; the
            "away" variant when `cfg.exclude_away` is on, else "plain"). Positive = ahead (more
            left than an even pace needs), negative = behind. None when it can't be computed.
        lines: 3–5 short sentences for display (fewer only in degenerate cases; empty when
            `reason` is set). In order: meals with the week-over-week comparison, top spot,
            snacks, busiest day, pace. May contain "$" — escape it for markdown.
        reason: why there is nothing to show (no data, or the week is outside `S..O`); None for
            a normal recap. When set, counts are 0 and the optional fields are None.
    """

    week_start: date
    start: date | None
    end: date | None
    days: int
    complete: bool
    is_latest: bool
    in_progress: bool
    meals: int
    me_swipes: int
    dd_meals: int
    snack_spend: float
    snack_count: int
    dd_spend: float
    top_spot: str | None
    top_spot_count: int
    busiest_day: tuple[date, int] | None
    zero_meal_days: int
    prev_meals: int | None
    prev_meals_same_days: int | None
    pace_delta: float | None
    lines: list[str] = field(default_factory=list)
    reason: str | None = None


def _monday(d: date) -> date:
    """The Monday of the week containing `d`."""
    return d - timedelta(days=d.weekday())


def _has_data(df: pd.DataFrame | None) -> bool:
    return df is not None and not df.empty


def weeks_available(df: pd.DataFrame, cfg: Config) -> list[date]:
    """Mondays of every week that has at least one observed day, oldest first.

    A week qualifies when it intersects `S..O`, so the first entry can be a Monday before the
    semester starts and the last entry is the week containing O (possibly still in progress).
    Empty when there are no transactions or O is before the semester starts.
    """
    if not _has_data(df):
        return []
    s, o = cfg.semester_start, observed_through(cfg, df)
    if o < s:
        return []
    first, last = _monday(s), _monday(o)
    return [first + timedelta(weeks=i) for i in range((last - first).days // 7 + 1)]


def _empty(week_start: date, reason: str) -> Recap:
    return Recap(
        week_start=week_start, start=None, end=None, days=0, complete=False, is_latest=False,
        in_progress=False, meals=0, me_swipes=0, dd_meals=0, snack_spend=0.0, snack_count=0,
        dd_spend=0.0, top_spot=None, top_spot_count=0, busiest_day=None, zero_meal_days=0,
        prev_meals=None, prev_meals_same_days=None, pace_delta=None, lines=[], reason=reason,
    )


def _top_spot(rows: pd.DataFrame, purchases: pd.DataFrame) -> tuple[str | None, int]:
    """Place with the most meals among `rows` (ME swipes, net) and `purchases` (DD, deduplicated)."""
    swipes = rows[(rows["pot"] == "ME") & rows["kind"].isin(["usage", "reversal"])]
    per_place = -swipes.groupby("description")["amount"].sum()
    dd_meals = purchases[purchases["dd_class"] == "meal"].groupby("description").size()
    per_place = per_place.add(dd_meals, fill_value=0)
    per_place = per_place[per_place > 0.5]
    if per_place.empty:
        return None, 0
    place, count = min(per_place.items(), key=lambda item: (-item[1], str(item[0])))
    return str(place), int(round(count))


def _pace_delta(df: pd.DataFrame, cfg: Config, end: date) -> float | None:
    """Strict `me_left − target` as of `end`, in the variant the user has selected."""
    result = metrics.compute_metrics(df, dataclasses.replace(cfg, as_of=end))
    return result.targets["away" if cfg.exclude_away else "plain"].strict_delta


def weekly_recap(df: pd.DataFrame, cfg: Config, week_start: date) -> Recap:
    """Summarise the week starting on the Monday `week_start` (see `Recap` for every field).

    `week_start` is normally one of `weeks_available(df, cfg)`; any other day is treated as "the
    week containing this day". The week is clipped to `S..O`, so a week in progress only covers
    the days observed so far. Never raises on empty or out-of-range input: the result then has
    `reason` set, zero counts and no `lines`.
    """
    week_start = _monday(week_start)
    if not _has_data(df):
        return _empty(week_start, _NO_DATA)

    s, e, o = cfg.semester_start, cfg.semester_end, observed_through(cfg, df)
    week_end = week_start + timedelta(days=6)
    start, end = max(week_start, s), min(week_end, o)
    if start > end:
        return _empty(week_start, _OUTSIDE)

    known = df[df["date"] >= cfg.data_cutoff]
    per_day = metrics.daily(known, dataclasses.replace(cfg, as_of=o))  # indexed S..O
    week = per_day.loc[start:end]
    meals_by_day = week["meals"].round().astype(int)
    meals = int(meals_by_day.sum())

    rows = known[(known["date"] >= start) & (known["date"] <= end)]
    purchases = metrics._dd_purchases(rows)  # one row per DD purchase (split tender merged)
    snacks = purchases[purchases["dd_class"] == "snack"]
    top_spot, top_spot_count = _top_spot(rows, purchases)

    busiest_day = None
    if meals > 0:
        busiest = meals_by_day.idxmax()  # first maximum = earliest day
        busiest_day = (busiest, int(meals_by_day[busiest]))

    skip_away = cfg.exclude_away
    zero_meal_days = sum(1 for d, n in meals_by_day.items() if n == 0 and not (skip_away and cfg.is_away(d)))

    prev_start, prev_end = max(week_start - timedelta(days=7), s), week_start - timedelta(days=1)
    prev_meals = None
    if prev_start <= prev_end:
        prev_meals = int(round(per_day.loc[prev_start:prev_end, "meals"].sum()))
    prev_meals_same_days = None
    if start - timedelta(days=7) >= s:
        same_days = per_day.loc[start - timedelta(days=7) : end - timedelta(days=7), "meals"]
        prev_meals_same_days = int(round(same_days.sum()))

    recap = Recap(
        week_start=week_start,
        start=start,
        end=end,
        days=(end - start).days + 1,
        complete=start == week_start and end == week_end,
        is_latest=week_start == _monday(o),
        in_progress=end == o and end < min(week_end, e),
        meals=meals,
        me_swipes=int(round(week["me_swipes"].sum())),
        dd_meals=int(round(week["dd_meals"].sum())),
        snack_spend=float(-snacks["amount"].sum()) or 0.0,  # "or" turns -0.0 into 0.0
        snack_count=int(len(snacks)),
        dd_spend=float(week["dd_spend"].sum()),
        top_spot=top_spot,
        top_spot_count=top_spot_count,
        busiest_day=busiest_day,
        zero_meal_days=zero_meal_days,
        prev_meals=prev_meals,
        prev_meals_same_days=prev_meals_same_days,
        pace_delta=_pace_delta(df, cfg, end),
    )
    return dataclasses.replace(recap, lines=recap_lines(recap))


# Sentences
def _count(n: int, noun: str) -> str:
    """'1 meal', '3 meals'."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _short_date(d: date) -> str:
    """'Sep 7'."""
    return f"{d:%b} {d.day}"


def _money(amount: float) -> str:
    """'$14' for whole or larger amounts, '$4.50' for small ones with cents."""
    if amount >= WHOLE_DOLLARS_FROM or abs(amount - round(amount)) < 0.005:
        return f"${amount:,.0f}"
    return f"${amount:,.2f}"


def _meal_line(r: Recap) -> str:
    if r.is_latest:
        this_week = "so far this week" if r.in_progress else "this week"
        last_week = "last week"
    else:
        this_week = f"the week of {_short_date(r.week_start)}"
        last_week = "the week before"
    line = f"{_count(r.meals, 'meal') if r.meals else 'No meals'} {this_week}"

    prev = r.prev_meals_same_days
    if prev is None:
        return line + "."
    diff = r.meals - prev
    if not r.complete:  # partial week: say that only the matching days are being compared
        last_week = f"the same days {last_week}"
        if diff == 0:
            return f"{line}, level with {last_week}."
    if diff == 0:
        return f"{line}, same as {last_week}."
    return f"{line}, {abs(diff)} {'more' if diff > 0 else 'fewer'} than {last_week}."


def _day_line(r: Recap) -> str:
    day, meals = r.busiest_day
    line = f"Busiest day was {day:%A} with {_count(meals, 'meal')}"
    if r.zero_meal_days:
        line += f"; {_count(r.zero_meal_days, 'day')} had none"
    return line + "."


def _pace_line(r: Recap) -> str:
    if abs(r.pace_delta) < ON_PACE_TOLERANCE:
        line = "Right on an even pace"
    else:
        side = "ahead of" if r.pace_delta > 0 else "behind"
        line = f"{abs(r.pace_delta):.1f} meals {side} an even pace"
    if not r.is_latest and r.end is not None:
        line += f" as of {_short_date(r.end)}"
    return line + "."


def recap_lines(recap: Recap) -> list[str]:
    """The display sentences for `recap` (what `weekly_recap` stores in `Recap.lines`).

    Up to five, in this order, each skipped when it has nothing to say:
        1. meals, compared like-for-like with the week before when that's possible —
           "9 meals this week, 2 more than last week." / "4 meals so far this week, 1 more than
           the same days last week." (or "level with the same days last week.") / "No meals the
           week of Aug 31, 8 fewer than the week before."
        2. top spot — "3 at GH The Den."
        3. snacks — "$14 on 3 snacks." / "No snacks."
        4. busiest day and meal-less days — "Busiest day was Tuesday with 3 meals; 2 days had none."
        5. pace — "10.4 meals ahead of an even pace." / "3.0 meals behind an even pace as of Sep 13."
    Returns [] when `recap.reason` is set.
    """
    if recap.reason is not None:
        return []
    lines = [_meal_line(recap)]
    if recap.top_spot is not None:
        lines.append(f"{recap.top_spot_count} at {recap.top_spot}.")
    if recap.snack_count:
        lines.append(f"{_money(recap.snack_spend)} on {_count(recap.snack_count, 'snack')}.")
    else:
        lines.append("No snacks.")
    if recap.busiest_day is not None:
        lines.append(_day_line(recap))
    if recap.pace_delta is not None:
        lines.append(_pace_line(recap))
    return lines
