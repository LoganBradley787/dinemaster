"""Pure metric computations over an ingested transactions DataFrame + Config.

No Streamlit imports; nothing here reads or writes files. Every function takes
a `Config` (see `dinemaster.config.load_config`) and, where relevant, the
DataFrame produced by `dinemaster.ingest.load_transactions` (columns:
`account, pot ('ME'|'DD'), timestamp (datetime64), date (datetime.date),
description, amount (float, signed), balance (float), kind
('usage'|'reversal'|'load'|'adjustment'), dd_class ('meal'|'snack'|None)`).

Day-counting vocabulary (see spec "Day counting"), all `datetime.date`:
    S, E          semester start/end (inclusive).
    A             as-of date, clamped to [S-1, E]. A == S-1 means "before the
                  semester started, nothing elapsed yet".
    T             total days in the semester, (E-S)+1.
    De            elapsed days (through A, inclusive), (A-S)+1.
    Dr            remaining days (after A, through E), E-A.
    away_*        enabled AwayPeriod days counted in a window; the "_away"
                  suffixed fields (t_away/de_away/dr_away) are the "usable"
                  variants with away days subtracted out.

Every metric that depends on the number of days remaining/elapsed is computed
twice: once treating away periods as ordinary days ("plain") and once with
enabled away periods excluded from the day counts and skipped when walking
forward day-by-day ("away"). `compute_metrics` always returns both; the UI
decides which to emphasize based on `cfg.exclude_away`. The `apply_away`
boolean parameter on the lower-level helpers selects which behavior to use.

"Not enough data" / "runs past the end of the semester" results are
represented with `None` fields (never NaN/inf), paired with a short
human-readable `reason` string where useful.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from dinemaster.config import Config

# small shared helpers
def _date_range(start: date, end: date) -> list[date]:
    """Every date from `start` to `end`, inclusive. Empty list if start > end."""
    if start > end:
        return []
    n = (end - start).days
    return [start + timedelta(days=i) for i in range(n + 1)]


def clamp_as_of(cfg: Config) -> date:
    """The as-of date used everywhere else, clamped to [S-1, E].

    S-1 represents "before the semester started" (De=0, Dr=T). Values beyond
    E clamp to E (everything elapsed).
    """
    lo = cfg.semester_start - timedelta(days=1)
    hi = cfg.semester_end
    return max(lo, min(cfg.as_of, hi))


# Day counting
@dataclass(frozen=True)
class DayCounts:
    """Calendar bookkeeping for the semester, evaluated at the clamped as-of date.

    Fields:
        s, e: semester start/end (inclusive).
        as_of_raw: cfg.as_of before clamping (for display of the raw input).
        a: as-of date after clamping to [s-1, e]. This is "A" in the spec.
        t, de, dr: total / elapsed / remaining days (see module docstring).
        away_tot, away_el, away_rem: enabled away-period days in [s,e],
            [s,a], and (a,e] respectively.
        t_away, de_away, dr_away: the "usable" variants with away days
            removed (t-away_tot, de-away_el, dr-away_rem).
        frac_remaining, frac_remaining_away: dr/t and dr_away/t_away (None if
            the denominator is 0).
        progress: de/t, calendar progress through the semester (0..1).
        notice: human-readable note when the configured as-of fell outside
            [s-1, e] and had to be clamped; None otherwise.
    """

    s: date
    e: date
    as_of_raw: date
    a: date
    t: int
    de: int
    dr: int
    away_tot: int
    away_el: int
    away_rem: int
    t_away: int
    de_away: int
    dr_away: int
    frac_remaining: float | None
    frac_remaining_away: float | None
    progress: float
    notice: str | None


def day_counts(cfg: Config) -> DayCounts:
    """Compute `DayCounts` for `cfg.as_of` against `cfg.semester_start/end`."""
    s, e = cfg.semester_start, cfg.semester_end
    a = clamp_as_of(cfg)

    notice = None
    if cfg.as_of < s - timedelta(days=1):
        notice = f"As-of date {cfg.as_of} is before the semester starts; showing day 0."
    elif cfg.as_of > e:
        notice = f"As-of date {cfg.as_of} is after the semester ends; clamped to {e}."

    t = (e - s).days + 1
    de = (a - s).days + 1
    dr = (e - a).days

    away_tot = sum(1 for d in _date_range(s, e) if cfg.is_away(d))
    away_el = sum(1 for d in _date_range(s, a) if cfg.is_away(d)) if a >= s else 0
    away_rem = sum(1 for d in _date_range(a + timedelta(days=1), e) if cfg.is_away(d))

    t_away = t - away_tot
    de_away = de - away_el
    dr_away = dr - away_rem

    frac_remaining = dr / t if t > 0 else None
    frac_remaining_away = dr_away / t_away if t_away > 0 else None
    progress = de / t if t > 0 else 0.0

    return DayCounts(
        s=s, e=e, as_of_raw=cfg.as_of, a=a, t=t, de=de, dr=dr,
        away_tot=away_tot, away_el=away_el, away_rem=away_rem,
        t_away=t_away, de_away=de_away, dr_away=dr_away,
        frac_remaining=frac_remaining, frac_remaining_away=frac_remaining_away,
        progress=progress, notice=notice,
    )


# Usage & balances
@dataclass(frozen=True)
class Balances:
    """Usage/balance summary over the window `data_cutoff <= date <= A`.

    Fields:
        me_used, dd_used: dollars/meals consumed this window (positive
            numbers), i.e. -sum(amount) over kind in {usage, reversal}.
        me_left, dd_left: starting_me/dd minus the above; these drive every
            other metric and chart.
        me_deposits, dd_deposits: sum of kind == 'load' amounts in-window.
        me_derived_balance, dd_derived_balance: sum, over every account in
            that pot seen anywhere in the ledger, of the account's last
            `balance` on or before A (accounts with no rows in-window still
            contribute their last known balance). None if the pot has no
            rows at all.
        me_warning, dd_warning: human-readable reconciliation warning, or
            None. Raised when |derived - configured left| > 0.005, or when
            deposits-in-window don't match the configured starting amount.
            These are surfaced, never silently resolved.
    """

    me_used: float
    dd_used: float
    me_left: float
    dd_left: float
    me_deposits: float
    dd_deposits: float
    me_derived_balance: float | None
    dd_derived_balance: float | None
    me_warning: str | None
    dd_warning: str | None


def _pot_usage(df: pd.DataFrame, pot: str, cutoff: date, a: date) -> float:
    if df.empty:
        return 0.0
    mask = (
        (df["pot"] == pot)
        & (df["kind"].isin(["usage", "reversal"]))
        & (df["date"] >= cutoff)
        & (df["date"] <= a)
    )
    return float(-df.loc[mask, "amount"].sum())


def _pot_deposits(df: pd.DataFrame, pot: str, cutoff: date, a: date) -> float:
    if df.empty:
        return 0.0
    mask = (df["pot"] == pot) & (df["kind"] == "load") & (df["date"] >= cutoff) & (df["date"] <= a)
    return float(df.loc[mask, "amount"].sum())


def _pot_derived_balance(df: pd.DataFrame, pot: str, a: date) -> float | None:
    if df.empty:
        return None
    sub = df[(df["pot"] == pot) & (df["date"] <= a)]
    if sub.empty:
        return None
    last_per_account = sub.sort_values("timestamp").groupby("account").tail(1)
    return float(last_per_account["balance"].sum())


def _reconcile(derived: float | None, configured_left: float, deposits: float, starting_amount: float) -> str | None:
    problems = []
    if derived is not None and abs(derived - configured_left) > 0.005:
        problems.append(f"ledger balance ({derived:.2f}) doesn't match computed left ({configured_left:.2f})")
    if abs(deposits - starting_amount) > 0.005:
        problems.append(f"deposits in window ({deposits:.2f}) don't match configured starting amount ({starting_amount:.2f})")
    return "; ".join(problems) if problems else None


def balances(df: pd.DataFrame, cfg: Config) -> Balances:
    """Compute `Balances` over the window `cfg.data_cutoff <= date <= clamp_as_of(cfg)`."""
    a = clamp_as_of(cfg)
    cutoff = cfg.data_cutoff

    me_used = _pot_usage(df, "ME", cutoff, a)
    dd_used = _pot_usage(df, "DD", cutoff, a)
    me_left = cfg.starting_me - me_used
    dd_left = cfg.starting_dd - dd_used

    me_deposits = _pot_deposits(df, "ME", cutoff, a)
    dd_deposits = _pot_deposits(df, "DD", cutoff, a)

    me_derived = _pot_derived_balance(df, "ME", a)
    dd_derived = _pot_derived_balance(df, "DD", a)

    # Only reconcile a pot that actually has ledger rows; a pot with zero
    # rows (e.g. not yet ingested) has nothing to compare against.
    me_warning = _reconcile(me_derived, me_left, me_deposits, cfg.starting_me) if me_derived is not None else None
    dd_warning = _reconcile(dd_derived, dd_left, dd_deposits, cfg.starting_dd) if dd_derived is not None else None

    return Balances(
        me_used=me_used, dd_used=dd_used, me_left=me_left, dd_left=dd_left,
        me_deposits=me_deposits, dd_deposits=dd_deposits,
        me_derived_balance=me_derived, dd_derived_balance=dd_derived,
        me_warning=me_warning, dd_warning=dd_warning,
    )


# DD purchase grouping (split-tender dedup) & breakdown
def _dd_purchases(df: pd.DataFrame) -> pd.DataFrame:
    """Dedupe DD `usage` rows into one row per (timestamp, description) purchase.

    Split-tender purchases (paid from more than one DD account) appear as
    multiple ledger rows sharing a timestamp+description; this sums their
    amounts into a single purchase row so meal/snack *counts* aren't
    double-counted. (Dollar totals don't need this — summing every row
    already gives the correct total regardless of how many accounts paid.)
    """
    if df.empty:
        return df.assign(**{c: [] for c in ["timestamp", "date", "description", "amount", "dd_class"]})
    dd_usage = df[(df["pot"] == "DD") & (df["kind"] == "usage")]
    if dd_usage.empty:
        return dd_usage
    grouped = (
        dd_usage.groupby(["timestamp", "description"], as_index=False)
        .agg(amount=("amount", "sum"), date=("date", "first"), dd_class=("dd_class", "first"))
    )
    return grouped


@dataclass(frozen=True)
class DDBreakdown:
    """Dining-dollar usage broken down by meal vs. snack, over the usage window.

    Fields:
        meal_count, meal_amount: number of distinct meal purchases and their
            total dollar amount (positive number).
        snack_count, snack_amount: same, for snacks.
        by_location: {description: {"meal_count", "meal_amount",
            "snack_count", "snack_amount"}} — the same split per location,
            since one location can produce both meal- and snack-sized
            purchases.
    """

    meal_count: int
    meal_amount: float
    snack_count: int
    snack_amount: float
    by_location: dict[str, dict[str, float]]


def dd_breakdown(df: pd.DataFrame, cfg: Config) -> DDBreakdown:
    """Meal/snack breakdown of DD usage in the window `data_cutoff <= date <= A`."""
    a = clamp_as_of(cfg)
    if df.empty:
        return DDBreakdown(0, 0.0, 0, 0.0, {})
    windowed = df[(df["date"] >= cfg.data_cutoff) & (df["date"] <= a)]
    purchases = _dd_purchases(windowed)
    if purchases.empty:
        return DDBreakdown(0, 0.0, 0, 0.0, {})

    meals = purchases[purchases["dd_class"] == "meal"]
    snacks = purchases[purchases["dd_class"] == "snack"]

    by_location: dict[str, dict[str, float]] = {}
    for desc, group in purchases.groupby("description"):
        g_meals = group[group["dd_class"] == "meal"]
        g_snacks = group[group["dd_class"] == "snack"]
        by_location[desc] = {
            "meal_count": int(len(g_meals)),
            "meal_amount": float(-g_meals["amount"].sum()),
            "snack_count": int(len(g_snacks)),
            "snack_amount": float(-g_snacks["amount"].sum()),
        }

    return DDBreakdown(
        meal_count=int(len(meals)),
        meal_amount=float(-meals["amount"].sum()),
        snack_count=int(len(snacks)),
        snack_amount=float(-snacks["amount"].sum()),
        by_location=by_location,
    )


# daily() — per-day table for charts
def daily(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Per-day table indexed by every date from S to A (both inclusive).

    Columns:
        me_swipes: net ME swipes that day (usage+reversal amounts negated;
            a same-day reversal cancels out a swipe).
        dd_meals, dd_snacks: count of distinct DD purchases that day
            classified meal/snack (split-tender purchases counted once).
        dd_spend: total DD dollars spent that day (usage+reversal, positive).
        meals: me_swipes + dd_meals (the spec's "daily meals" figure).
        me_bal, dd_bal: end-of-day balance for that pot, starting from
            cfg.starting_me/dd and subtracting cumulative net usage since
            cfg.data_cutoff (loads/adjustments are excluded, so there's no
            deposit spike in this series).
    """
    s, a = cfg.semester_start, clamp_as_of(cfg)
    idx = _date_range(s, a)
    out = pd.DataFrame(
        index=pd.Index(idx, name="date"),
        columns=["me_swipes", "dd_meals", "dd_snacks", "dd_spend", "meals", "me_bal", "dd_bal"],
        dtype=float,
    )

    if df.empty:
        me_by_day: dict[date, float] = {}
        dd_spend_by_day: dict[date, float] = {}
        purchases = pd.DataFrame(columns=["date", "dd_class"])
    else:
        me_usage = df[(df["pot"] == "ME") & (df["kind"].isin(["usage", "reversal"]))]
        me_by_day = (-me_usage.groupby("date")["amount"].sum()).to_dict()

        dd_usage_rev = df[(df["pot"] == "DD") & (df["kind"].isin(["usage", "reversal"]))]
        dd_spend_by_day = (-dd_usage_rev.groupby("date")["amount"].sum()).to_dict()

        purchases = _dd_purchases(df[df["pot"] == "DD"])

    dd_meals_by_day = purchases[purchases["dd_class"] == "meal"].groupby("date").size().to_dict() if not purchases.empty else {}
    dd_snacks_by_day = purchases[purchases["dd_class"] == "snack"].groupby("date").size().to_dict() if not purchases.empty else {}

    me_running = cfg.starting_me
    dd_running = cfg.starting_dd
    cutoff = cfg.data_cutoff
    for d in idx:
        if d >= cutoff:
            me_running -= me_by_day.get(d, 0.0)
            dd_running -= dd_spend_by_day.get(d, 0.0)
        swipes = me_by_day.get(d, 0.0)
        meals_ct = dd_meals_by_day.get(d, 0)
        snacks_ct = dd_snacks_by_day.get(d, 0)
        out.loc[d, "me_swipes"] = swipes
        out.loc[d, "dd_meals"] = meals_ct
        out.loc[d, "dd_snacks"] = snacks_ct
        out.loc[d, "dd_spend"] = dd_spend_by_day.get(d, 0.0)
        out.loc[d, "meals"] = swipes + meals_ct
        out.loc[d, "me_bal"] = me_running
        out.loc[d, "dd_bal"] = dd_running

    return out


# Series helpers for charts
def ideal_series(cfg: Config, start_amount: float, apply_away: bool) -> pd.Series:
    """Straight-line ideal balance from `start_amount` (at S) down to 0 (at E).

    Indexed by every date S..E. Value at date d is
    `start_amount * counted_days_after(d) / counted_total_days`, where
    "counted" days exclude enabled away periods when `apply_away` is True.
    This makes the line flat across (and into the start of) an away period
    and guarantees the value is exactly 0 at E. The value at S is one day's
    decrement below `start_amount` (matches the Dr convention used
    elsewhere: "remaining" excludes the reference day itself).
    """
    s, e = cfg.semester_start, cfg.semester_end
    idx = _date_range(s, e)

    def counted(d: date) -> bool:
        return not (apply_away and cfg.is_away(d))

    total = sum(1 for d in idx if counted(d))
    if total <= 0:
        return pd.Series([0.0] * len(idx), index=pd.Index(idx, name="date"))

    # suffix count of counted days strictly after each date
    suffix = [0] * (len(idx) + 1)
    for i in range(len(idx) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + (1 if counted(idx[i]) else 0)
    values = [start_amount * suffix[i + 1] / total for i in range(len(idx))]
    return pd.Series(values, index=pd.Index(idx, name="date"))


def projection_series(cfg: Config, a: date, left: float, rate: float, apply_away: bool) -> pd.Series:
    """Dashed burn-rate projection from A to min(run-out date, E).

    Value at A is `left`; each subsequent counted day subtracts `rate`
    (away days are skipped, i.e. held flat, when `apply_away`). Stops at the
    day the balance would reach 0, or at E if it never does. Never returns a
    negative value.
    """
    e = cfg.semester_end
    idx = [a]
    values = [max(left, 0.0)]
    balance = left
    day = a + timedelta(days=1)
    while day <= e:
        if apply_away and cfg.is_away(day):
            idx.append(day)
            values.append(max(balance, 0.0))
            day += timedelta(days=1)
            continue
        balance -= rate
        idx.append(day)
        values.append(max(balance, 0.0))
        if balance <= 0:
            break
        day += timedelta(days=1)
    return pd.Series(values, index=pd.Index(idx, name="date"))


# Metric 3/5 support: run-out at a fixed 2/day pace
@dataclass(frozen=True)
class RunOut:
    """Result of walking forward at a fixed 2-meals/day pace.

    Fields:
        date: last fully-covered day (None if fewer than 2 units are
            available to start with).
        spare_days: date - semester_end, in days (negative means running out
            before the semester ends). None if date is None.
        reason: human-readable note when date is None.
    """

    date: date | None
    spare_days: float | None
    reason: str | None


def run_out_two_per_day(cfg: Config, a: date, available: float, apply_away: bool) -> RunOut:
    """Walk forward from `a + 1 day`, consuming 2 units/day while >= 2 remain.

    Away days are skipped entirely (no consumption, no effect on the count)
    when `apply_away` is True. The walk isn't bounded by semester_end — a
    date past E is a valid answer (it just means you'd run out later than
    the plan needs, i.e. `spare_days` is positive).
    """
    remaining = available
    if remaining < 2:
        return RunOut(None, None, "already below 2 units; out at this pace")
    day = a + timedelta(days=1)
    last_covered = None
    while remaining >= 2:
        if apply_away and cfg.is_away(day):
            day += timedelta(days=1)
            continue
        remaining -= 2
        last_covered = day
        day += timedelta(days=1)
    spare = (last_covered - cfg.semester_end).days
    return RunOut(last_covered, spare, None)


# Metric 6: burn rate & projected run-out
def burn_rate(used: float, elapsed_days: int) -> float | None:
    """Average units/day consumed so far, or None if there isn't enough signal.

    None (rather than 0 or inf) when `used <= 0` or `elapsed_days <= 0` —
    the spec's "not enough data" case.
    """
    if used <= 0 or elapsed_days <= 0:
        return None
    return used / elapsed_days


@dataclass(frozen=True)
class BurnResult:
    """Projected run-out at a pot's historical burn rate.

    Fields:
        rate: units/day used so far (None if `burn_rate` returned None).
        runout_date: the day the balance is projected to cross 0, walking
            forward from A+1 (skipping away days when `apply_away`), possibly
            after E. None if `rate` is None or 0.
        spare_days: fractional usable days of supply beyond the remaining
            usable days (left/rate - dr_eff); negative = runs out early.
            None if runout_date is None.
        lasts_past_end: True if runout_date is after E (or rate is 0).
        leftover_raw: left - rate * dr_eff (can be negative — a projected
            shortfall). None if rate is None.
        leftover_display: max(leftover_raw, 0) — what to show as "left over
            at semester end". None if rate is None.
        shortfall: max(-leftover_raw, 0) — how far short you're projected to
            come up, if at all. None if rate is None.
        reason: "not enough data" when rate is None; None otherwise.
        leftover_note: optional extra message about unspent DD (rollover /
            forfeiture warning); set by `compute_metrics`, None for ME and
            for pots below `cfg.dd_leftover_flag`.
    """

    rate: float | None
    runout_date: date | None
    spare_days: float | None
    lasts_past_end: bool
    leftover_raw: float | None
    leftover_display: float | None
    shortfall: float | None
    reason: str | None
    leftover_note: str | None = None


def burn_projection(cfg: Config, a: date, rate: float | None, left: float, dr_eff: int, apply_away: bool) -> BurnResult:
    """Project a pot's run-out date and semester-end leftover at a fixed daily `rate`."""
    if rate is None:
        return BurnResult(None, None, None, False, None, None, None, "not enough data")

    e = cfg.semester_end
    # Walk usable days until the balance crosses zero. Continues past E (away periods
    # only exist inside the semester) so a surplus still yields a date and spare days.
    balance = left
    runout = a
    if left > 0 and rate > 0:
        day = a + timedelta(days=1)
        while balance > 0:
            if not (apply_away and cfg.is_away(day)):
                balance -= rate
                runout = day
            day += timedelta(days=1)

    leftover_raw = left - rate * dr_eff
    leftover_display = max(leftover_raw, 0.0)
    shortfall = max(-leftover_raw, 0.0)
    if rate <= 0:
        return BurnResult(rate, None, None, True, leftover_raw, leftover_display, shortfall, None)
    # Fractional usable days the balance covers beyond (or short of) the remaining usable days.
    spare = left / rate - dr_eff
    return BurnResult(rate, runout, spare, runout > e, leftover_raw, leftover_display, shortfall, None)


# Metric 4: day mix
@dataclass(frozen=True)
class DayMix:
    """How to spread `M` meals over `R` remaining days as 1s and 2s.

    Fields:
        one_meal_days, two_meal_days: clamp(2R-M, 0, R) and R minus that.
        one_per_week, two_per_week: the same counts scaled to a 7-day week
            (count * 7 / R). None if R <= 0.
        note: "surplus: ..." when M > 2R (you'd need some 3-meal days to use
            it all), "shortfall: ..." when M < R (below a 1/day pace),
            "semester over" when R <= 0, else None.
    """

    one_meal_days: float
    two_meal_days: float
    one_per_week: float | None
    two_per_week: float | None
    note: str | None


def day_mix(meals: float, days: float) -> DayMix:
    """Decompose `meals` available over `days` remaining into 1-/2-meal days."""
    if days <= 0:
        return DayMix(0.0, 0.0, None, None, "semester over")
    one = min(max(2 * days - meals, 0.0), days)
    two = days - one
    note = None
    if meals > 2 * days:
        note = "surplus: exceeds a 2/day pace — you'd need some 3-meal days to use it all"
    elif meals < days:
        note = "shortfall: below a 1/day pace at this rate"
    return DayMix(one, two, one * 7 / days, two * 7 / days, note)


# What-if
@dataclass(frozen=True)
class WhatIfResult:
    """Projected end-of-semester balances for a hypothetical meals/day rate.

    Consumption draws from ME first, then DD (at `cfg.meal_price` per meal)
    once ME is exhausted, walking forward from A+1 through E (away days
    skipped when `apply_away`).

    Fields:
        me_end, dd_end: projected balance at E (floored at 0).
        me_runout, dd_runout: date each pot is projected to hit 0, or None
            if it never does within the semester.
        reason: set (and the other fields left unchanged from the inputs)
            when `meals_per_day <= 0`, since there's nothing to project.
    """

    me_end: float
    dd_end: float
    me_runout: date | None
    dd_runout: date | None
    reason: str | None


def what_if(cfg: Config, a: date, me_left: float, dd_left: float, meals_per_day: float, apply_away: bool) -> WhatIfResult:
    """Project ME/DD balances forward at a hypothetical `meals_per_day` rate."""
    if meals_per_day <= 0:
        return WhatIfResult(me_left, dd_left, None, None, "no consumption modeled at 0 meals/day")

    me, dd = me_left, dd_left
    me_runout: date | None = None
    dd_runout: date | None = None
    day = a + timedelta(days=1)
    e = cfg.semester_end
    while day <= e:
        if apply_away and cfg.is_away(day):
            day += timedelta(days=1)
            continue
        remaining = meals_per_day
        if me > 0:
            use = min(me, remaining)
            me -= use
            remaining -= use
            if me <= 0 and me_runout is None:
                me_runout = day
        if remaining > 0:
            dd -= remaining * cfg.meal_price
            if dd <= 0 and dd_runout is None:
                dd_runout = day
        day += timedelta(days=1)

    return WhatIfResult(max(me, 0.0), max(dd, 0.0), me_runout, dd_runout, None)


# Metric 2: targets
@dataclass(frozen=True)
class TargetResult:
    """Where you "should" be by now, two ways.

    Fields:
        strict_target, strict_delta: `starting_me * f`, and `me_left -
            strict_target` (positive means ahead of pace).
        pooled_total: P = starting_me + round_meals(starting_dd / price) —
            the semester's total meal-equivalents if DD were spent as meals.
        pooled_target, pooled_delta: `P * f - round_meals(dd_left / price)`
            (the ME-equivalent target after crediting DD already banked),
            and `me_left - pooled_target`.
        reason: set instead of computing anything when `f` is undefined
            (t or t_eff == 0).
    """

    strict_target: float | None
    strict_delta: float | None
    pooled_total: float | None
    pooled_target: float | None
    pooled_delta: float | None
    reason: str | None


def _targets(cfg: Config, me_left: float, dd_left: float, f: float | None) -> TargetResult:
    if f is None:
        return TargetResult(None, None, None, None, None, "semester length is zero")
    strict_target = cfg.starting_me * f
    strict_delta = me_left - strict_target
    pooled_total = cfg.starting_me + cfg.round_meals(cfg.starting_dd / cfg.meal_price)
    pooled_target = pooled_total * f - cfg.round_meals(dd_left / cfg.meal_price)
    pooled_delta = me_left - pooled_target
    return TargetResult(strict_target, strict_delta, pooled_total, pooled_target, pooled_delta, None)


# Metric 3: allowed pace
@dataclass(frozen=True)
class PaceResult:
    """Allowed meals/day to stay on pace for the rest of the semester.

    Fields:
        me_only: `me_left / Dr` (Dr or Dr_away depending on variant).
        with_dd: `(me_left + round_meals(dd_left/price)) / Dr`.
        reason: "semester over" (and both fields None) when Dr <= 0.
    """

    me_only: float | None
    with_dd: float | None
    reason: str | None


def _pace(cfg: Config, me_left: float, dd_left: float, dr_eff: int) -> PaceResult:
    if dr_eff <= 0:
        return PaceResult(None, None, "semester over")
    pooled = me_left + cfg.round_meals(dd_left / cfg.meal_price)
    return PaceResult(me_left / dr_eff, pooled / dr_eff, None)


# compute_metrics — the one-stop entry point
@dataclass(frozen=True)
class Metrics:
    """Every metric from spec §Metrics, computed for both away variants.

    Fields:
        day_counts: `DayCounts` (metric 1's De/T live here too, plus the
            away-adjusted day counts every other metric is built from).
        balances: `Balances` (me_left/dd_left and reconciliation warnings).
        progress: De/T — metric 1, calendar progress through the semester
            (0..1). The away-adjusted version is `day_counts.de_away /
            day_counts.t_away`.
        targets: {"plain": TargetResult, "away": TargetResult} — metric 2.
        pace: {"plain": PaceResult, "away": PaceResult} — metric 3, allowed
            meals/day (ME-only and pooled-with-DD).
        day_mix: {"plain"|"away": {"me_only"|"with_dd": DayMix}} — metric 4,
            using the same M (meals available) as `pace` and R = Dr(_eff).
        run_out: {"plain"|"away": {"me_only"|"with_dd": RunOut}} — metric 5,
            the 2-meals/day run-out date.
        burn: {"plain"|"away": {"me"|"dd": BurnResult}} — metric 6, burn
            rate, projected run-out, and leftover-at-E. The "dd" entries'
            `leftover_note` carries the forfeiture/rollover warning from
            `cfg.dd_leftover_flag` / `cfg.dd_rollover` when it applies.
        dd_breakdown: `DDBreakdown` — metric 7, meal/snack $ and counts by
            location. Not away-dependent (it's purely historical), so the
            same value applies regardless of which day-count variant the UI
            is showing.
    """

    day_counts: DayCounts
    balances: Balances
    progress: float
    targets: dict[str, TargetResult]
    pace: dict[str, PaceResult]
    day_mix: dict[str, dict[str, DayMix]]
    run_out: dict[str, dict[str, RunOut]]
    burn: dict[str, dict[str, BurnResult]]
    dd_breakdown: DDBreakdown


def _leftover_note(cfg: Config, leftover_display: float | None) -> str | None:
    if leftover_display is None or leftover_display < cfg.dd_leftover_flag:
        return None
    if cfg.dd_rollover:
        return f"≈${leftover_display:.0f} carries to spring"
    return f"≈${leftover_display:.0f} unspent will be lost"


def compute_metrics(df: pd.DataFrame, cfg: Config) -> Metrics:
    """Compute every metric (both away variants) for the given ledger + config."""
    dc = day_counts(cfg)
    bal = balances(df, cfg)
    breakdown = dd_breakdown(df, cfg)

    targets: dict[str, TargetResult] = {}
    pace: dict[str, PaceResult] = {}
    mix: dict[str, dict[str, DayMix]] = {}
    runout: dict[str, dict[str, RunOut]] = {}
    burn: dict[str, dict[str, BurnResult]] = {}

    for variant, apply_away in (("plain", False), ("away", True)):
        dr_eff = dc.dr_away if apply_away else dc.dr
        de_eff = dc.de_away if apply_away else dc.de
        f = dc.frac_remaining_away if apply_away else dc.frac_remaining

        targets[variant] = _targets(cfg, bal.me_left, bal.dd_left, f)
        pace[variant] = _pace(cfg, bal.me_left, bal.dd_left, dr_eff)

        pooled_meals = bal.me_left + cfg.round_meals(bal.dd_left / cfg.meal_price)
        mix[variant] = {
            "me_only": day_mix(bal.me_left, dr_eff),
            "with_dd": day_mix(pooled_meals, dr_eff),
        }
        runout[variant] = {
            "me_only": run_out_two_per_day(cfg, dc.a, bal.me_left, apply_away),
            "with_dd": run_out_two_per_day(cfg, dc.a, pooled_meals, apply_away),
        }

        me_rate = burn_rate(bal.me_used, de_eff)
        dd_rate = burn_rate(bal.dd_used, de_eff)
        me_burn = burn_projection(cfg, dc.a, me_rate, bal.me_left, dr_eff, apply_away)
        dd_burn = burn_projection(cfg, dc.a, dd_rate, bal.dd_left, dr_eff, apply_away)
        note = _leftover_note(cfg, dd_burn.leftover_display)
        if note is not None:
            dd_burn = dataclasses.replace(dd_burn, leftover_note=note)
        burn[variant] = {"me": me_burn, "dd": dd_burn}

    return Metrics(
        day_counts=dc,
        balances=bal,
        progress=dc.progress,
        targets=targets,
        pace=pace,
        day_mix=mix,
        run_out=runout,
        burn=burn,
        dd_breakdown=breakdown,
    )
