"""Daily meal plan and dining-dollar spend-down: how to use up what is left by semester end.

Pure functions over `Config`, the two balances, and the weekday profile from
`dinemaster.forecast.weekday_profile`; no Streamlit, no file access, no ledger needed.

Typical call (what the dashboard and tiles do):

    o = freshness.observed_through(cfg, df)
    left = metrics.balances(df, dataclasses.replace(cfg, as_of=o))
    profile = forecast.weekday_profile(df, cfg)
    start = o + timedelta(days=1)                      # first day that is not yet in the data
    plan = daily_budget(cfg, left.me_left, left.dd_left, profile, start)
    line = today_line(plan, date.today())              # "2 meals today, 1 tomorrow"
    advice = spend_down(cfg, left.me_left, left.dd_left, profile, start).sentence

Three public pieces
-------------------
* `daily_budget` places every remaining meal on a specific day. Days of the week that have
  historically been heavier get more (weights = the profile's `meals_rate`), away days get none,
  and no day gets more than `cfg.max_meals_per_day`.
* `today_line` turns that plan into one short line about today and tomorrow.
* `spend_down` answers "how fast should I use each pot so both hit zero on the last day?",
  first setting aside the dining dollars that snacks are expected to take.

Day rules shared by all three: the plan covers `start .. cfg.semester_end` inclusive. A day counts
as *away* only when `cfg.exclude_away` is on and `cfg.is_away(day)`; with the toggle off every day
is an ordinary day. Weekdays are numbered Monday=0 .. Sunday=6.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from dinemaster.config import Config

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: Columns of the `daily_budget` frame, in order.
BUDGET_COLUMNS = ["weekday", "away", "weight", "meals"]

#: Below this many usable days the spend-down sentence gives totals instead of per-week rates
#: (a weekly rate over three days would read as a wildly inflated number).
SHORT_HORIZON_DAYS = 7

#: A per-week rate under this reads as "1 every N weeks" rather than rounding to "0 a week".
MIN_WEEKLY_RATE = 2 / 3

#: Snack money under this many dollars is not worth a mention in the sentence.
SNACK_MENTION_DOLLARS = 0.5

_EPS = 1e-9  # absorbs float noise when flooring quotas and dollar divisions


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _remaining_days(cfg: Config, start: date) -> tuple[list[date], np.ndarray]:
    """Days `max(start, S) .. E` and, aligned with them, whether each counts as an away day."""
    first = max(start, cfg.semester_start)
    days = [first + timedelta(days=i) for i in range((cfg.semester_end - first).days + 1)]
    away = np.array([bool(cfg.exclude_away and cfg.is_away(d)) for d in days], dtype=bool)
    return days, away


def _weekday_values(profile: pd.DataFrame | None, column: str) -> np.ndarray | None:
    """The profile's `column` as 7 non-negative floats (Monday first), or None if unusable.

    Unusable = no profile, an empty one, or one without that column. Missing weekdays, NaNs
    and negative values count as 0.
    """
    if profile is None or len(profile) == 0 or column not in profile.columns:
        return None
    values = pd.to_numeric(profile[column], errors="coerce").reindex(range(7)).to_numpy(dtype=float)
    return np.clip(np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)


def _whole(x: float) -> int:
    """Floor to an int, forgiving float noise (19.999999999 -> 20); never negative."""
    return max(0, math.floor(x + _EPS))


# ---------------------------------------------------------------------------
# daily budget
# ---------------------------------------------------------------------------


def _evenly_spaced(count: int, picks: int) -> np.ndarray:
    """`picks` distinct positions spread evenly through `range(count)` (requires picks <= count)."""
    return ((np.arange(picks) + 0.5) * count / picks).astype(int)


def _largest_remainders(fractions: np.ndarray, picks: int) -> np.ndarray:
    """Positions of the `picks` largest fractions; ties are spread evenly rather than front-loaded.

    Days with the same weight have the same fraction, so there are usually more tied candidates
    than leftover meals. Giving the extras to the earliest tied days would bunch them at the
    start of the plan; picking evenly spaced ones spreads them across the weeks instead.
    """
    if picks <= 0:
        return np.array([], dtype=int)
    key = np.round(fractions, 9)
    threshold = np.sort(key)[::-1][picks - 1]
    certain = np.flatnonzero(key > threshold)
    tied = np.flatnonzero(key == threshold)  # in date order
    return np.concatenate([certain, tied[_evenly_spaced(tied.size, picks - certain.size)]])


def _allocate(weights: np.ndarray, usable: np.ndarray, total: int, cap: int) -> np.ndarray:
    """Whole meals per day: proportional to `weights`, at most `cap` a day, summing to `total`.

    Largest-remainder apportionment with a cap. Each pass shares what is still unplaced among
    the open days in proportion to their weights; any day whose share reaches the cap is filled
    to the cap and closed, and its overflow is re-shared among the rest on the next pass. When
    only zero-weight days are still open they share equally. `total` must not exceed
    `cap * usable.sum()`; non-usable days always get 0.
    """
    meals = np.zeros(len(weights), dtype=int)
    open_days = np.flatnonzero(usable)
    remaining = int(total)
    while remaining > 0 and open_days.size:
        share = weights[open_days]
        if not share.sum() > 0:
            share = np.ones(open_days.size)
        quota = remaining * share / share.sum()

        full = quota >= cap - _EPS
        if full.any():
            meals[open_days[full]] = cap
            remaining -= cap * int(full.sum())
            open_days = open_days[~full]
            continue

        base = np.floor(quota + _EPS).astype(int)
        meals[open_days] = base
        extra = _largest_remainders(quota - base, remaining - int(base.sum()))
        meals[open_days[extra]] += 1
        remaining = 0
    return meals


def daily_budget(cfg: Config, me_left: float, dd_left: float, profile: pd.DataFrame | None,
                 start: date, include_dd: bool = True) -> pd.DataFrame:
    """Place every remaining meal on a day between `start` and semester end.

    Args:
        cfg: uses `semester_start/end`, `exclude_away` + away periods, `max_meals_per_day`,
            `meal_price` and the DD rounding settings.
        me_left: meal exchanges left (fractions are dropped; negative counts as 0).
        dd_left: dining dollars left (negative counts as 0).
        profile: `forecast.weekday_profile(df, cfg)`. Only `meals_rate` is read. `None`, an
            empty frame, a frame without that column, or all-zero rates all mean "no weekday
            pattern known": every day is weighted equally.
        start: first day to plan — the day after the last observed day (`O + 1`). A `start`
            before the semester is moved up to `cfg.semester_start`.
        include_dd: True plans exchanges plus the meals dining dollars can buy
            (`cfg.round_meals(dd_left / cfg.meal_price)`); False plans exchanges only.

    Returns:
        DataFrame indexed by `datetime.date` (index name "date"), one row per day
        `start .. cfg.semester_end`, ascending, with columns:

        weekday  day name ("Monday" ..).
        away     bool — the day is skipped because it is an away day (always False when
                 `cfg.exclude_away` is off).
        weight   float — that weekday's `meals_rate` (1.0 everywhere in the uniform fallback),
                 0.0 on away days. Meals are shared in proportion to this.
        meals    int — meals planned for the day, 0 .. `cfg.max_meals_per_day`.

        `frame.attrs`:

        total        int, meals there were to place: `me_meals + dd_meals`.
        me_meals     int, `floor(me_left)`.
        dd_meals     int, whole meals from dining dollars (0 when `include_dd` is False).
        include_dd   the argument, echoed back.
        placed       int, `frame["meals"].sum()`; equals `total` unless there is a surplus.
        surplus      int, meals that fit on no day because every usable day is already at the
                     cap (`total - placed`); 0 in the normal case. A positive surplus means
                     "even eating the maximum every day, this many go unused".
        cap          int, the per-day maximum used (`cfg.max_meals_per_day`, at least 1).
        usable_days  int, non-away days in the plan.
        capacity     int, `cap * usable_days`.
        uniform      bool, True when the weekday pattern was unusable and days were weighted
                     equally.
        reason       None normally; a short string when nothing could be planned ("the
                     semester is over ..." with an empty frame, or every remaining day is an
                     away day).

    How meals are shared: each usable day's ideal share is `total * weight / sum(weights)`.
    Days get the whole part of their share, and the meals left over go one each to the days with
    the largest fractional parts (largest-remainder method), so the plan always sums exactly to
    `total`. A heavier weekday therefore never gets fewer meals than a lighter one, and two days
    of the same weekday differ by at most one meal; which of several equal days gets the extra
    meal is spread evenly through the plan rather than bunched at the start. A day whose share
    would exceed the cap is held at the cap and its overflow is re-shared among the other days
    (days of a weekday with rate 0 receive meals only through this overflow).
    """
    days, away = _remaining_days(cfg, start)
    cap = max(1, int(cfg.max_meals_per_day))

    me_meals = _whole(me_left)
    dd_meals = 0
    if include_dd and cfg.meal_price > 0:
        dd_meals = _whole(cfg.round_meals(max(0.0, dd_left) / cfg.meal_price))
    total = me_meals + dd_meals

    rates = _weekday_values(profile, "meals_rate")
    uniform = rates is None or not rates.sum() > 0
    if uniform:
        rates = np.ones(7)
    weights = np.array([rates[d.weekday()] for d in days], dtype=float)
    weights[away] = 0.0

    usable_days = int((~away).sum())
    capacity = cap * usable_days
    placed = min(total, capacity)
    meals = _allocate(weights, ~away, placed, cap)

    if not days:
        reason = "the semester is over — no days left to plan"
    elif usable_days == 0:
        reason = "every remaining day is an away day"
    else:
        reason = None

    frame = pd.DataFrame(
        {
            "weekday": pd.Series([WEEKDAY_NAMES[d.weekday()] for d in days], dtype=object),
            "away": away,
            "weight": weights,
            "meals": meals.astype(int),
        },
        columns=BUDGET_COLUMNS,
    )
    frame.index = pd.Index(days, name="date", dtype=object)
    frame.attrs.update(
        total=total, me_meals=me_meals, dd_meals=dd_meals, include_dd=bool(include_dd),
        placed=int(placed), surplus=int(total - placed), cap=cap, usable_days=usable_days,
        capacity=capacity, uniform=bool(uniform), reason=reason,
    )
    return frame


# ---------------------------------------------------------------------------
# today line
# ---------------------------------------------------------------------------


def _meals_word(count: int) -> str:
    return "meal" if count == 1 else "meals"


def _day_phrase(away: bool, meals: int, when: str, with_noun: bool) -> str:
    """One day in words, lower case: "2 meals today", "1 tomorrow", "none tomorrow", "away today"."""
    if away:
        return f"away {when}"
    if meals == 0:
        return f"no meals {when}" if with_noun else f"none {when}"
    return f"{meals} {_meals_word(meals)} {when}" if with_noun else f"{meals} {when}"


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _planned(budget: pd.DataFrame, day: date) -> tuple[bool, int] | None:
    """(away, meals) for `day`, or None when the plan has no row for it."""
    if day not in budget.index:
        return None
    row = budget.loc[day]
    return bool(row["away"]), int(row["meals"])


def today_line(budget: pd.DataFrame | None, today: date) -> str:
    """One short line about today and tomorrow, read off a `daily_budget` frame.

    No trailing period, so it can sit in a tile or be embedded in a sentence. Wording:

    * both days planned: "2 meals today, 1 tomorrow" / "1 meal today, none tomorrow" /
      "No meals today, 2 tomorrow" / "No meals today or tomorrow"
    * away days: "Away today, 2 meals tomorrow" / "2 meals today, away tomorrow" /
      "Away today and tomorrow"
    * today is the last day of the plan: "2 meals today"
    * the plan starts tomorrow (today is already covered by the data): "2 meals tomorrow"
    * the plan starts later than tomorrow: "Plan starts Fri, Dec 18: 1 meal that day"
    * no plan, or today is past its last day: "No days left to plan"

    The line reports the plan only; check `budget.attrs["surplus"]` separately if you want to
    say that some meals fit on no day.
    """
    if budget is None or budget.empty or today > budget.index[-1]:
        return "No days left to plan"

    tomorrow = today + timedelta(days=1)
    now, nxt = _planned(budget, today), _planned(budget, tomorrow)

    if now is None:
        if nxt is not None:
            return _capitalized(_day_phrase(*nxt, "tomorrow", with_noun=True))
        first = budget.index[0]
        if first < today:  # a hand-built plan with a gap; nothing sensible to say about today
            return "No days left to plan"
        away, meals = _planned(budget, first)
        what = "away that day" if away else f"{meals} {_meals_word(meals)} that day"
        return f"Plan starts {first:%a, %b} {first.day}: {what}"

    if nxt is None:
        return _capitalized(_day_phrase(*now, "today", with_noun=True))
    if now[0] and nxt[0]:
        return "Away today and tomorrow"
    if not now[0] and not nxt[0] and now[1] == 0 and nxt[1] == 0:
        return "No meals today or tomorrow"
    # "tomorrow" can drop the word "meals" only when today's half already said it.
    said_meals = not now[0]
    return _capitalized(
        f"{_day_phrase(*now, 'today', with_noun=True)}, {_day_phrase(*nxt, 'tomorrow', with_noun=not said_meals)}"
    )


# ---------------------------------------------------------------------------
# spend-down
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpendDown:
    """How fast to use each pot so both reach zero on the last usable day.

    Fields:
        usable_days: R — days from `start` through semester end, not counting away days when
            `cfg.exclude_away` is on. 0 when nothing is left to plan.
        weeks: W = R / 7.
        end: the day both pots are meant to reach zero: the last usable day (semester end
            unless the semester ends inside an away period).
        snack_reserve: dollars expected to go to snacks over the usable days — the sum of the
            profile's `dd_snack_rate` for each day's weekday. Not capped at the balance, so it
            can exceed `dd_left` (then snacks alone would use up the dining dollars).
        dd_for_meals: `max(0, dd_left - snack_reserve)` — dollars free for meals.
        dd_meals: whole meals those dollars buy, `floor(dd_for_meals / cfg.meal_price)`.
        dd_meals_per_week: `dd_meals / W`.
        exchanges_per_week: `me_left / W`.
        meals_per_day: `(me_left + dd_meals) / R` — exchanges and dining-dollar meals together.
        dd_unallocated: dollars left over after those whole meals (`dd_for_meals` minus their
            cost) — the slack that is neither a planned meal nor reserved for snacks.
        sentence: the advice in plain English, always displayable (contains "$" amounts in
            some cases — escape before putting it in Streamlit markdown).
        reason: None normally. When `usable_days` is 0 it says why ("the semester is over ...",
            or every remaining day is an away day) and every numeric field from `snack_reserve`
            to `dd_unallocated` is None.

    Negative balances are treated as 0 throughout.
    """

    usable_days: int
    weeks: float
    end: date
    snack_reserve: float | None
    dd_for_meals: float | None
    dd_meals: int | None
    dd_meals_per_week: float | None
    exchanges_per_week: float | None
    meals_per_day: float | None
    dd_unallocated: float | None
    sentence: str
    reason: str | None


def _about_dollars(amount: float) -> str:
    """Whole dollars for estimates: 13.6 -> "$14"."""
    return f"${amount:,.0f}"


def _dollars(amount: float) -> str:
    """An exact balance: "$80" when whole, "$4.50" otherwise."""
    return f"${amount:,.0f}" if abs(amount - round(amount)) < 0.005 else f"${amount:,.2f}"


def _number(x: float) -> str:
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}"


def _pace_phrase(total: float, usable_days: int, singular: str, plural: str) -> str:
    """How fast to use `total` items: "about 10 exchanges a week", "about 1 meal every 3 weeks".

    In the last few days (`usable_days < SHORT_HORIZON_DAYS`) it is just the total ("6
    exchanges"); the caller adds "over the last 3 days".
    """
    if usable_days < SHORT_HORIZON_DAYS:
        return f"{_number(total)} {singular if _number(total) == '1' else plural}"
    per_week = total * 7 / usable_days
    if per_week >= MIN_WEEKLY_RATE:
        count = max(1, math.floor(per_week + 0.5))
        return f"about {count} {singular if count == 1 else plural} a week"
    every = max(2, math.floor(1 / per_week + 0.5))
    return f"about 1 {singular} every {every} weeks"


def _sentence(cfg: Config, me_left: float, dd_left: float, snack_reserve: float, dd_for_meals: float,
              dd_meals: int, usable_days: int, end: date) -> str:
    """The spend-down advice; see `spend_down` for the cases."""
    day = f"{end:%b} {end.day}"
    if usable_days >= SHORT_HORIZON_DAYS:
        span = ""
    elif usable_days == 1:
        span = " on the last day"
    else:
        span = f" over the last {usable_days} days"
    exchanges = _pace_phrase(me_left, usable_days, "exchange", "exchanges") if me_left > 0 else None
    snacks_matter = snack_reserve >= SNACK_MENTION_DOLLARS

    if dd_meals > 0:
        meals = _pace_phrase(dd_meals, usable_days, "meal", "meals")
        if exchanges:
            text = f"Buy {meals} with dining dollars and use {exchanges}{span} — both reach zero on {day}."
        else:
            text = f"No exchanges left — buy {meals} with dining dollars{span} to reach zero on {day}."
        if snacks_matter:
            text += f" About {_about_dollars(snack_reserve)} is set aside for snacks."
        return text

    # No dining-dollar meals to plan: say why, then fall back to exchanges alone.
    if dd_left <= 0:
        if exchanges:
            return f"No dining dollars left — use {exchanges}{span} to reach zero on {day}."
        return "No exchanges or dining dollars left."
    if snack_reserve >= dd_left:
        why = (f"snacks alone would use up your dining dollars "
               f"(about {_about_dollars(snack_reserve)} expected, {_dollars(dd_left)} left)")
    elif snacks_matter:
        why = f"after about {_about_dollars(snack_reserve)} for snacks there isn't a full meal of dining dollars left"
    else:
        why = f"only {_dollars(dd_left)} in dining dollars left, not enough for a meal"
    if exchanges:
        return f"{_capitalized(why)} — use {exchanges}{span} to reach zero on {day}."
    return f"No exchanges left, and {why}."


def spend_down(cfg: Config, me_left: float, dd_left: float, profile: pd.DataFrame | None,
               start: date) -> SpendDown:
    """Work out the pace that empties both pots exactly on the last usable day.

    Args:
        cfg: uses `semester_start/end`, `exclude_away` + away periods and `meal_price`.
        me_left: meal exchanges left.
        dd_left: dining dollars left.
        profile: `forecast.weekday_profile(df, cfg)`. Only `dd_snack_rate` (expected snack
            dollars per day, by weekday) is read; without it nothing is reserved for snacks.
        start: first day to plan (`O + 1`); moved up to `cfg.semester_start` if earlier.

    Dining dollars are split in two: first the snack money the weekday pattern predicts for the
    remaining days (`snack_reserve`), then whole meals at `cfg.meal_price` from the rest. Whole
    meals are always rounded down here (`cfg.rounding_mode` is not used: you cannot buy part of
    a meal). See `SpendDown` for every field.

    `sentence` covers these cases:

    * both pots usable — "Buy about 2 meals a week with dining dollars and use about 10
      exchanges a week — both reach zero on Dec 18." plus " About $14 is set aside for snacks."
      when snacks are expected. A pace under roughly one a week reads "about 1 meal every 3
      weeks". With fewer than 7 usable days it gives totals: "Buy 1 meal with dining dollars
      and use 6 exchanges over the last 3 days — both reach zero on Dec 18."
    * no dining dollars — "No dining dollars left — use about 10 exchanges a week to reach
      zero on Dec 18."
    * snacks would eat all the dining dollars — "Snacks alone would use up your dining dollars
      (about $140 expected, $80 left) — use about 10 exchanges a week to reach zero on Dec 18."
    * less than one meal left after snacks — "After about $14 for snacks there isn't a full
      meal of dining dollars left — use ..." (or "Only $4.50 in dining dollars left, not enough
      for a meal — use ..." when no snacks are expected).
    * no exchanges — "No exchanges left — buy about 2 meals a week with dining dollars to
      reach zero on Dec 18.", or "No exchanges left, and <the dining-dollar reason>." when
      there are no dining-dollar meals either; "No exchanges or dining dollars left." when
      both pots are empty.
    * nothing to plan (`reason` set) — "The semester is over — nothing left to spread out."
    """
    days, away = _remaining_days(cfg, start)
    usable = [d for d, is_away in zip(days, away) if not is_away]
    if not usable:
        if not days:
            reason = "the semester is over — no days left to plan"
            sentence = "The semester is over — nothing left to spread out."
        else:
            reason = "every remaining day is an away day"
            sentence = "Every remaining day is an away day — nothing to spread out."
        return SpendDown(0, 0.0, cfg.semester_end, None, None, None, None, None, None, None, sentence, reason)

    usable_days = len(usable)
    weeks = usable_days / 7
    me = max(0.0, float(me_left))
    dd = max(0.0, float(dd_left))

    snack_rates = _weekday_values(profile, "dd_snack_rate")
    snack_reserve = float(sum(snack_rates[d.weekday()] for d in usable)) if snack_rates is not None else 0.0
    dd_for_meals = max(0.0, dd - snack_reserve)
    dd_meals = _whole(dd_for_meals / cfg.meal_price) if cfg.meal_price > 0 else 0
    dd_unallocated = max(0.0, dd_for_meals - dd_meals * cfg.meal_price)

    return SpendDown(
        usable_days=usable_days,
        weeks=weeks,
        end=usable[-1],
        snack_reserve=snack_reserve,
        dd_for_meals=dd_for_meals,
        dd_meals=dd_meals,
        dd_meals_per_week=dd_meals / weeks,
        exchanges_per_week=me / weeks,
        meals_per_day=(me + dd_meals) / usable_days,
        dd_unallocated=dd_unallocated,
        sentence=_sentence(cfg, me, dd, snack_reserve, dd_for_meals, dd_meals, usable_days, usable[-1]),
        reason=None,
    )
