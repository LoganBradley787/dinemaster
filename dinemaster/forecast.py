"""Day-of-week-aware forecast of the two pots (meal exchanges and dining dollars).

Pure functions over the ingest DataFrame + `Config`; no Streamlit, no file writes. Figures for
these results live in `dinemaster.forecast_charts`.

The idea in three steps
-----------------------
1. **History** (`observed_days`): one row per *observed* day. A day is observed when it lies in
   `max(S, data_cutoff) .. O`, where `O = freshness.observed_through(cfg, df)` is the last day the
   exports actually cover. Days after `O` are unknown, so they are forecast rather than counted as
   zero-usage days. With `cfg.exclude_away` on, away days are dropped from the history.
2. **Weekday profile** (`weekday_profile`): for each weekday (Monday=0 .. Sunday=6) a plain
   average of what happened, and a *forecast rate*: the recency-weighted mean of that weekday's
   days, shrunk toward the overall weighted daily mean. The weight of a day is
   `0.5 ** (age_days / cfg.forecast.half_life_days)` with age counted back from `O`; shrinkage is
   `(n * weekday_mean + prior * overall_mean) / (n + prior)` with `n` the number of observed days
   of that weekday and `prior = cfg.forecast.prior_days`.
3. **Simulation** (`forecast`): every future day (`O+1 .. E`) draws its usage by bootstrap from
   the history: with probability `n / (n + prior)` from the same weekday's days, otherwise from
   all observed days, both recency-weighted. The mean of that draw is exactly the forecast rate
   above, so the `expected` line and the simulated band agree. Away days draw nothing when
   `cfg.exclude_away` is on. `cfg.forecast.simulations` runs, seeded by `cfg.forecast.seed`, so the
   same inputs always give the same numbers.

Units: the ME pot is in swipes (meals); the DD pot is in dollars (meal and snack purchases, net
of refunds). `dd_snack_*` columns are **dollars**, `dd_meal*` columns are purchase **counts**.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from dinemaster import metrics
from dinemaster.config import Config
from dinemaster.freshness import observed_through

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: Columns of `observed_days`.
HISTORY_COLUMNS = ["weekday", "me", "dd_meals", "dd_spend", "dd_snack", "meals", "weight"]

#: Columns of `weekday_profile`, in order.
PROFILE_COLUMNS = [
    "weekday", "days",
    "meals_avg", "me_avg", "dd_meals_avg", "dd_spend_avg", "dd_snack_avg",
    "me_rate", "dd_meal_rate", "dd_spend_rate", "dd_snack_rate", "meals_rate",
    "share",
]

# profile column -> history column it summarizes
_AVG_SOURCES = {
    "meals_avg": "meals", "me_avg": "me", "dd_meals_avg": "dd_meals",
    "dd_spend_avg": "dd_spend", "dd_snack_avg": "dd_snack",
}
_RATE_SOURCES = {
    "me_rate": "me", "dd_meal_rate": "dd_meals", "dd_spend_rate": "dd_spend",
    "dd_snack_rate": "dd_snack", "meals_rate": "meals",
}

#: Run-out dates are only reported when at least this share of simulations runs out by semester end.
RUNOUT_MIN_SHARE = 0.05

#: Balances at or below this count as "hit zero" (absorbs float noise from summing cents).
_ZERO = 1e-6

_DEFAULT_BAND = (10, 90)


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def _empty_history() -> pd.DataFrame:
    frame = pd.DataFrame({c: pd.Series(dtype=float) for c in HISTORY_COLUMNS}, index=pd.Index([], name="date"))
    return frame.astype({"weekday": int})


def _recency_weights(ages_days: np.ndarray, half_life_days: float) -> np.ndarray:
    """`0.5 ** (age / half_life)`; all ones when the half-life is not positive (recency off)."""
    if half_life_days <= 0:
        return np.ones(len(ages_days))
    return 0.5 ** (ages_days / half_life_days)


def _probabilities(weights: np.ndarray) -> np.ndarray:
    """Weights scaled to sum to 1; uniform if they all underflowed to zero."""
    total = weights.sum()
    if not total > 0:
        return np.full(len(weights), 1.0 / len(weights))
    return weights / total


def observed_days(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """The history the forecast learns from: one row per observed (and non-away) day.

    Index: `datetime.date`, ascending, covering `max(S, cfg.data_cutoff) .. O` where
    `O = observed_through(cfg, df)`. When `cfg.exclude_away` is on, away days are left out.
    Days in the window with no transactions are real zero-usage days and are included.

    Columns:
        weekday   int, Monday=0 .. Sunday=6.
        me        net ME swipes that day (reversals cancel swipes).
        dd_meals  number of DD purchases classified as meals (split tender counted once).
        dd_spend  DD dollars spent that day, meals + snacks, net of refunds.
        dd_snack  DD dollars spent on snack purchases that day.
        meals     me + dd_meals.
        weight    recency weight `0.5 ** (age_days / half_life_days)`, age counted back from O
                  (so the day O itself has weight 1).

    Returns an empty frame (same columns) when `df` is empty, the semester has not started, or
    every observed day is an away day.
    """
    if df.empty:
        return _empty_history()
    o = observed_through(cfg, df)
    start = max(cfg.semester_start, cfg.data_cutoff)
    if o < start:
        return _empty_history()

    day = metrics.daily(df, replace(cfg, as_of=o))  # indexed S..O
    keep = [d >= start and not (cfg.exclude_away and cfg.is_away(d)) for d in day.index]
    day = day[keep]
    if day.empty:
        return _empty_history()

    snack_rows = df[(df["pot"] == "DD") & (df["kind"] == "usage") & (df["dd_class"] == "snack")]
    snack_dollars = (-snack_rows.groupby("date")["amount"].sum()).to_dict()

    ages = np.array([(o - d).days for d in day.index], dtype=float)
    return pd.DataFrame(
        {
            "weekday": [d.weekday() for d in day.index],
            "me": day["me_swipes"].to_numpy(float),
            "dd_meals": day["dd_meals"].to_numpy(float),
            "dd_spend": day["dd_spend"].to_numpy(float),
            "dd_snack": [float(snack_dollars.get(d, 0.0)) for d in day.index],
            "meals": day["meals"].to_numpy(float),
            "weight": _recency_weights(ages, cfg.forecast.half_life_days),
        },
        index=pd.Index(list(day.index), name="date"),
        columns=HISTORY_COLUMNS,
    )


# ---------------------------------------------------------------------------
# weekday profile
# ---------------------------------------------------------------------------


def _profile_from_history(hist: pd.DataFrame, cfg: Config, o: date, reason: str | None) -> pd.DataFrame:
    profile = pd.DataFrame(0.0, index=pd.RangeIndex(7), columns=PROFILE_COLUMNS)
    profile["weekday"] = list(WEEKDAY_NAMES)
    profile["days"] = 0
    profile.attrs.update(observed_through=o, reason=reason, observed_days=int(len(hist)), overall={})
    if hist.empty:
        return profile

    prior = max(0.0, float(cfg.forecast.prior_days))
    weights = _probabilities(hist["weight"].to_numpy(float))
    weekday = hist["weekday"].to_numpy()
    counts = np.bincount(weekday, minlength=7)
    profile["days"] = counts

    for column, source in _AVG_SOURCES.items():
        sums = np.bincount(weekday, weights=hist[source].to_numpy(float), minlength=7)
        profile[column] = np.divide(sums, counts, out=np.zeros(7), where=counts > 0)

    weight_by_weekday = np.bincount(weekday, weights=weights, minlength=7)
    overall: dict[str, float] = {}
    for column, source in _RATE_SOURCES.items():
        values = hist[source].to_numpy(float)
        overall_mean = float((weights * values).sum())  # weights already sum to 1
        weighted_sums = np.bincount(weekday, weights=weights * values, minlength=7)
        has_weight = weight_by_weekday > 0
        weekday_mean = np.divide(weighted_sums, weight_by_weekday, out=np.zeros(7), where=has_weight)
        # A weekday whose weights all underflowed carries no information: treat it as unseen.
        n = np.where(has_weight, counts, 0).astype(float)
        denominator = n + prior
        shrunk = np.divide(n * weekday_mean + prior * overall_mean, denominator,
                           out=np.full(7, overall_mean), where=denominator > 0)
        profile[column] = shrunk
        overall[source] = overall_mean
    # Shrinkage is linear, so this equals the shrunk "meals" rate; assigning it keeps the identity exact.
    profile["meals_rate"] = profile["me_rate"] + profile["dd_meal_rate"]

    week_total = float(profile["meals_rate"].sum())
    profile["share"] = profile["meals_rate"] / week_total if week_total > 0 else 0.0
    profile.attrs["overall"] = overall
    return profile


def _no_history_reason(df: pd.DataFrame, cfg: Config, o: date) -> str:
    if df.empty:
        return "no transactions loaded yet"
    if o < max(cfg.semester_start, cfg.data_cutoff):
        return "no observed days yet — the data does not reach into the semester"
    return "no usable days yet — every observed day so far is an away day"


def weekday_profile(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """How each day of the week has gone so far, and the rate the forecast uses for it.

    Always 7 rows, indexed 0..6 (Monday=0). Built from `observed_days(df, cfg)`, so it only
    reflects days the data covers and (with `cfg.exclude_away` on) skips away days.

    Columns:
        weekday        day name ("Monday" ..).
        days           int, how many observed days of this weekday the row is based on.
        meals_avg, me_avg, dd_meals_avg, dd_spend_avg, dd_snack_avg
                       plain (unweighted) per-day averages over those days: total meals, ME
                       swipes, DD meal purchases (count), DD dollars (meals + snacks, net of
                       refunds), DD snack dollars. 0.0 when `days == 0` — check `days` before
                       showing an average.
        me_rate, dd_meal_rate, dd_spend_rate, dd_snack_rate, meals_rate
                       forecast rates per day in the same units: recency-weighted mean of this
                       weekday's days, shrunk toward the overall weighted daily mean with
                       `cfg.forecast.prior_days` pseudo-days. A weekday never observed gets the
                       overall mean. `meals_rate == me_rate + dd_meal_rate`.
        share          this weekday's fraction of a whole week's `meals_rate` (sums to 1; all 0
                       when nothing has been eaten yet).

    `profile.attrs`:
        observed_through  the date O the history ends on.
        observed_days     total rows of history used.
        overall           {"me", "dd_meals", "dd_spend", "dd_snack", "meals": weighted daily mean}
                          — what every weekday is shrunk toward ({} without history).
        reason            None normally; a short string when there is no history at all (then
                          `days` is 0 everywhere and every number is 0).
    """
    o = observed_through(cfg, df)
    hist = observed_days(df, cfg)
    reason = _no_history_reason(df, cfg, o) if hist.empty else None
    return _profile_from_history(hist, cfg, o, reason)


# ---------------------------------------------------------------------------
# forecast
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class PotForecast:
    """Forecast for one pot. ME is in swipes, DD in dollars.

    Fields:
        pot: "ME" or "DD".
        start_balance: balance at the end of day O (the last observed day), before any forecast.
        expected: Series indexed by date `O..E`: expected end-of-day balance. Starts at the
            balance on O and drops by that weekday's forecast rate each day (nothing on away
            days when `cfg.exclude_away`), floored at 0. Deterministic: no simulation noise.
        lo, hi: same index; the `band` percentiles of the simulated end-of-day balance
            (`lo` = the pessimistic edge, i.e. the lower balance). Floored at 0.
        band: the two percentiles used, e.g. (10, 90) — an "80% likely range".
        leftover_p50, leftover_lo, leftover_hi: balance left at the end of E (median and band
            percentiles of the simulations), never below 0.
        prob_lasts: share of simulations (0..1) still above zero after E.
        runout_p50, runout_lo, runout_hi: the day the balance hits zero — median, early edge
            (`lo`) and late edge (`hi`) — computed among the simulations that run out on or
            before E. All None when fewer than 5% of simulations run out; read them together
            with `prob_lasts`, since they describe only the runs that do run out.
        reason: None for an ordinary forecast. Otherwise a short note. When `available` is
            False it says why there are no numbers (no history): the three Series are empty
            and every other field except `pot`, `start_balance` and `band` is None. When
            `available` is True it is a caveat to show next to the numbers (the pot has never
            been used so the forecast is flat, the pot is already used up, or the semester is
            over so there is nothing left to forecast).
    """

    pot: str
    start_balance: float
    expected: pd.Series
    lo: pd.Series
    hi: pd.Series
    band: tuple[int, int]
    leftover_p50: float | None
    leftover_lo: float | None
    leftover_hi: float | None
    prob_lasts: float | None
    runout_p50: date | None
    runout_lo: date | None
    runout_hi: date | None
    reason: str | None

    @property
    def available(self) -> bool:
        """True when the forecast has numbers (there was history to learn from)."""
        return not self.expected.empty


@dataclass(frozen=True, eq=False)
class Forecast:
    """Result of `forecast`.

    Fields:
        profile: the `weekday_profile` frame the forecast was built from.
        observed_through: O — the last day counted as known. `O+1 .. E` is forecast.
        me, dd: `PotForecast` for meal exchanges (swipes) and dining dollars ($).
        band: percentile pair behind every lo/hi (from `cfg.forecast.band`).
        simulations: number of simulated semesters behind the ranges.
    """

    profile: pd.DataFrame
    observed_through: date
    me: PotForecast
    dd: PotForecast
    band: tuple[int, int]
    simulations: int


def _band(cfg: Config) -> tuple[int, int]:
    """`cfg.forecast.band` as an ordered (low, high) percentile pair, falling back to (10, 90)."""
    try:
        low, high = sorted(float(p) for p in cfg.forecast.band)
    except (TypeError, ValueError):
        return _DEFAULT_BAND
    if not (0 <= low < high <= 100):
        return _DEFAULT_BAND
    return (int(low), int(high)) if low == int(low) and high == int(high) else (low, high)


def _series(values, dates: list[date]) -> pd.Series:
    return pd.Series(np.asarray(values, dtype=float), index=pd.Index(dates, name="date"))


def _unavailable(pot: str, start_balance: float, band: tuple[int, int], reason: str) -> PotForecast:
    empty = _series([], [])
    return PotForecast(pot, start_balance, empty, empty.copy(), empty.copy(), band,
                       None, None, None, None, None, None, None, reason)


def _draw_source_days(hist: pd.DataFrame, weekdays: np.ndarray, usable: np.ndarray, cfg: Config,
                      simulations: int) -> np.ndarray:
    """For every (simulation, future day), the row of `hist` whose usage that day copies.

    Shape `(simulations, len(weekdays))`. A future day of weekday `w` copies a same-weekday day
    with probability `n_w / (n_w + prior)` and any observed day otherwise; both picks are
    recency-weighted. Entries for non-usable (away) days are left at 0 and must be masked.
    """
    rng = np.random.default_rng(cfg.forecast.seed)
    prior = max(0.0, float(cfg.forecast.prior_days))
    weights = hist["weight"].to_numpy(float)
    everyone = _probabilities(weights)
    hist_weekday = hist["weekday"].to_numpy()

    source = np.zeros((simulations, len(weekdays)), dtype=np.intp)
    for wd in range(7):  # fixed order keeps the random stream reproducible
        columns = np.flatnonzero((weekdays == wd) & usable)
        if columns.size == 0:
            continue
        shape = (simulations, columns.size)
        pooled = rng.choice(len(hist), size=shape, p=everyone)
        own = np.flatnonzero(hist_weekday == wd)
        if own.size == 0 or not weights[own].sum() > 0:
            source[:, columns] = pooled
            continue
        same_weekday = own[rng.choice(own.size, size=shape, p=_probabilities(weights[own]))]
        use_pool = rng.random(shape) < prior / (own.size + prior)
        source[:, columns] = np.where(use_pool, pooled, same_weekday)
    return source


def _pot_forecast(pot: str, noun: str, start: float, usage: np.ndarray, expected_usage: np.ndarray,
                  dates: list[date], band: tuple[int, int], ever_used: bool) -> PotForecast:
    """Turn simulated daily usage into balances, ranges, and run-out dates for one pot.

    `usage` is `(simulations, len(dates) - 1)`: what each simulation consumes on each future day.
    `expected_usage` is the per-day forecast rate for the same days. `dates[0]` is O.
    """
    simulations = usage.shape[0]
    raw = np.concatenate([np.full((simulations, 1), start), start - np.cumsum(usage, axis=1)], axis=1)
    # Once a pot hits zero it stays there (you cannot spend what is not there).
    out = np.minimum.accumulate(raw, axis=1) <= _ZERO
    balance = np.where(out, 0.0, raw)

    lo, median, hi = np.percentile(balance, [band[0], 50, band[1]], axis=0)
    expected = np.maximum(start - np.concatenate([[0.0], np.cumsum(expected_usage)]), 0.0)

    ran_out = out[:, -1]
    runout: list[date | None] = [None, None, None]
    if ran_out.mean() >= RUNOUT_MIN_SHARE:
        first_zero_day = out[ran_out].argmax(axis=1)
        offsets = np.percentile(first_zero_day, [band[0], 50, band[1]])
        runout = [dates[int(round(float(q)))] for q in offsets]

    if start <= _ZERO:
        reason = f"{noun} are already used up"
    elif len(dates) <= 1:
        reason = "the semester is over — nothing left to forecast"
    elif not ever_used:
        reason = f"no {noun} used yet — the forecast assumes none will be"
    else:
        reason = None

    return PotForecast(
        pot=pot, start_balance=float(start),
        expected=_series(expected, dates), lo=_series(lo, dates), hi=_series(hi, dates), band=band,
        leftover_p50=float(median[-1]), leftover_lo=float(lo[-1]), leftover_hi=float(hi[-1]),
        prob_lasts=float(1.0 - ran_out.mean()),
        runout_p50=runout[1], runout_lo=runout[0], runout_hi=runout[2],
        reason=reason,
    )


def forecast(df: pd.DataFrame, cfg: Config) -> Forecast:
    """Project both pots from the day after the last observed day to semester end.

    Balances start from `metrics.balances` evaluated at O (so they match the rest of the app
    whenever the data is current). Each future day `O+1 .. E` uses its weekday's behaviour from
    `weekday_profile`; away days use nothing when `cfg.exclude_away` is on and are ordinary days
    when it is off. ME and DD are simulated together (a simulated day copies both pots' usage
    from the same historical day), then summarized separately — see `PotForecast`.

    Deterministic for a given `df` + `cfg` (`cfg.forecast.seed`). Never raises on empty or short
    data: without any observed day both pots come back with `available == False` and a `reason`.
    """
    o = observed_through(cfg, df)
    hist = observed_days(df, cfg)
    band = _band(cfg)
    simulations = max(1, int(cfg.forecast.simulations))
    left = metrics.balances(df, replace(cfg, as_of=o))

    if hist.empty:
        reason = _no_history_reason(df, cfg, o)
        profile = _profile_from_history(hist, cfg, o, reason)
        return Forecast(profile, o, _unavailable("ME", left.me_left, band, reason),
                        _unavailable("DD", left.dd_left, band, reason), band, simulations)

    profile = _profile_from_history(hist, cfg, o, None)
    dates = [o + timedelta(days=i) for i in range((cfg.semester_end - o).days + 1)]  # O..E
    future = dates[1:]
    weekdays = np.array([d.weekday() for d in future], dtype=int)
    usable = np.array([not (cfg.exclude_away and cfg.is_away(d)) for d in future], dtype=bool)
    source = _draw_source_days(hist, weekdays, usable, cfg, simulations)

    def pot(name: str, noun: str, start: float, history_column: str, rate_column: str) -> PotForecast:
        observed = hist[history_column].to_numpy(float)
        usage = np.where(usable, observed[source], 0.0)
        expected_usage = np.where(usable, profile[rate_column].to_numpy(float)[weekdays], 0.0)
        return _pot_forecast(name, noun, start, usage, expected_usage, dates, band, bool(np.any(observed != 0)))

    return Forecast(
        profile=profile, observed_through=o,
        me=pot("ME", "meal exchanges", left.me_left, "me", "me_rate"),
        dd=pot("DD", "dining dollars", left.dd_left, "dd_spend", "dd_spend_rate"),
        band=band, simulations=simulations,
    )
