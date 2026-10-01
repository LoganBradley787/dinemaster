"""Eating habits: when, where and how regularly meals happen (spec "8. Habits").

Pure functions over the ingest DataFrame (`dinemaster.ingest.load_transactions`) and a `Config`.
No Streamlit, no file writes. Figure builders for these results live in `habits_charts.py`.

Vocabulary
    S, E   semester start / end (`cfg.semester_start`, `cfg.semester_end`).
    O      observed-through day, `freshness.observed_through(cfg, df)`: the last day whose usage
           is actually known. Days after O are unknown and never treated as zero-meal days.
    place  a transaction's `description` (e.g. "GH The Den").
    visit  one thing bought at a place: an ME swipe that was not reversed, or one DD purchase
           (a split-tender purchase - same timestamp and description on several DD accounts -
           is one visit).
    meal   a visit that counts as a meal: every ME swipe, and DD purchases with
           `dd_class == "meal"`. DD snacks are visits but not meals.

Reversals
    An ME reversal (a refunded swipe) cancels the swipe it undoes rather than being counted at
    its own timestamp: it is matched to the most recent earlier swipe at the same place (falling
    back to the most recent earlier swipe anywhere). The cancelled swipe disappears from the hour
    matrix, the streaks, the late-night numbers and the place table, so no count is ever
    negative. A reversal with no earlier swipe in the window is ignored. DD reversals (refunds)
    reduce DD dollars but never change visit or meal counts, as in the ingest spec.

Windows
    `hour_weekday_matrix`, `late_night` and `place_stats` look at `cfg.data_cutoff .. O`.
    `streaks` looks at `S .. O` (a streak needs a defined run of days).

Weekdays are numbered Monday=0 .. Sunday=6 and labelled with `WEEKDAYS`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from dinemaster.config import Config
from dinemaster.freshness import observed_through

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
"""Short weekday labels in Monday=0 .. Sunday=6 order (row labels of the hour matrix)."""

PLACE_STATS_COLUMNS = [
    "swipes", "dd_meals", "dd_snacks", "dd_spend", "visits", "typical_hour", "favorite_weekday", "favorite_weekday_num",
]
"""Column order of `place_stats` (also the columns of its empty result)."""

_VISIT_COLUMNS = ["timestamp", "date", "place", "pot", "n", "meals", "dd_class"]
_REQUIRED = {"pot", "timestamp", "date", "description", "amount", "kind", "dd_class"}
_EPS = 1e-9


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


def _window(df: pd.DataFrame | None, start: date, end: date) -> pd.DataFrame | None:
    """Rows dated `start..end` (inclusive), or None when there is nothing usable."""
    if df is None or df.empty or not _REQUIRED.issubset(df.columns) or start > end:
        return None
    sub = df[(df["date"] >= start) & (df["date"] <= end)]
    if sub.empty:
        return None
    return sub.assign(timestamp=pd.to_datetime(sub["timestamp"]))


def _surviving_swipes(me: pd.DataFrame) -> list[dict]:
    """ME swipes left after each reversal cancels the swipe it undoes (see module docstring)."""
    ordered = me.assign(_reversal=me["kind"] == "reversal").sort_values(["timestamp", "_reversal"], kind="stable")
    swipes: list[dict] = []
    for ts, day, place, amount, is_reversal in zip(
        ordered["timestamp"], ordered["date"], ordered["description"], ordered["amount"], ordered["_reversal"]
    ):
        if not is_reversal:
            if -amount > _EPS:
                swipes.append({"timestamp": ts, "date": day, "place": place, "n": float(-amount)})
            continue
        to_cancel = float(amount)
        for same_place_only in (True, False):
            for swipe in reversed(swipes):
                if to_cancel <= _EPS:
                    break
                if swipe["n"] <= _EPS or (same_place_only and swipe["place"] != place):
                    continue
                cancelled = min(swipe["n"], to_cancel)
                swipe["n"] -= cancelled
                to_cancel -= cancelled
    return [s for s in swipes if s["n"] > _EPS]


def _visits(df: pd.DataFrame | None, start: date, end: date) -> pd.DataFrame:
    """One row per visit dated `start..end`, oldest first.

    Columns: `timestamp`, `date`, `place`, `pot` ('ME'|'DD'), `n` (how many visits the row stands
    for: the swipe count for ME - normally 1 - and 1 for a DD purchase), `meals` (how many of
    those are meals: `n` for ME, 1 for a DD meal, 0 for a DD snack), `dd_class` (None for ME).
    """
    sub = _window(df, start, end)
    if sub is None:
        return pd.DataFrame(columns=_VISIT_COLUMNS)

    me = sub[(sub["pot"] == "ME") & sub["kind"].isin(["usage", "reversal"])]
    rows = [
        {**s, "pot": "ME", "meals": s["n"], "dd_class": None} for s in (_surviving_swipes(me) if not me.empty else [])
    ]

    dd_usage = sub[(sub["pot"] == "DD") & (sub["kind"] == "usage")]
    if not dd_usage.empty:
        purchases = dd_usage.groupby(["timestamp", "description"], as_index=False).agg(
            date=("date", "first"), dd_class=("dd_class", "first")
        )
        for ts, place, day, dd_class in zip(
            purchases["timestamp"], purchases["description"], purchases["date"], purchases["dd_class"]
        ):
            rows.append({
                "timestamp": ts, "date": day, "place": place, "pot": "DD", "n": 1.0,
                "meals": 1.0 if dd_class == "meal" else 0.0, "dd_class": dd_class,
            })

    if not rows:
        return pd.DataFrame(columns=_VISIT_COLUMNS)
    out = pd.DataFrame(rows, columns=_VISIT_COLUMNS)
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    return out.sort_values("timestamp", kind="stable").reset_index(drop=True)


def _dd_net_rows(df: pd.DataFrame | None, start: date, end: date) -> pd.DataFrame:
    """DD usage + reversal rows dated `start..end` with a positive `dollars` column (refunds negative)."""
    sub = _window(df, start, end)
    if sub is None:
        return pd.DataFrame(columns=["timestamp", "description", "dollars"])
    dd = sub[(sub["pot"] == "DD") & sub["kind"].isin(["usage", "reversal"])]
    return dd.assign(dollars=-dd["amount"])[["timestamp", "description", "dollars"]]


def day_rollover_hour(cfg: Config) -> int:
    """Hour at which a new "dining day" starts, used so late nights are not split at midnight.

    Equal to `cfg.habits.late_night_end` when the late-night window ends in the morning (the
    default 4 means 00:00-03:59 belongs to the previous evening); 0 (plain calendar days)
    when the window ends after noon.
    """
    end = int(cfg.habits.late_night_end) % 24
    return end if end <= 12 else 0


def late_hours(start: int, end: int) -> list[int]:
    """Clock hours inside the late-night window `[start, end)`, wrapping midnight when start > end.

    `late_hours(21, 4) == [21, 22, 23, 0, 1, 2, 3]`; `late_hours(0, 3) == [0, 1, 2]`;
    `start == end` is an empty window.
    """
    start, end = int(start) % 24, int(end) % 24
    return [(start + i) % 24 for i in range((end - start) % 24)]


def format_hour(hour: float | None) -> str:
    """Clock label for a decimal hour: 12.5 -> "12:30 PM", 9 -> "9 AM", 0 -> "12 AM"; None/NaN -> ""."""
    if hour is None or (isinstance(hour, float) and math.isnan(hour)):
        return ""
    minutes = int(round(float(hour) * 60)) % (24 * 60)
    h, m = divmod(minutes, 60)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


# ---------------------------------------------------------------------------
# Hour x weekday matrix
# ---------------------------------------------------------------------------


def hour_weekday_matrix(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Meal counts by weekday and clock hour over `cfg.data_cutoff .. O`.

    Returns a 7 x 24 integer DataFrame, always that shape (all zeros with no data):
        index    `WEEKDAYS` ("Mon" .. "Sun", so `.iloc[i]` is weekday number i), name "weekday".
        columns  ints 0..23 (hour of day the meal was bought), name "hour".
        values   meals = ME swipes net of reversals + DD meal purchases. A reversed swipe is
                 removed from the cell of the swipe itself, so cells are never negative.
    Weekday and hour come from the transaction's own clock time: a 00:10 purchase after a
    Saturday night out is in the "Sun" row, hour 0.
    """
    matrix = pd.DataFrame(
        0, index=pd.Index(WEEKDAYS, name="weekday"), columns=pd.Index(range(24), name="hour"), dtype=int
    )
    visits = _visits(df, cfg.data_cutoff, observed_through(cfg, df))
    meals = visits[visits["meals"] > 0]
    if meals.empty:
        return matrix
    counts = meals.groupby([meals["timestamp"].dt.weekday, meals["timestamp"].dt.hour])["meals"].sum()
    for (weekday, hour), value in counts.items():
        matrix.iloc[int(weekday), int(hour)] = int(round(value))
    return matrix


def busiest_slot(matrix: pd.DataFrame) -> tuple[str, int, int] | None:
    """The fullest cell of an `hour_weekday_matrix` as `(weekday label, hour, meals)`.

    None when the matrix is empty or all zeros. Ties go to the earliest weekday, then hour.
    """
    if matrix is None or matrix.empty or int(matrix.to_numpy().max()) <= 0:
        return None
    values = matrix.to_numpy()
    row, col = divmod(int(values.argmax()), values.shape[1])
    return str(matrix.index[row]), int(matrix.columns[col]), int(values[row, col])


# ---------------------------------------------------------------------------
# Streaks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """A run of consecutive counted days.

    Fields:
        length: number of counted days in the run (0 = there is no such run).
        start, end: first and last day of the run (None when length is 0). When away days are
            being skipped the run can bridge them, so `end - start + 1` may exceed `length`.
        place: the place for a same-place run; None for every other kind of run.
    """

    length: int
    start: date | None
    end: date | None
    place: str | None = None


_NO_RUN = Run(0, None, None, None)


@dataclass(frozen=True)
class Streaks:
    """Longest and current runs within `S .. O`.

    Counted days: every day `S..O`, except that with `cfg.exclude_away` on, an away day
    (`cfg.is_away`) on which nothing was eaten is skipped entirely - it neither breaks a run nor
    adds to its length, so a streak or a gap carries across a break. An away day with a meal is
    an ordinary meal day. With the toggle off (or a disabled period) away days are ordinary.

    Fields:
        same_place: longest run of consecutive counted days with at least one meal at the same
            place (`place` is set). A day with meals at two places extends both places' runs.
        meal_streak: longest run of consecutive counted days with at least one meal.
        gap: longest run of consecutive counted days with no meal.
        current: the run of meal days ending on the last counted day (length 0 if that day had
            no meal - note O can be a day whose export shows nothing yet).
        days: number of counted days.
        observed_through: O.
        reason: why every run is empty ("no observed days yet", "no meals recorded yet"), else None.

    Ties: among equally long runs the most recent wins; two places tied on the same days are
    resolved alphabetically.
    """

    same_place: Run
    meal_streak: Run
    gap: Run
    current: Run
    days: int
    observed_through: date
    reason: str | None


def _longest_run(days: list[date], flags: list[bool], place: str | None = None) -> Run:
    """Longest stretch of consecutive True flags; the latest one on a tie."""
    best = _NO_RUN
    length = 0
    for i, flag in enumerate(flags):
        length = length + 1 if flag else 0
        if length and length >= best.length:
            best = Run(length, days[i - length + 1], days[i], place)
    return best


def streaks(df: pd.DataFrame, cfg: Config) -> Streaks:
    """Longest same-place run, longest meal streak, longest gap and current streak (see `Streaks`).

    Never raises on empty or short data: every run is `Run(0, None, None)` and `reason` says why.
    """
    s, o = cfg.semester_start, observed_through(cfg, df)
    if o < s:
        return Streaks(_NO_RUN, _NO_RUN, _NO_RUN, _NO_RUN, 0, o, "no observed days yet")

    visits = _visits(df, s, o)
    meals = visits[visits["meals"] > 0]
    meal_days = set(meals["date"])
    all_days = [s + timedelta(days=i) for i in range((o - s).days + 1)]
    days = [d for d in all_days if d in meal_days or not (cfg.exclude_away and cfg.is_away(d))]
    if not meal_days:
        return Streaks(_NO_RUN, _NO_RUN, _NO_RUN, _NO_RUN, len(days), o, "no meals recorded yet")

    ate = [d in meal_days for d in days]

    same_place = _NO_RUN
    for place, group in sorted(meals.groupby("place"), key=lambda kv: kv[0]):
        at_place = set(group["date"])
        run = _longest_run(days, [d in at_place for d in days], place)
        if (run.length, run.end) > (same_place.length, same_place.end or date.min):
            same_place = run

    current_len = 0
    while current_len < len(ate) and ate[-1 - current_len]:
        current_len += 1
    current = Run(current_len, days[-current_len], days[-1]) if current_len else _NO_RUN

    return Streaks(
        same_place=same_place,
        meal_streak=_longest_run(days, ate),
        gap=_longest_run(days, [not a for a in ate]),
        current=current,
        days=len(days),
        observed_through=o,
        reason=None,
    )


# ---------------------------------------------------------------------------
# Late night
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LateNight:
    """Late-night activity over `cfg.data_cutoff .. O`.

    "Late night" is the clock-hour window `[start_hour, end_hour)` from `cfg.habits`
    (`late_night_start` inclusive, `late_night_end` exclusive); it wraps midnight when
    start > end, so the default 21 -> 4 covers 9:00 PM through 3:59 AM.

    Fields:
        start_hour, end_hour: the window, as configured.
        count: late-night visits = `me_swipes + dd_purchases`.
        me_swipes: ME swipes in the window (reversed swipes removed).
        dd_purchases: DD purchases in the window, meals and snacks alike (split tender once).
        dd_dollars: DD dollars spent in the window, net of refunds posted in the window (>= 0).
        dd_share: `dd_dollars` as a fraction (0..1) of all DD dollars spent in
            `data_cutoff .. O`; None when no DD has been spent at all.
        top_place, top_place_count: the place with the most late-night visits and how many
            (ties: more late-night DD dollars, then alphabetical); None / 0 with no visits.
        nights: distinct nights with at least one late-night visit; the hours after midnight
            belong to the evening before (see `day_rollover_hour`).
        reason: "no transactions observed yet" when the window holds no visits of any kind;
            None otherwise (including "there is data, but nothing late at night").
    """

    start_hour: int
    end_hour: int
    count: int
    me_swipes: int
    dd_purchases: int
    dd_dollars: float
    dd_share: float | None
    top_place: str | None
    top_place_count: int
    nights: int
    reason: str | None


def late_night(df: pd.DataFrame, cfg: Config) -> LateNight:
    """Late-night DD purchases and ME swipes (see `LateNight` for every field)."""
    start_hour, end_hour = cfg.habits.late_night_start, cfg.habits.late_night_end
    hours = late_hours(start_hour, end_hour)
    cutoff, o = cfg.data_cutoff, observed_through(cfg, df)

    visits = _visits(df, cutoff, o)
    dd_rows = _dd_net_rows(df, cutoff, o)
    dd_total = float(dd_rows["dollars"].sum()) if not dd_rows.empty else 0.0
    late_dd = dd_rows[dd_rows["timestamp"].dt.hour.isin(hours)] if not dd_rows.empty else dd_rows
    dd_dollars = max(0.0, float(late_dd["dollars"].sum())) if not late_dd.empty else 0.0
    dd_share = min(1.0, dd_dollars / dd_total) if dd_total > _EPS else None

    reason = "no transactions observed yet" if visits.empty else None
    late = visits[visits["timestamp"].dt.hour.isin(hours)] if not visits.empty else visits
    if late.empty:
        return LateNight(start_hour, end_hour, 0, 0, 0, dd_dollars, dd_share, None, 0, 0, reason)

    me_swipes = int(round(late.loc[late["pot"] == "ME", "n"].sum()))
    dd_purchases = int((late["pot"] == "DD").sum())

    by_place = late.groupby("place")["n"].sum()
    dollars_by_place = late_dd.groupby("description")["dollars"].sum() if not late_dd.empty else pd.Series(dtype=float)
    top_place = min(by_place.index, key=lambda p: (-by_place[p], -float(dollars_by_place.get(p, 0.0)), p))

    rollover = day_rollover_hour(cfg)
    night_of = [
        ts.date() - timedelta(days=1) if ts.hour < rollover else ts.date() for ts in late["timestamp"]
    ]

    return LateNight(
        start_hour=start_hour,
        end_hour=end_hour,
        count=me_swipes + dd_purchases,
        me_swipes=me_swipes,
        dd_purchases=dd_purchases,
        dd_dollars=dd_dollars,
        dd_share=dd_share,
        top_place=str(top_place),
        top_place_count=int(round(by_place[top_place])),
        nights=len(set(night_of)),
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------


def place_stats(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Per-place habits over `cfg.data_cutoff .. O`.

    Returns a DataFrame indexed by place (index name "place"), one row per place with at least
    one visit, sorted by most visits (ties: more DD dollars, then alphabetical). Empty frame
    with the same columns when there are no visits. Columns (`PLACE_STATS_COLUMNS`):
        swipes                int    ME swipes there, reversed swipes removed.
        dd_meals, dd_snacks   int    DD purchases there classed meal / snack.
        dd_spend              float  DD dollars spent there, net of refunds, >= 0.
        visits                int    swipes + dd_meals + dd_snacks.
        typical_hour          float  median time of day of the visits as a decimal clock hour in
                                     [0, 24) (12.5 = 12:30 PM; pass to `format_hour`). Hours
                                     before `day_rollover_hour(cfg)` count as the end of the
                                     previous evening, so 11 PM and 1 AM give midnight, not noon.
        favorite_weekday      str    `WEEKDAYS` label of the weekday with the most visits
                                     (by clock date; ties go to the earliest weekday).
        favorite_weekday_num  int    the same weekday as 0..6 (Monday=0).
    """
    cutoff, o = cfg.data_cutoff, observed_through(cfg, df)
    visits = _visits(df, cutoff, o)
    empty = pd.DataFrame(columns=PLACE_STATS_COLUMNS, index=pd.Index([], name="place"))
    if visits.empty:
        return empty

    dd_rows = _dd_net_rows(df, cutoff, o)
    spend = dd_rows.groupby("description")["dollars"].sum() if not dd_rows.empty else pd.Series(dtype=float)
    rollover = day_rollover_hour(cfg)

    records = []
    for place, group in visits.groupby("place"):
        clock = group["timestamp"].dt.hour + group["timestamp"].dt.minute / 60.0
        shifted = clock.where(clock >= rollover, clock + 24.0)
        by_weekday = group.groupby(group["timestamp"].dt.weekday)["n"].sum()
        favorite = int(min(by_weekday.index, key=lambda w: (-by_weekday[w], w)))
        swipes = int(round(group.loc[group["pot"] == "ME", "n"].sum()))
        dd_meals = int((group["dd_class"] == "meal").sum())
        dd_snacks = int(((group["pot"] == "DD") & (group["dd_class"] != "meal")).sum())
        records.append({
            "place": place,
            "swipes": swipes,
            "dd_meals": dd_meals,
            "dd_snacks": dd_snacks,
            "dd_spend": max(0.0, float(spend.get(place, 0.0))),
            "visits": swipes + dd_meals + dd_snacks,
            "typical_hour": float(shifted.median()) % 24.0,
            "favorite_weekday": WEEKDAYS[favorite],
            "favorite_weekday_num": favorite,
        })

    out = pd.DataFrame(records).sort_values(
        ["visits", "dd_spend", "place"], ascending=[False, False, True], kind="stable"
    )
    return out.set_index("place")[PLACE_STATS_COLUMNS]
