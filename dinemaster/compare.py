"""Semester comparison: this semester next to each past semester in `cfg.history`.

Pure functions over the ingest DataFrame (see `dinemaster.ingest.load_transactions`) and `Config`.
Unlike the rest of the app this module ignores `cfg.data_cutoff` — it looks at the whole ledger.

Vocabulary
    window    The dates a semester is asked about. A past semester: its configured `start..end`.
              The current semester: `S..O`, semester start through `freshness.observed_through`
              (usage after `O` is unknown, so it is left out — never counted as zero).
    coverage  The part of a window the ledger can actually speak for. Old semesters are usually
              only partly there (the dining site exports reach back about six months, and some
              accounts may be missing), so every rate is "per covered day", not per semester day.
                * past semester: first to last transaction date inside the window (any kind of
                  row, any account).
                * current semester: the whole window `S..O` as soon as it holds one transaction —
                  quiet days at either edge are known zero-usage days, which keeps these rates in
                  line with the burn rate shown elsewhere in the app.
              A window holding no transactions has no coverage (`None` fields plus a `note`); the
              ledger cannot tell "no plan / no export" apart from "used nothing".
    rate days The covered days used as the denominator of per-day rates: all of them, minus
              `cfg.is_away` days when `cfg.exclude_away` is on (the shared away rule).

Units: ME amounts are meal-exchange swipes, DD amounts are dollars; both are net of reversals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import NamedTuple

import pandas as pd

from dinemaster.config import Config
from dinemaster.freshness import observed_through
from dinemaster.metrics import _dd_purchases

TOP_PLACES = 5
"""How many places `SemesterSummary.top_places` keeps."""

COVERAGE_NOTE_SLACK_DAYS = 3
"""A past semester whose first/last transaction is within this many days of its start/end is
treated as fully exported: the note stays quiet about coverage (rates still use covered days)."""

WEEKLY_COLUMNS = ["semester", "week", "pot", "amount", "week_start", "days", "complete", "current"]
"""Column order of the `weekly_usage` frame."""

POTS = ("ME", "DD")
_POT_WORDS = {"ME": "meal exchange", "DD": "dining dollar"}
_USAGE_KINDS = ["usage", "reversal"]


@dataclass(frozen=True)
class PotUsage:
    """One pot's use over a semester's covered days.

    Fields:
        used: net amount consumed inside the window — swipes for ME, dollars for DD (usage minus
            reversals; deposits and adjustments are not usage). 0.0 when the pot has rows but none
            of them are usage.
        per_day: `used / rate_days`; None when there are no rate days (all covered days away).
        per_week: `per_day * 7`; None when `per_day` is.
    """

    used: float
    per_day: float | None
    per_week: float | None


class TopPlace(NamedTuple):
    """A place (transaction description) and how much it was used in one semester.

    Fields:
        place: the description as it appears in the ledger.
        visits: ME net swipes there + DD purchases there (a split-tender purchase counts once).
        me_swipes: ME net swipes there (int).
        dd_spend: DD dollars spent there, net of reversals.
    """

    place: str
    visits: int
    me_swipes: int
    dd_spend: float


@dataclass(frozen=True)
class SemesterSummary:
    """Usage in one semester, measured over the days the ledger covers.

    Fields:
        name: the history entry's name, or `current_semester_name(cfg)` for the current semester.
        start, end: the window (see module docstring). For the current semester `end` is the
            observed-through date, which is `start - 1 day` or earlier before anything is observed.
        total_days: length of the whole semester in days, start and end inclusive — for the
            current semester that is `S..E`, not the shorter window.
        current: True for the current semester (always the last item of `semester_summaries`).
        covered_start, covered_end: first and last covered date; both None with no data.
        covered_days: calendar days from `covered_start` to `covered_end` inclusive; 0 with no data.
        rate_days: `covered_days` minus away days when `cfg.exclude_away` is on — the denominator
            of every `per_day`.
        me, dd: `PotUsage` for meal exchanges / dining dollars; None when that pot has no rows of
            any kind inside the window (the semester's export did not include it).
        dd_meal_count, dd_meal_amount: DD purchases classed as meals and their gross dollars.
        dd_snack_count, dd_snack_amount: the same for snacks. Counts treat a split-tender
            purchase as one; dollars are gross purchases, so with reversals
            `dd_meal_amount + dd_snack_amount` can exceed `dd.used`. All zero with no DD rows.
        top_places: up to `TOP_PLACES` `TopPlace` tuples, most visits first (ties: more DD
            dollars, then more swipes, then name). Empty list with no data.
        note: plain-words caveats for the UI — why there is no data, partial coverage, a missing
            pot, away days left out. Empty string when there is nothing to caveat.
    """

    name: str
    start: date
    end: date
    total_days: int
    current: bool
    covered_start: date | None
    covered_end: date | None
    covered_days: int
    rate_days: int
    me: PotUsage | None
    dd: PotUsage | None
    dd_meal_count: int
    dd_meal_amount: float
    dd_snack_count: int
    dd_snack_amount: float
    top_places: list[TopPlace]
    note: str

    @property
    def pots(self) -> dict[str, PotUsage | None]:
        """`{"ME": self.me, "DD": self.dd}` for code that loops over pots."""
        return {"ME": self.me, "DD": self.dd}


def current_semester_name(cfg: Config) -> str:
    """Display name for the current semester, from its start date: "Spring|Summer|Fall <year>"."""
    month = cfg.semester_start.month
    season = "Spring" if month <= 5 else "Summer" if month <= 7 else "Fall"
    return f"{season} {cfg.semester_start.year}"


@dataclass(frozen=True)
class _Window:
    """A semester's window, its ledger rows, and the coverage they establish."""

    name: str
    start: date
    end: date
    total_days: int
    current: bool
    rows: pd.DataFrame
    covered_start: date | None
    covered_end: date | None


def _days(start: date, end: date) -> int:
    """Days from `start` to `end` inclusive; 0 when the range is empty."""
    return max((end - start).days + 1, 0)


def _fmt(d: date) -> str:
    return f"{d:%b} {d.day}"


def _rows_between(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df.empty or start > end:
        return df.iloc[0:0]
    return df[(df["date"] >= start) & (df["date"] <= end)]


def _windows(df: pd.DataFrame, cfg: Config) -> list[_Window]:
    """History semesters in config order, then the current semester."""
    out = []
    for h in cfg.history:
        rows = _rows_between(df, h.start, h.end)
        first, last = (rows["date"].min(), rows["date"].max()) if not rows.empty else (None, None)
        out.append(_Window(h.name, h.start, h.end, _days(h.start, h.end), False, rows, first, last))

    s, o = cfg.semester_start, observed_through(cfg, df)
    rows = _rows_between(df, s, o)
    first, last = (s, o) if not rows.empty else (None, None)
    out.append(_Window(current_semester_name(cfg), s, o, _days(s, cfg.semester_end), True, rows, first, last))
    return out


def _net_usage(rows: pd.DataFrame, pot: str) -> pd.DataFrame:
    """The pot's usage and reversal rows (amounts still signed: debits negative)."""
    return rows[(rows["pot"] == pot) & (rows["kind"].isin(_USAGE_KINDS))]


def _pot_usage(rows: pd.DataFrame, pot: str, rate_days: int) -> PotUsage | None:
    if not (rows["pot"] == pot).any():
        return None
    used = float(-_net_usage(rows, pot)["amount"].sum()) + 0.0  # + 0.0 turns -0.0 into 0.0
    per_day = used / rate_days if rate_days > 0 else None
    return PotUsage(used, per_day, per_day * 7 if per_day is not None else None)


def _top_places(rows: pd.DataFrame, purchases: pd.DataFrame) -> list[TopPlace]:
    swipes = (-_net_usage(rows, "ME").groupby("description")["amount"].sum()).to_dict()
    spend = (-_net_usage(rows, "DD").groupby("description")["amount"].sum()).to_dict()
    bought = purchases.groupby("description").size().to_dict() if not purchases.empty else {}
    places = []
    for place in set(swipes) | set(spend) | set(bought):
        me_swipes = int(round(swipes.get(place, 0.0)))
        visits = me_swipes + int(bought.get(place, 0))
        if visits > 0:
            places.append(TopPlace(place, visits, me_swipes, float(spend.get(place, 0.0))))
    places.sort(key=lambda p: (-p.visits, -p.dd_spend, -p.me_swipes, p.place))
    return places[:TOP_PLACES]


def _note(w: _Window, cfg: Config, me: PotUsage | None, dd: PotUsage | None, covered: int, away: int) -> str:
    if w.covered_start is None:
        if w.current and cfg.as_of < w.start:
            return "The semester hasn't started yet."
        return "No transactions in the ledger for these dates."

    parts = []
    if w.current:
        parts.append(f"In progress: observed through {_fmt(w.covered_end)} ({covered} of {w.total_days} days).")
    else:
        late_start = (w.covered_start - w.start).days > COVERAGE_NOTE_SLACK_DAYS
        early_end = (w.end - w.covered_end).days > COVERAGE_NOTE_SLACK_DAYS
        if late_start or early_end:
            parts.append(
                f"Ledger covers {_fmt(w.covered_start)} – {_fmt(w.covered_end)} only "
                f"({covered} of {w.total_days} days); rates use those days."
            )
    for pot, usage in (("ME", me), ("DD", dd)):
        if usage is None:
            parts.append(f"No {_POT_WORDS[pot]} records.")
    if away:
        parts.append(f"{away} away day{'' if away == 1 else 's'} left out of the rates.")
    return " ".join(parts)


def _summarize(w: _Window, cfg: Config) -> SemesterSummary:
    if w.covered_start is None:
        return SemesterSummary(
            name=w.name, start=w.start, end=w.end, total_days=w.total_days, current=w.current,
            covered_start=None, covered_end=None, covered_days=0, rate_days=0, me=None, dd=None,
            dd_meal_count=0, dd_meal_amount=0.0, dd_snack_count=0, dd_snack_amount=0.0,
            top_places=[], note=_note(w, cfg, None, None, 0, 0),
        )  # fmt: skip

    covered = _days(w.covered_start, w.covered_end)
    away = 0
    if cfg.exclude_away:
        away = sum(cfg.is_away(w.covered_start + timedelta(days=i)) for i in range(covered))
    rate_days = covered - away

    me = _pot_usage(w.rows, "ME", rate_days)
    dd = _pot_usage(w.rows, "DD", rate_days)
    purchases = _dd_purchases(w.rows)
    meals = purchases[purchases["dd_class"] == "meal"]
    snacks = purchases[purchases["dd_class"] == "snack"]

    return SemesterSummary(
        name=w.name, start=w.start, end=w.end, total_days=w.total_days, current=w.current,
        covered_start=w.covered_start, covered_end=w.covered_end, covered_days=covered, rate_days=rate_days,
        me=me, dd=dd,
        dd_meal_count=int(len(meals)), dd_meal_amount=float(-meals["amount"].sum()) + 0.0,
        dd_snack_count=int(len(snacks)), dd_snack_amount=float(-snacks["amount"].sum()) + 0.0,
        top_places=_top_places(w.rows, purchases),
        note=_note(w, cfg, me, dd, covered, away),
    )  # fmt: skip


def semester_summaries(df: pd.DataFrame, cfg: Config) -> list[SemesterSummary]:
    """One `SemesterSummary` per `cfg.history` semester (config order), then the current semester.

    Always returns `len(cfg.history) + 1` items and never raises on empty or partial data: a
    semester the ledger knows nothing about comes back with `covered_start is None`, `me`/`dd`
    None, and a `note` saying so. `cfg.data_cutoff` is ignored; the current semester stops at
    `freshness.observed_through(cfg, df)`.
    """
    return [_summarize(w, cfg) for w in _windows(df, cfg)]


def weekly_usage(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Usage per week of each semester, long format, for overlaying semesters on one week axis.

    One row per (semester, pot, week) for every week touched by that semester's coverage, in
    `semester_summaries` order, then pot (ME, DD), then week. Weeks inside coverage with no usage
    are present with `amount` 0; weeks outside coverage, pots with no rows in the semester, and
    semesters with no data have no rows at all (so a chart draws nothing rather than a false zero).

    Columns (`WEEKLY_COLUMNS`):
        semester: semester name (matches `SemesterSummary.name`).
        week: 1-based week of that semester — days 1-7 from its start are week 1, whatever weekday
            the semester starts on.
        pot: "ME" or "DD".
        amount: net usage that week — swipes for ME, dollars for DD. Summed over a semester's
            weeks it equals `SemesterSummary.<pot>.used`.
        week_start: date the week begins (`semester start + 7 * (week - 1)` days).
        days: covered days falling in that week, 1..7. Away days are not removed here.
        complete: `days == 7`. False for the weeks at the edges of coverage (including the
            current, unfinished week), whose `amount` is for fewer days.
        current: True for the current semester's rows.

    Empty frame with these columns when there is nothing to report.
    """
    records = []
    for w in _windows(df, cfg):
        if w.covered_start is None:
            continue
        first_week = (w.covered_start - w.start).days // 7 + 1
        last_week = (w.covered_end - w.start).days // 7 + 1
        for pot in POTS:
            if not (w.rows["pot"] == pot).any():
                continue
            usage = _net_usage(w.rows, pot)
            weeks = usage["date"].map(lambda d: (d - w.start).days // 7 + 1)
            by_week = (-usage.groupby(weeks)["amount"].sum()).to_dict()
            for week in range(first_week, last_week + 1):
                week_start = w.start + timedelta(days=7 * (week - 1))
                week_end = week_start + timedelta(days=6)
                days = _days(max(week_start, w.covered_start), min(week_end, w.covered_end))
                records.append(
                    {
                        "semester": w.name,
                        "week": week,
                        "pot": pot,
                        "amount": float(by_week.get(week, 0.0)) + 0.0,
                        "week_start": week_start,
                        "days": days,
                        "complete": days == 7,
                        "current": w.current,
                    }
                )
    return pd.DataFrame(records, columns=WEEKLY_COLUMNS)
