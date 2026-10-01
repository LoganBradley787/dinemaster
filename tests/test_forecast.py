"""Tests for dinemaster.forecast / forecast_charts — written before the implementation.

All data is synthetic and hand-built (same row shape as the ingest public API). The observed
window is controlled either by the last transaction date (empty raw dir) or by `cover()`,
which drops empty export files whose names/mtimes vouch for days with no transactions.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import pytest

from dinemaster import forecast as fc
from dinemaster import forecast_charts as fcc
from dinemaster.config import AwayPeriod, ForecastConfig, PotRule, load_config

COLUMNS = ["account", "pot", "timestamp", "date", "description", "amount", "balance", "kind", "dd_class"]

S = date(2026, 8, 21)  # a Friday
E = date(2026, 12, 18)
FOUR_WEEKS = date(2026, 9, 17)  # S..here is exactly 28 days: every weekday observed 4 times
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def swipes(d: date, n: int, place: str = "Newcomb") -> list[dict]:
    """`n` meal-exchange swipes on day `d` (distinct minutes)."""
    return [
        {
            "account": "Block 160 Meals", "pot": "ME", "timestamp": datetime(d.year, d.month, d.day, 12, i),
            "date": d, "description": place, "amount": -1.0, "balance": 0.0, "kind": "usage", "dd_class": None,
        }
        for i in range(n)
    ]


def dd(d: date, dollars: float, dd_class: str, place: str = "Cafe", account: str = "Dining Dollars",
       hour: int = 13, kind: str = "usage") -> dict:
    """One dining-dollar ledger row spending `dollars` (positive number) on day `d`."""
    return {
        "account": account, "pot": "DD", "timestamp": datetime(d.year, d.month, d.day, hour, 0),
        "date": d, "description": place, "amount": -dollars, "balance": 0.0, "kind": kind, "dd_class": dd_class,
    }


def make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLUMNS)


def me_history(per_day, start: date = S, end: date = FOUR_WEEKS) -> pd.DataFrame:
    """ME-only ledger where `per_day(d)` gives the number of swipes on each day."""
    rows: list[dict] = []
    for d in days(start, end):
        rows += swipes(d, per_day(d))
    return make_df(rows)


def cover(tmp_path, through: date) -> None:
    """Make both pots' exports vouch for every day up to `through` (even days with no rows)."""
    downloaded = datetime(through.year, through.month, through.day, 12) + timedelta(days=1)
    for account in ("Block 160 Meals", "Dining Dollars"):
        path = tmp_path / f"{account}_statement_2026-08-01_to_{through.isoformat()}.csv"
        path.write_text("Date,Description,Amount,Balance\n")
        os.utime(path, (downloaded.timestamp(), downloaded.timestamp()))


def make_cfg(tmp_path, as_of: date = FOUR_WEEKS, forecast: dict | None = None, **overrides):
    settings = dict(half_life_days=21.0, prior_days=2.0, simulations=400, seed=7, band=(10, 90))
    settings.update(forecast or {})
    base = replace(
        load_config(),
        semester_start=S, semester_end=E, data_cutoff=date(2026, 8, 1),
        starting_me=160.0, starting_dd=360.0, meal_price=15.0,
        exclude_away=True, away_periods=(),
        raw_dir=tmp_path, account_regex=r"^(?P<account>.+?)_statement",
        pots=(PotRule("(?i)meal", "ME"), PotRule("(?i)dining dollars", "DD")),
        as_of=as_of, forecast=ForecastConfig(**settings),
    )
    return replace(base, **overrides)


def varied(d: date) -> int:
    """A deterministic but bumpy swipe pattern (0..3) so simulations actually vary."""
    return ((d - S).days * 7) % 4


# ---------------------------------------------------------------------------
# weekday_profile: shape and plain averages
# ---------------------------------------------------------------------------


def test_profile_shape_and_columns(tmp_path):
    profile = fc.weekday_profile(me_history(lambda d: 1), make_cfg(tmp_path))
    assert list(profile.index) == [0, 1, 2, 3, 4, 5, 6]
    assert list(profile.columns) == [
        "weekday", "days", "meals_avg", "me_avg", "dd_meals_avg", "dd_spend_avg", "dd_snack_avg",
        "me_rate", "dd_meal_rate", "dd_spend_rate", "dd_snack_rate", "meals_rate", "share",
    ]
    assert list(profile["weekday"]) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert list(profile["days"]) == [4] * 7
    assert profile.attrs["observed_through"] == FOUR_WEEKS
    assert profile.attrs["reason"] is None


def test_plain_averages_are_per_weekday(tmp_path):
    df = me_history(lambda d: 3 if d.weekday() == MON else 1)
    profile = fc.weekday_profile(df, make_cfg(tmp_path))
    assert profile.loc[MON, "me_avg"] == pytest.approx(3.0)
    assert profile.loc[MON, "meals_avg"] == pytest.approx(3.0)
    for wd in (TUE, WED, THU, FRI, SAT, SUN):
        assert profile.loc[wd, "me_avg"] == pytest.approx(1.0)
    assert (profile["dd_meals_avg"] == 0).all() and (profile["dd_spend_avg"] == 0).all()


def test_heavy_weekday_has_higher_rate_and_share(tmp_path):
    df = me_history(lambda d: 3 if d.weekday() == MON else 1)
    profile = fc.weekday_profile(df, make_cfg(tmp_path))
    others = profile["meals_rate"].drop(MON)
    assert profile.loc[MON, "meals_rate"] > others.max()
    # Shrinkage pulls Monday toward the overall mean but not past it.
    assert 1.0 < profile.loc[MON, "me_rate"] < 3.0
    assert (others > 1.0).all()  # light days get pulled up toward the mean
    assert profile["share"].sum() == pytest.approx(1.0)
    assert profile["share"].idxmax() == MON
    assert profile.loc[MON, "share"] == pytest.approx(profile.loc[MON, "meals_rate"] / profile["meals_rate"].sum())


def test_meals_rate_is_me_plus_dd_meal_rate(tmp_path):
    rows: list[dict] = []
    for d in days(S, FOUR_WEEKS):
        rows += swipes(d, varied(d))
        if d.weekday() in (TUE, SAT):
            rows.append(dd(d, 12.0, "meal"))
    profile = fc.weekday_profile(make_df(rows), make_cfg(tmp_path))
    assert (profile["meals_rate"] - profile["me_rate"] - profile["dd_meal_rate"]).abs().max() < 1e-9
    assert (profile["meals_avg"] - profile["me_avg"] - profile["dd_meals_avg"]).abs().max() < 1e-9


# ---------------------------------------------------------------------------
# recency weighting and shrinkage
# ---------------------------------------------------------------------------


def tuesdays_ramp(d: date) -> int:
    """Tuesdays: nothing in the first two weeks, 2 swipes in the last two. Other days 1."""
    if d.weekday() != TUE:
        return 1
    return 2 if d >= date(2026, 9, 8) else 0


def test_recent_days_weigh_more(tmp_path):
    cfg = make_cfg(tmp_path, forecast=dict(prior_days=0.0, half_life_days=21.0))
    profile = fc.weekday_profile(me_history(tuesdays_ramp), cfg)
    assert profile.loc[TUE, "me_avg"] == pytest.approx(1.0)
    # Tuesdays Aug 25, Sep 1, Sep 8, Sep 15 are 23, 16, 9, 2 days before the last observed day.
    w = [0.5 ** (age / 21.0) for age in (23, 16, 9, 2)]
    expected = 2 * (w[2] + w[3]) / sum(w)
    assert profile.loc[TUE, "me_rate"] == pytest.approx(expected)
    assert profile.loc[TUE, "me_rate"] > 1.0


def test_shorter_half_life_leans_harder_on_recent_days(tmp_path):
    df = me_history(tuesdays_ramp)
    rate = {
        hl: fc.weekday_profile(df, make_cfg(tmp_path, forecast=dict(prior_days=0.0, half_life_days=hl))).loc[TUE, "me_rate"]
        for hl in (5.0, 21.0, 1e9)
    }
    assert rate[5.0] > rate[21.0] > rate[1e9]
    assert rate[1e9] == pytest.approx(1.0, abs=1e-6)  # effectively unweighted == plain average


def test_shrinkage_with_few_samples(tmp_path):
    # Aug 21 (Fri) .. Aug 28 (Fri): Friday seen twice, every other weekday once. Wednesday is a 5.
    end = date(2026, 8, 28)
    df = me_history(lambda d: 5 if d.weekday() == WED else 1, end=end)
    flat = dict(half_life_days=1e12)  # switch recency off so the arithmetic is exact
    profile = fc.weekday_profile(df, make_cfg(tmp_path, as_of=end, forecast=dict(prior_days=2.0, **flat)))
    overall = (7 * 1 + 5) / 8  # 1.5
    assert profile.attrs["overall"]["me"] == pytest.approx(overall)
    assert profile.loc[WED, "me_rate"] == pytest.approx((1 * 5 + 2 * overall) / 3)  # 2.667, far below 5
    assert profile.loc[FRI, "me_rate"] == pytest.approx((2 * 1 + 2 * overall) / 4)
    assert profile.loc[MON, "me_rate"] == pytest.approx((1 * 1 + 2 * overall) / 3)

    unshrunk = fc.weekday_profile(df, make_cfg(tmp_path, as_of=end, forecast=dict(prior_days=0.0, **flat)))
    assert unshrunk.loc[WED, "me_rate"] == pytest.approx(5.0)

    heavy_prior = fc.weekday_profile(df, make_cfg(tmp_path, as_of=end, forecast=dict(prior_days=50.0, **flat)))
    assert heavy_prior.loc[WED, "me_rate"] == pytest.approx((5 + 50 * overall) / 51)


def test_unseen_weekday_falls_back_to_overall_mean(tmp_path):
    end = date(2026, 8, 25)  # Fri..Tue observed; Wednesday and Thursday never seen
    df = me_history(lambda d: 2, end=end)
    profile = fc.weekday_profile(df, make_cfg(tmp_path, as_of=end))
    assert profile.loc[WED, "days"] == 0 and profile.loc[THU, "days"] == 0
    assert profile.loc[WED, "me_avg"] == 0.0
    assert profile.loc[WED, "me_rate"] == pytest.approx(2.0)
    assert profile.loc[THU, "meals_rate"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# away days
# ---------------------------------------------------------------------------

LABOR_DAY = AwayPeriod("Trip home", date(2026, 9, 5), date(2026, 9, 7))  # Sat, Sun, Mon
THANKSGIVING = AwayPeriod("Thanksgiving", date(2026, 11, 25), date(2026, 11, 29))


def test_away_days_in_history_excluded_when_on(tmp_path):
    cover(tmp_path, FOUR_WEEKS)
    df = me_history(lambda d: 0 if LABOR_DAY.contains(d) else 1)
    profile = fc.weekday_profile(df, make_cfg(tmp_path, away_periods=(LABOR_DAY,), exclude_away=True))
    assert profile.loc[SAT, "days"] == 3 and profile.loc[SUN, "days"] == 3 and profile.loc[MON, "days"] == 3
    assert profile.loc[TUE, "days"] == 4
    assert (profile["me_avg"] == 1.0).all()
    assert profile["me_rate"].tolist() == pytest.approx([1.0] * 7)


def test_away_days_in_history_are_ordinary_when_off(tmp_path):
    cover(tmp_path, FOUR_WEEKS)
    df = me_history(lambda d: 0 if LABOR_DAY.contains(d) else 1)
    profile = fc.weekday_profile(df, make_cfg(tmp_path, away_periods=(LABOR_DAY,), exclude_away=False))
    assert (profile["days"] == 4).all()
    assert profile.loc[SAT, "me_avg"] == pytest.approx(0.75)
    assert profile.loc[TUE, "me_avg"] == pytest.approx(1.0)


def test_future_away_days_get_no_usage_when_on(tmp_path):
    df = me_history(lambda d: 1)
    f = fc.forecast(df, make_cfg(tmp_path, away_periods=(THANKSGIVING,), exclude_away=True))
    before, last_away = date(2026, 11, 24), date(2026, 11, 29)
    for series in (f.me.expected, f.me.lo, f.me.hi):
        assert series[last_away] == pytest.approx(series[before])
    assert f.me.expected[date(2026, 11, 30)] == pytest.approx(f.me.expected[last_away] - 1.0)
    # 28 used; 92 days remain, 5 of them away.
    assert f.me.leftover_p50 == pytest.approx(160 - 28 - 87)


def test_future_away_days_are_ordinary_when_off(tmp_path):
    df = me_history(lambda d: 1)
    f = fc.forecast(df, make_cfg(tmp_path, away_periods=(THANKSGIVING,), exclude_away=False))
    assert f.me.expected[date(2026, 11, 29)] == pytest.approx(f.me.expected[date(2026, 11, 24)] - 5.0)
    assert f.me.leftover_p50 == pytest.approx(160 - 28 - 92)


# ---------------------------------------------------------------------------
# observed window
# ---------------------------------------------------------------------------


def test_days_after_observed_through_are_forecast_not_zeros(tmp_path):
    last = date(2026, 9, 10)
    df = me_history(lambda d: 2, end=last)  # nothing known after Sep 10
    cfg = make_cfg(tmp_path, as_of=date(2026, 9, 30))  # ...but "today" is Sep 30
    profile = fc.weekday_profile(df, cfg)
    assert profile["days"].sum() == 21  # Aug 21..Sep 10 only
    assert (profile["me_avg"] == 2.0).all()  # not diluted by 20 unknown days
    assert profile["me_rate"].tolist() == pytest.approx([2.0] * 7)

    f = fc.forecast(df, cfg)
    assert f.observed_through == last
    assert f.me.expected.index[0] == last and f.me.expected.index[-1] == E
    assert f.me.expected[last] == pytest.approx(160 - 42)
    assert f.me.expected[date(2026, 9, 11)] == pytest.approx(160 - 44)  # Sep 11 is forecast, not a zero day
    assert f.me.expected[date(2026, 9, 30)] == pytest.approx(160 - 42 - 2 * 20)


def test_covered_days_without_transactions_do_count_as_zeros(tmp_path):
    df = me_history(lambda d: 2, end=date(2026, 9, 10))
    cover(tmp_path, date(2026, 9, 24))  # exports vouch for two more (empty) weeks
    cfg = make_cfg(tmp_path, as_of=date(2026, 9, 24))
    profile = fc.weekday_profile(df, cfg)
    assert profile["days"].sum() == 35
    assert profile["me_avg"].max() < 2.0
    assert fc.forecast(df, cfg).observed_through == date(2026, 9, 24)


def test_rows_before_data_cutoff_are_ignored(tmp_path):
    cutoff = date(2026, 9, 4)  # a Friday: exactly two weeks remain in the window
    df = me_history(lambda d: 3 if d < cutoff else 1)
    profile = fc.weekday_profile(df, make_cfg(tmp_path, data_cutoff=cutoff))
    assert (profile["days"] == 2).all()
    assert (profile["me_avg"] == 1.0).all()


def test_observed_days_table(tmp_path):
    df = me_history(lambda d: 1)
    hist = fc.observed_days(df, make_cfg(tmp_path))
    assert hist.index[0] == S and hist.index[-1] == FOUR_WEEKS and len(hist) == 28
    assert {"weekday", "me", "dd_meals", "dd_spend", "dd_snack", "meals", "weight"} <= set(hist.columns)
    assert hist["weight"].iloc[-1] == pytest.approx(1.0)
    assert hist["weight"].iloc[-22] == pytest.approx(0.5)  # 21 days (one half-life) earlier
    assert hist["weekday"].iloc[0] == FRI


# ---------------------------------------------------------------------------
# forecast: structure, weekday effect, determinism
# ---------------------------------------------------------------------------


def test_forecast_follows_weekday_rates(tmp_path):
    df = me_history(lambda d: 3 if d.weekday() == MON else 1)
    f = fc.forecast(df, make_cfg(tmp_path, starting_me=400.0))  # enough that nothing hits zero
    drop = -f.me.expected.diff().dropna()
    by_weekday = drop.groupby([d.weekday() for d in drop.index]).mean()
    assert by_weekday[MON] > by_weekday.drop(MON).max()
    for wd in range(7):
        assert by_weekday[wd] == pytest.approx(f.profile.loc[wd, "me_rate"])


def test_forecast_structure_and_band_ordering(tmp_path):
    df = me_history(varied)
    f = fc.forecast(df, make_cfg(tmp_path, starting_me=400.0))  # enough that nothing hits zero
    used = sum(varied(d) for d in days(S, FOUR_WEEKS))
    assert f.band == (10, 90) and f.simulations == 400
    assert f.me.pot == "ME" and f.dd.pot == "DD"
    assert f.me.available
    for series in (f.me.expected, f.me.lo, f.me.hi):
        assert list(series.index) == days(FOUR_WEEKS, E)
        assert series.iloc[0] == pytest.approx(400 - used)  # all three start at today's balance
        assert (series >= 0).all()
    assert f.me.start_balance == pytest.approx(400 - used)
    assert (f.me.lo <= f.me.hi + 1e-9).all()
    assert f.me.lo.iloc[-1] < f.me.hi.iloc[-1]  # bumpy history => a real range
    assert f.me.leftover_lo <= f.me.leftover_p50 <= f.me.leftover_hi
    assert f.me.leftover_lo == pytest.approx(f.me.lo.iloc[-1])
    assert f.me.leftover_hi == pytest.approx(f.me.hi.iloc[-1])
    # The simulated median lands near the rate-based expected line.
    assert abs(f.me.leftover_p50 - f.me.expected.iloc[-1]) < 6
    assert f.me.prob_lasts == 1.0 and f.me.reason is None


def test_same_seed_same_result(tmp_path):
    df, cfg = me_history(varied), make_cfg(tmp_path)
    a, b = fc.forecast(df, cfg), fc.forecast(df, cfg)
    for name in ("expected", "lo", "hi"):
        pd.testing.assert_series_equal(getattr(a.me, name), getattr(b.me, name))
    assert (a.me.leftover_lo, a.me.leftover_p50, a.me.leftover_hi) == (b.me.leftover_lo, b.me.leftover_p50, b.me.leftover_hi)
    assert a.me.prob_lasts == b.me.prob_lasts
    assert (a.me.runout_lo, a.me.runout_p50, a.me.runout_hi) == (b.me.runout_lo, b.me.runout_p50, b.me.runout_hi)


def test_different_seed_changes_the_band_but_not_the_expected_line(tmp_path):
    df = me_history(varied)
    a = fc.forecast(df, make_cfg(tmp_path, forecast=dict(seed=7)))
    b = fc.forecast(df, make_cfg(tmp_path, forecast=dict(seed=8)))
    pd.testing.assert_series_equal(a.me.expected, b.me.expected)
    assert not a.me.lo.equals(b.me.lo) or not a.me.hi.equals(b.me.hi)


def test_wider_band_is_wider(tmp_path):
    df = me_history(varied)
    narrow = fc.forecast(df, make_cfg(tmp_path, starting_me=400.0, forecast=dict(band=(25, 75))))
    wide = fc.forecast(df, make_cfg(tmp_path, starting_me=400.0, forecast=dict(band=(5, 95))))
    assert wide.me.leftover_lo <= narrow.me.leftover_lo <= narrow.me.leftover_hi <= wide.me.leftover_hi
    assert wide.me.leftover_hi - wide.me.leftover_lo > narrow.me.leftover_hi - narrow.me.leftover_lo


# ---------------------------------------------------------------------------
# run-out, leftover, chance of lasting
# ---------------------------------------------------------------------------


def test_constant_usage_runs_out_on_a_known_day(tmp_path):
    df = me_history(lambda d: 2)  # 56 used, 104 left; 92 days remain at exactly 2/day
    f = fc.forecast(df, make_cfg(tmp_path))
    pd.testing.assert_series_equal(f.me.lo, f.me.expected, check_names=False)
    pd.testing.assert_series_equal(f.me.hi, f.me.expected, check_names=False)
    runout = FOUR_WEEKS + timedelta(days=52)  # Nov 8
    assert f.me.runout_p50 == f.me.runout_lo == f.me.runout_hi == runout
    assert f.me.expected[runout] == pytest.approx(0.0, abs=1e-9)
    assert f.me.expected[runout - timedelta(days=1)] == pytest.approx(2.0)
    assert f.me.expected.iloc[-1] == 0.0
    assert f.me.prob_lasts == 0.0
    assert f.me.leftover_p50 == f.me.leftover_lo == f.me.leftover_hi == 0.0


def test_plenty_left_never_runs_out(tmp_path):
    df = me_history(lambda d: 2)
    f = fc.forecast(df, make_cfg(tmp_path, starting_me=1000.0))
    assert f.me.prob_lasts == 1.0
    assert f.me.runout_p50 is None and f.me.runout_lo is None and f.me.runout_hi is None
    assert f.me.leftover_p50 == pytest.approx(1000 - 56 - 184)
    assert f.me.reason is None


def test_runout_range_is_ordered_when_usage_is_bumpy(tmp_path):
    df = me_history(varied)  # averages 1.5/day; 98 left cover only ~65 of the 92 remaining days
    f = fc.forecast(df, make_cfg(tmp_path, starting_me=140.0))
    assert f.me.prob_lasts < 0.05
    assert f.me.runout_lo <= f.me.runout_p50 <= f.me.runout_hi <= E
    assert f.me.runout_lo < f.me.runout_hi
    assert f.me.runout_lo > FOUR_WEEKS


def test_already_empty_pot(tmp_path):
    df = me_history(lambda d: 2)
    f = fc.forecast(df, make_cfg(tmp_path, starting_me=56.0))
    assert f.me.start_balance == pytest.approx(0.0)
    assert f.me.prob_lasts == 0.0
    assert f.me.runout_p50 == FOUR_WEEKS
    assert (f.me.expected == 0).all()
    assert "used up" in f.me.reason


# ---------------------------------------------------------------------------
# dining dollars
# ---------------------------------------------------------------------------


def dd_history() -> pd.DataFrame:
    """Every day a $10 meal; Fridays also a $4 snack. One meal is split across two DD accounts."""
    split_day = date(2026, 9, 2)
    rows: list[dict] = []
    for d in days(S, FOUR_WEEKS):
        if d == split_day:
            rows.append(dd(d, 6.0, "meal"))
            rows.append(dd(d, 4.0, "meal", account="Promotional Dining Dollars"))
        else:
            rows.append(dd(d, 10.0, "meal"))
        if d.weekday() == FRI:
            rows.append(dd(d, 4.0, "snack", place="Vending", hour=16))
    return make_df(rows)


def test_dd_profile_counts_dollars_and_split_tender(tmp_path):
    profile = fc.weekday_profile(dd_history(), make_cfg(tmp_path))
    assert (profile["dd_meals_avg"] == 1.0).all()  # the split purchase is one meal
    assert (profile["meals_avg"] == 1.0).all() and (profile["me_avg"] == 0.0).all()
    assert profile.loc[FRI, "dd_spend_avg"] == pytest.approx(14.0)
    assert profile.loc[WED, "dd_spend_avg"] == pytest.approx(10.0)
    assert profile.loc[FRI, "dd_snack_avg"] == pytest.approx(4.0)  # snack dollars, not a count
    assert profile.loc[WED, "dd_snack_avg"] == 0.0
    assert profile.loc[FRI, "dd_snack_rate"] > profile.loc[WED, "dd_snack_rate"] > 0.0  # shrunk toward the mean
    assert profile.loc[FRI, "dd_spend_rate"] > profile.loc[WED, "dd_spend_rate"]


def test_dd_forecast_consumes_dollars_for_meals_and_snacks(tmp_path):
    f = fc.forecast(dd_history(), make_cfg(tmp_path, starting_dd=5000.0))
    spent = 28 * 10.0 + 4 * 4.0
    assert f.dd.start_balance == pytest.approx(5000.0 - spent)
    drop = -f.dd.expected.diff().dropna()
    for d, value in drop.items():
        assert value == pytest.approx(f.profile.loc[d.weekday(), "dd_spend_rate"])
    assert f.dd.prob_lasts == 1.0 and f.dd.runout_p50 is None
    assert f.dd.leftover_lo <= f.dd.leftover_p50 <= f.dd.leftover_hi


def test_dd_reversal_reduces_spend_but_not_meal_count(tmp_path):
    day = date(2026, 9, 16)
    rows = [dd(d, 10.0, "meal") for d in days(S, FOUR_WEEKS)]
    refund = dd(day, -10.0, None, hour=14, kind="reversal")  # +$10 back
    profile = fc.weekday_profile(make_df(rows + [refund]), make_cfg(tmp_path))
    assert profile.loc[WED, "dd_meals_avg"] == pytest.approx(1.0)
    assert profile.loc[WED, "dd_spend_avg"] == pytest.approx(7.5)


def test_dd_runs_out_with_default_balance(tmp_path):
    f = fc.forecast(dd_history(), make_cfg(tmp_path))  # $360 start, ~$10.6/day
    assert f.dd.prob_lasts == 0.0
    assert FOUR_WEEKS < f.dd.runout_lo <= f.dd.runout_p50 <= f.dd.runout_hi < E
    assert f.dd.leftover_p50 == 0.0


# ---------------------------------------------------------------------------
# zero usage, empty data, semester edges
# ---------------------------------------------------------------------------


def test_pot_with_no_usage_gets_a_flat_forecast_and_a_note(tmp_path):
    f = fc.forecast(dd_history(), make_cfg(tmp_path))  # no ME rows at all
    assert f.me.available
    assert (f.me.expected == 160.0).all() and (f.me.lo == 160.0).all() and (f.me.hi == 160.0).all()
    assert f.me.prob_lasts == 1.0
    assert f.me.runout_p50 is None
    assert f.me.leftover_p50 == 160.0
    assert "no" in f.me.reason.lower()
    assert f.dd.reason is None


def test_empty_frame_never_crashes(tmp_path):
    empty = pd.DataFrame(columns=COLUMNS)
    cfg = make_cfg(tmp_path)
    profile = fc.weekday_profile(empty, cfg)
    assert list(profile.index) == list(range(7))
    assert (profile["days"] == 0).all()
    assert (profile.drop(columns=["weekday"]) == 0).all().all()
    assert profile.attrs["reason"]

    assert fc.observed_days(empty, cfg).empty

    f = fc.forecast(empty, cfg)
    for pot in (f.me, f.dd):
        assert not pot.available
        assert pot.expected.empty and pot.lo.empty and pot.hi.empty
        assert pot.leftover_p50 is None and pot.prob_lasts is None and pot.runout_p50 is None
        assert pot.reason


def test_before_the_semester_has_no_history(tmp_path):
    df = me_history(lambda d: 1)
    f = fc.forecast(df, make_cfg(tmp_path, as_of=date(2026, 8, 1)))
    assert f.observed_through == S - timedelta(days=1)
    assert not f.me.available and f.me.reason
    assert (f.profile["days"] == 0).all()


def test_all_observed_days_away(tmp_path):
    df = me_history(lambda d: 1)
    everything = AwayPeriod("Gone", S, E)
    f = fc.forecast(df, make_cfg(tmp_path, away_periods=(everything,)))
    assert not f.me.available and f.me.reason


def test_semester_over(tmp_path):
    cover(tmp_path, E)
    df = me_history(lambda d: 1, end=E)  # 120 swipes
    f = fc.forecast(df, make_cfg(tmp_path, as_of=date(2027, 1, 5)))
    assert f.observed_through == E
    assert list(f.me.expected.index) == [E]
    assert f.me.expected.iloc[0] == pytest.approx(40.0)
    assert f.me.leftover_p50 == pytest.approx(40.0) and f.me.prob_lasts == 1.0
    assert f.me.runout_p50 is None
    assert "over" in f.me.reason


def test_zero_simulations_or_half_life_do_not_crash(tmp_path):
    df = me_history(varied)
    f = fc.forecast(df, make_cfg(tmp_path, forecast=dict(simulations=0, half_life_days=0.0, prior_days=0.0)))
    assert f.me.available and f.simulations >= 1


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------


def test_weekday_profile_chart(tmp_path):
    rows: list[dict] = []
    for d in days(S, FOUR_WEEKS):
        rows += swipes(d, varied(d))
        rows.append(dd(d, 10.0, "meal"))
    profile = fc.weekday_profile(make_df(rows), make_cfg(tmp_path))
    fig = fcc.weekday_profile_chart(profile)
    assert isinstance(fig, go.Figure)
    bars = [t for t in fig.data if isinstance(t, go.Bar)]
    assert [b.name for b in bars] == ["ME swipes", "DD meals"]
    assert fig.layout.barmode == "stack"
    assert list(bars[0].x) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert list(bars[0].y) == pytest.approx(list(profile["me_avg"]))
    assert "days" in bars[0].hovertemplate  # observed-day counts in hover
    dollars = [t for t in fig.data if isinstance(t, go.Scatter) and "$" in t.name]
    assert len(dollars) == 1
    assert list(dollars[0].y) == pytest.approx(list(profile["dd_spend_avg"]))


def test_weekday_profile_chart_empty(tmp_path):
    profile = fc.weekday_profile(pd.DataFrame(columns=COLUMNS), make_cfg(tmp_path))
    assert isinstance(fcc.weekday_profile_chart(profile), go.Figure)
    assert isinstance(fcc.weekday_profile_chart(pd.DataFrame()), go.Figure)


def test_forecast_chart(tmp_path):
    cfg = make_cfg(tmp_path, away_periods=(THANKSGIVING,))
    df = me_history(varied)
    f = fc.forecast(df, cfg)
    actual = pd.Series([160.0 - i for i in range(28)], index=days(S, FOUR_WEEKS))
    fig = fcc.forecast_chart(f.me, actual, cfg, "Meal exchanges", "meals")
    assert isinstance(fig, go.Figure)
    names = [t.name for t in fig.data]
    assert "actual" in names and "expected" in names
    assert any("range" in (n or "") for n in names)
    assert fig.layout.title.text == "Meal exchanges"
    assert len(fig.layout.shapes) >= 3  # data-through line, semester-end line, away shading
    expected = next(t for t in fig.data if t.name == "expected")
    assert list(expected.y) == pytest.approx(list(f.me.expected.values))


def test_forecast_chart_without_a_forecast(tmp_path):
    cfg = make_cfg(tmp_path)
    f = fc.forecast(pd.DataFrame(columns=COLUMNS), cfg)
    fig = fcc.forecast_chart(f.dd, pd.Series(dtype=float), cfg, "Dining dollars", "$")
    assert isinstance(fig, go.Figure)
    fig = fcc.forecast_chart(f.dd, None, cfg, "Dining dollars", "$")
    assert isinstance(fig, go.Figure)
