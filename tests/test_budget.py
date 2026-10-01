"""Tests for dinemaster.budget — written before the implementation.

Everything is synthetic: weekday profiles are hand-built frames with the columns
`forecast.weekday_profile` produces (one integration test builds a profile from a hand-built
ledger instead). The semester ends on Friday 2026-12-18.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from dinemaster import budget as bg
from dinemaster import forecast as fc
from dinemaster.config import AwayPeriod, PotRule, load_config

S = date(2026, 8, 21)  # a Friday
E = date(2026, 12, 18)  # a Friday
TWO_WEEKS_OUT = date(2026, 12, 5)  # Saturday; ..E is exactly 14 days, every weekday twice
ONE_WEEK_OUT = date(2026, 12, 12)  # Saturday; ..E is exactly 7 days
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def make_cfg(tmp_path, **overrides):
    base = replace(
        load_config(),
        semester_start=S, semester_end=E, data_cutoff=date(2026, 8, 1),
        starting_me=160.0, starting_dd=360.0, meal_price=15.0,
        rounding_mode="floor", rounding_granularity=1.0,
        exclude_away=True, away_periods=(), max_meals_per_day=3,
        raw_dir=tmp_path, account_regex=r"^(?P<account>.+?)_statement",
        pots=(PotRule("(?i)meal", "ME"), PotRule("(?i)dining dollars", "DD")),
        as_of=E,
    )
    return replace(base, **overrides)


def make_profile(meals_rate=1.0, dd_snack_rate=0.0) -> pd.DataFrame:
    """A weekday profile with just the columns budget.py reads.

    Each argument is either one number for every weekday or a `{weekday: value}` dict
    (weekdays not named get 0).
    """
    def column(value):
        if isinstance(value, dict):
            return [float(value.get(wd, 0.0)) for wd in range(7)]
        return [float(value)] * 7

    return pd.DataFrame(
        {"weekday": NAMES, "meals_rate": column(meals_rate), "dd_snack_rate": column(dd_snack_rate)},
        index=pd.RangeIndex(7),
    )


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def plan_frame(rows: list[tuple[date, bool, int]]) -> pd.DataFrame:
    """A hand-built budget frame: (date, away, meals) per row."""
    frame = pd.DataFrame(
        {
            "weekday": [NAMES[d.weekday()] for d, _, _ in rows],
            "away": [away for _, away, _ in rows],
            "weight": [0.0 if away else 1.0 for _, away, _ in rows],
            "meals": [meals for _, _, meals in rows],
        },
        index=pd.Index([d for d, _, _ in rows], name="date"),
    )
    frame.attrs.update(total=sum(m for _, _, m in rows), surplus=0)
    return frame


# ---------------------------------------------------------------------------
# daily_budget: shape and totals
# ---------------------------------------------------------------------------


def test_budget_shape(tmp_path):
    budget = bg.daily_budget(make_cfg(tmp_path), 20, 0.0, make_profile(), TWO_WEEKS_OUT)
    assert list(budget.index) == days(TWO_WEEKS_OUT, E)
    assert list(budget.columns) == ["weekday", "away", "weight", "meals"]
    assert budget.iloc[0]["weekday"] == "Saturday" and budget.iloc[-1]["weekday"] == "Friday"
    assert pd.api.types.is_integer_dtype(budget["meals"])
    assert budget["away"].dtype == bool
    assert not budget["away"].any()


@pytest.mark.parametrize("me_left,dd_left", [(0, 0.0), (1, 0.0), (13, 0.0), (20, 47.0), (27, 119.99), (40, 30.0)])
def test_allocation_sums_exactly_to_total(tmp_path, me_left, dd_left):
    profile = make_profile({MON: 1.3, TUE: 0.9, WED: 1.7, THU: 2.2, FRI: 1.1, SAT: 0.4, SUN: 0.6})
    budget = bg.daily_budget(make_cfg(tmp_path), me_left, dd_left, profile, TWO_WEEKS_OUT)
    expected_total = me_left + int(dd_left // 15)
    assert budget.attrs["total"] == expected_total
    assert budget.attrs["surplus"] == 0
    assert int(budget["meals"].sum()) == expected_total
    assert (budget["meals"] >= 0).all()


def test_sums_exactly_over_a_long_stretch(tmp_path):
    """The full remaining semester with awkward weights still lands on the exact total."""
    profile = make_profile({MON: 1.31, TUE: 0.97, WED: 1.73, THU: 2.21, FRI: 1.13, SAT: 0.41, SUN: 0.67})
    budget = bg.daily_budget(make_cfg(tmp_path), 112, 282.51, profile, date(2026, 10, 1))
    assert budget.attrs["total"] == 112 + 18
    assert int(budget["meals"].sum()) == 130
    assert budget.attrs["surplus"] == 0


def test_total_floors_exchanges_and_rounds_dining_dollars(tmp_path):
    cfg = make_cfg(tmp_path)
    with_dd = bg.daily_budget(cfg, 10.7, 47.0, make_profile(), TWO_WEEKS_OUT)
    assert with_dd.attrs["total"] == 10 + 3
    assert with_dd.attrs["me_meals"] == 10 and with_dd.attrs["dd_meals"] == 3
    assert with_dd.attrs["include_dd"] is True


def test_exchanges_only_ignores_dining_dollars(tmp_path):
    cfg = make_cfg(tmp_path)
    only_me = bg.daily_budget(cfg, 10.7, 47.0, make_profile(), TWO_WEEKS_OUT, include_dd=False)
    assert only_me.attrs["total"] == 10
    assert only_me.attrs["dd_meals"] == 0
    assert only_me.attrs["include_dd"] is False
    assert int(only_me["meals"].sum()) == 10


def test_total_respects_rounding_mode_and_stays_whole(tmp_path):
    ceil_cfg = make_cfg(tmp_path, rounding_mode="ceil")
    assert bg.daily_budget(ceil_cfg, 10, 47.0, make_profile(), TWO_WEEKS_OUT).attrs["total"] == 10 + 4
    half_cfg = make_cfg(tmp_path, rounding_granularity=0.5)  # 52.5 / 15 = 3.5 meals
    budget = bg.daily_budget(half_cfg, 10, 52.5, make_profile(), TWO_WEEKS_OUT)
    assert budget.attrs["total"] == 13  # half a meal cannot be placed on a day
    assert int(budget["meals"].sum()) == 13


def test_negative_balances_plan_nothing(tmp_path):
    budget = bg.daily_budget(make_cfg(tmp_path), -2, -5.0, make_profile(), TWO_WEEKS_OUT)
    assert budget.attrs["total"] == 0 and budget.attrs["surplus"] == 0
    assert (budget["meals"] == 0).all()


# ---------------------------------------------------------------------------
# daily_budget: weekday weights
# ---------------------------------------------------------------------------


def test_weight_column_is_the_weekday_rate(tmp_path):
    profile = make_profile({MON: 1.0, TUE: 1.0, WED: 1.0, THU: 2.0, FRI: 1.0, SAT: 0.5, SUN: 0.5})
    budget = bg.daily_budget(make_cfg(tmp_path), 20, 0.0, profile, TWO_WEEKS_OUT)
    for d, row in budget.iterrows():
        assert row["weight"] == pytest.approx(profile.loc[d.weekday(), "meals_rate"])


def test_heavier_weekdays_get_more(tmp_path):
    profile = make_profile({MON: 1.0, TUE: 1.0, WED: 1.0, THU: 2.0, FRI: 1.0, SAT: 0.5, SUN: 0.5})
    budget = bg.daily_budget(make_cfg(tmp_path), 70, 0.0, profile, date(2026, 11, 7))  # 6 whole weeks
    by_weekday = budget.groupby("weekday")["meals"].sum()
    assert by_weekday["Thursday"] > by_weekday["Monday"] > by_weekday["Saturday"]
    assert int(budget["meals"].sum()) == 70


def test_a_heavier_day_never_gets_fewer_than_a_lighter_day(tmp_path):
    profile = make_profile({MON: 1.3, TUE: 0.9, WED: 1.7, THU: 2.2, FRI: 1.1, SAT: 0.4, SUN: 0.6})
    for total in (5, 17, 29, 38):
        budget = bg.daily_budget(make_cfg(tmp_path), total, 0.0, profile, TWO_WEEKS_OUT)
        most_on_a_lighter_day = 0
        for weight, group in budget.groupby("weight", sort=True):  # lightest weekday first
            assert group["meals"].min() >= most_on_a_lighter_day, (total, weight)
            assert group["meals"].max() - group["meals"].min() <= 1  # same-weight days stay within one meal
            most_on_a_lighter_day = group["meals"].max()


def test_profile_from_the_forecast_module_drives_the_plan(tmp_path):
    """End to end: a ledger with 3 swipes every Monday and 1 otherwise puts more on Mondays."""
    rows = []
    for d in days(S, date(2026, 9, 17)):
        for i in range(3 if d.weekday() == MON else 1):
            rows.append({
                "account": "Block 160 Meals", "pot": "ME", "timestamp": datetime(d.year, d.month, d.day, 12, i),
                "date": d, "description": "Newcomb", "amount": -1.0, "balance": 0.0, "kind": "usage",
                "dd_class": None,
            })
    df = pd.DataFrame(rows)
    cfg = make_cfg(tmp_path, as_of=date(2026, 9, 17))
    profile = fc.weekday_profile(df, cfg)
    budget = bg.daily_budget(cfg, 100, 0.0, profile, date(2026, 9, 18))
    per_day = budget.groupby("weekday")["meals"].mean()
    assert per_day["Monday"] > per_day.drop("Monday").max()
    assert int(budget["meals"].sum()) == 100


# ---------------------------------------------------------------------------
# daily_budget: uniform fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", [
    None,
    pd.DataFrame(),
    make_profile(0.0),
    make_profile(1.0).drop(columns=["meals_rate"]),
    make_profile(float("nan")),
], ids=["none", "empty", "all-zero", "missing-column", "nan"])
def test_unusable_profile_falls_back_to_uniform(tmp_path, profile):
    budget = bg.daily_budget(make_cfg(tmp_path), 20, 0.0, profile, TWO_WEEKS_OUT)
    assert budget["weight"].nunique() == 1 and budget["weight"].iloc[0] > 0
    assert int(budget["meals"].sum()) == 20
    assert budget["meals"].max() - budget["meals"].min() <= 1
    assert budget.attrs["uniform"] is True


def test_usable_profile_is_not_flagged_uniform(tmp_path):
    budget = bg.daily_budget(make_cfg(tmp_path), 20, 0.0, make_profile({THU: 2.0, MON: 1.0}), TWO_WEEKS_OUT)
    assert budget.attrs["uniform"] is False


def test_equal_days_share_leftovers_evenly_instead_of_front_loading(tmp_path):
    """7 meals over 14 identical days: every other day, not the first seven days."""
    budget = bg.daily_budget(make_cfg(tmp_path), 7, 0.0, None, TWO_WEEKS_OUT)
    meals = list(budget["meals"])
    assert sum(meals) == 7 and set(meals) == {0, 1}
    assert all(not (a == 1 and b == 1) for a, b in zip(meals, meals[1:]))


# ---------------------------------------------------------------------------
# daily_budget: away days
# ---------------------------------------------------------------------------


def test_away_days_get_zero_when_the_toggle_is_on(tmp_path):
    trip = AwayPeriod("Trip", date(2026, 12, 8), date(2026, 12, 10))
    budget = bg.daily_budget(make_cfg(tmp_path, away_periods=(trip,)), 22, 0.0, make_profile(), TWO_WEEKS_OUT)
    away = budget.loc[date(2026, 12, 8):date(2026, 12, 10)]
    assert away["away"].all()
    assert (away["meals"] == 0).all() and (away["weight"] == 0).all()
    assert int(budget["away"].sum()) == 3
    assert int(budget["meals"].sum()) == 22  # still all placed, on the other 11 days
    assert budget.attrs["usable_days"] == 11


def test_away_days_are_ordinary_when_the_toggle_is_off(tmp_path):
    trip = AwayPeriod("Trip", date(2026, 12, 8), date(2026, 12, 10))
    cfg = make_cfg(tmp_path, away_periods=(trip,), exclude_away=False)
    budget = bg.daily_budget(cfg, 28, 0.0, make_profile(), TWO_WEEKS_OUT)
    assert not budget["away"].any()
    assert (budget["meals"] == 2).all()
    assert budget.attrs["usable_days"] == 14


def test_disabled_away_period_is_ignored(tmp_path):
    trip = AwayPeriod("Trip", date(2026, 12, 8), date(2026, 12, 10), enabled=False)
    budget = bg.daily_budget(make_cfg(tmp_path, away_periods=(trip,)), 28, 0.0, make_profile(), TWO_WEEKS_OUT)
    assert not budget["away"].any()


def test_every_remaining_day_away(tmp_path):
    trip = AwayPeriod("Gone", TWO_WEEKS_OUT, E)
    budget = bg.daily_budget(make_cfg(tmp_path, away_periods=(trip,)), 9, 0.0, make_profile(), TWO_WEEKS_OUT)
    assert len(budget) == 14 and (budget["meals"] == 0).all()
    assert budget.attrs["total"] == 9 and budget.attrs["surplus"] == 9
    assert budget.attrs["reason"]


# ---------------------------------------------------------------------------
# daily_budget: per-day cap
# ---------------------------------------------------------------------------


def test_no_day_exceeds_the_cap_and_overflow_moves_to_other_days(tmp_path):
    # Uncapped, each Monday would get 20 * 10/32 = 6.25 meals.
    profile = make_profile({MON: 10.0, TUE: 1.0, WED: 1.0, THU: 1.0, FRI: 1.0, SAT: 1.0, SUN: 1.0})
    budget = bg.daily_budget(make_cfg(tmp_path), 20, 0.0, profile, TWO_WEEKS_OUT)
    assert budget["meals"].max() == 3
    mondays = budget[budget["weekday"] == "Monday"]
    assert (mondays["meals"] == 3).all()
    assert int(budget["meals"].sum()) == 20  # the 6.5 overflow meals went to the other days
    assert budget.attrs["surplus"] == 0
    others = budget[budget["weekday"] != "Monday"]["meals"]
    assert others.max() - others.min() <= 1


def test_cap_follows_config(tmp_path):
    budget = bg.daily_budget(make_cfg(tmp_path, max_meals_per_day=2), 25, 0.0, make_profile(), TWO_WEEKS_OUT)
    assert budget["meals"].max() == 2 and int(budget["meals"].sum()) == 25
    assert budget.attrs["cap"] == 2 and budget.attrs["capacity"] == 28


def test_more_meals_than_capacity_fills_to_cap_and_reports_surplus(tmp_path):
    budget = bg.daily_budget(make_cfg(tmp_path), 50, 0.0, make_profile({THU: 2.0, MON: 1.0}), TWO_WEEKS_OUT)
    assert (budget["meals"] == 3).all()
    assert budget.attrs["total"] == 50
    assert budget.attrs["capacity"] == 42
    assert budget.attrs["surplus"] == 8
    assert budget.attrs["placed"] == 42


def test_surplus_counts_only_usable_days(tmp_path):
    trip = AwayPeriod("Trip", date(2026, 12, 8), date(2026, 12, 10))
    budget = bg.daily_budget(make_cfg(tmp_path, away_periods=(trip,)), 50, 0.0, make_profile(), TWO_WEEKS_OUT)
    assert budget.attrs["capacity"] == 33 and budget.attrs["surplus"] == 17
    assert (budget.loc[budget["away"], "meals"] == 0).all()
    assert (budget.loc[~budget["away"], "meals"] == 3).all()


def test_zero_weight_days_take_overflow_before_anything_is_called_surplus(tmp_path):
    """Only Mondays have ever had meals, but one Monday cannot hold 5: the rest spills over."""
    budget = bg.daily_budget(make_cfg(tmp_path), 5, 0.0, make_profile({MON: 1.0}), ONE_WEEK_OUT)
    assert budget.loc[date(2026, 12, 14), "meals"] == 3
    assert int(budget["meals"].sum()) == 5 and budget.attrs["surplus"] == 0
    assert budget["meals"].max() == 3


# ---------------------------------------------------------------------------
# daily_budget: window edges
# ---------------------------------------------------------------------------


def test_start_after_semester_end_gives_an_empty_plan(tmp_path):
    budget = bg.daily_budget(make_cfg(tmp_path), 12, 30.0, make_profile(), E + timedelta(days=1))
    assert budget.empty
    assert list(budget.columns) == ["weekday", "away", "weight", "meals"]
    assert budget.attrs["total"] == 14
    assert budget.attrs["surplus"] == 14
    assert budget.attrs["capacity"] == 0
    assert "over" in budget.attrs["reason"]


def test_start_on_the_last_day(tmp_path):
    budget = bg.daily_budget(make_cfg(tmp_path), 2, 0.0, make_profile(), E)
    assert list(budget.index) == [E] and budget.loc[E, "meals"] == 2
    assert budget.attrs["reason"] is None


def test_start_before_the_semester_is_clamped_to_its_first_day(tmp_path):
    budget = bg.daily_budget(make_cfg(tmp_path), 160, 0.0, make_profile(), S - timedelta(days=10))
    assert budget.index[0] == S and budget.index[-1] == E
    assert int(budget["meals"].sum()) == 160


# ---------------------------------------------------------------------------
# today_line
# ---------------------------------------------------------------------------

D1, D2, D3 = date(2026, 12, 16), date(2026, 12, 17), date(2026, 12, 18)


def test_today_line_basic():
    assert bg.today_line(plan_frame([(D1, False, 2), (D2, False, 1), (D3, False, 1)]), D1) == "2 meals today, 1 tomorrow"


def test_today_line_singular_and_plural():
    assert bg.today_line(plan_frame([(D1, False, 1), (D2, False, 2)]), D1) == "1 meal today, 2 tomorrow"


def test_today_line_zero_days():
    assert bg.today_line(plan_frame([(D1, False, 0), (D2, False, 2)]), D1) == "No meals today, 2 tomorrow"
    assert bg.today_line(plan_frame([(D1, False, 2), (D2, False, 0)]), D1) == "2 meals today, none tomorrow"
    assert bg.today_line(plan_frame([(D1, False, 0), (D2, False, 0)]), D1) == "No meals today or tomorrow"


def test_today_line_away_days():
    assert bg.today_line(plan_frame([(D1, True, 0), (D2, False, 2)]), D1) == "Away today, 2 meals tomorrow"
    assert bg.today_line(plan_frame([(D1, False, 2), (D2, True, 0)]), D1) == "2 meals today, away tomorrow"
    assert bg.today_line(plan_frame([(D1, True, 0), (D2, True, 0)]), D1) == "Away today and tomorrow"


def test_today_line_on_the_last_day_has_no_tomorrow():
    assert bg.today_line(plan_frame([(D2, False, 1), (D3, False, 2)]), D3) == "2 meals today"


def test_today_line_when_today_is_already_in_the_data():
    """The plan starts the day after the last observed day, which can be tomorrow."""
    assert bg.today_line(plan_frame([(D2, False, 2), (D3, False, 1)]), D1) == "2 meals tomorrow"
    assert bg.today_line(plan_frame([(D2, True, 0), (D3, False, 1)]), D1) == "Away tomorrow"


def test_today_line_when_the_plan_starts_later():
    assert bg.today_line(plan_frame([(D3, False, 1)]), D1) == "Plan starts Fri, Dec 18: 1 meal that day"


def test_today_line_without_days_left():
    empty = plan_frame([])
    assert bg.today_line(empty, D1) == "No days left to plan"
    assert bg.today_line(plan_frame([(D1, False, 2)]), D3) == "No days left to plan"


def test_today_line_from_a_real_budget(tmp_path):
    budget = bg.daily_budget(make_cfg(tmp_path), 28, 0.0, make_profile(), TWO_WEEKS_OUT)
    assert bg.today_line(budget, TWO_WEEKS_OUT) == "2 meals today, 2 tomorrow"


# ---------------------------------------------------------------------------
# spend_down: arithmetic
# ---------------------------------------------------------------------------


def test_spend_down_arithmetic(tmp_path):
    profile = make_profile(dd_snack_rate=1.0)  # $1 of snacks a day
    sd = bg.spend_down(make_cfg(tmp_path), 20, 80.0, profile, TWO_WEEKS_OUT)
    assert sd.usable_days == 14
    assert sd.weeks == pytest.approx(2.0)
    assert sd.end == E
    assert sd.snack_reserve == pytest.approx(14.0)
    assert sd.dd_for_meals == pytest.approx(66.0)
    assert sd.dd_meals == 4
    assert sd.dd_unallocated == pytest.approx(6.0)
    assert sd.dd_meals_per_week == pytest.approx(2.0)
    assert sd.exchanges_per_week == pytest.approx(10.0)
    assert sd.meals_per_day == pytest.approx(24 / 14)
    assert sd.reason is None


def test_snack_reserve_follows_the_weekday_rates(tmp_path):
    profile = make_profile(dd_snack_rate={FRI: 5.0, SAT: 2.0})
    sd = bg.spend_down(make_cfg(tmp_path), 20, 80.0, profile, TWO_WEEKS_OUT)  # two Fridays, two Saturdays
    assert sd.snack_reserve == pytest.approx(14.0)
    sd = bg.spend_down(make_cfg(tmp_path), 20, 80.0, profile, date(2026, 12, 14))  # Mon..Fri: one Friday
    assert sd.usable_days == 5 and sd.snack_reserve == pytest.approx(5.0)


def test_spend_down_skips_away_days_when_the_toggle_is_on(tmp_path):
    trip = AwayPeriod("Trip", date(2026, 12, 5), date(2026, 12, 11))
    profile = make_profile(dd_snack_rate=1.0)
    on = bg.spend_down(make_cfg(tmp_path, away_periods=(trip,)), 21, 80.0, profile, TWO_WEEKS_OUT)
    assert on.usable_days == 7 and on.weeks == pytest.approx(1.0)
    assert on.snack_reserve == pytest.approx(7.0)
    assert on.exchanges_per_week == pytest.approx(21.0)
    off = bg.spend_down(make_cfg(tmp_path, away_periods=(trip,), exclude_away=False), 21, 80.0, profile, TWO_WEEKS_OUT)
    assert off.usable_days == 14 and off.snack_reserve == pytest.approx(14.0)


@pytest.mark.parametrize("profile", [None, pd.DataFrame(), make_profile().drop(columns=["dd_snack_rate"])],
                         ids=["none", "empty", "missing-column"])
def test_spend_down_without_snack_history_reserves_nothing(tmp_path, profile):
    sd = bg.spend_down(make_cfg(tmp_path), 20, 60.0, profile, TWO_WEEKS_OUT)
    assert sd.snack_reserve == 0.0
    assert sd.dd_meals == 4 and sd.dd_unallocated == pytest.approx(0.0)


def test_spend_down_uses_floor_for_whole_dining_dollar_meals(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path, rounding_mode="ceil"), 20, 59.99, None, TWO_WEEKS_OUT)
    assert sd.dd_meals == 3
    assert sd.dd_unallocated == pytest.approx(14.99)


def test_spend_down_after_the_semester(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 20, 80.0, make_profile(), E + timedelta(days=1))
    assert sd.usable_days == 0 and sd.weeks == 0.0
    assert sd.reason and "over" in sd.reason
    assert sd.dd_meals is None and sd.dd_meals_per_week is None
    assert sd.exchanges_per_week is None and sd.meals_per_day is None
    assert sd.snack_reserve is None and sd.dd_for_meals is None and sd.dd_unallocated is None
    assert sd.sentence  # still something to show


def test_spend_down_when_every_remaining_day_is_away(tmp_path):
    trip = AwayPeriod("Gone", TWO_WEEKS_OUT, E)
    sd = bg.spend_down(make_cfg(tmp_path, away_periods=(trip,)), 20, 80.0, make_profile(), TWO_WEEKS_OUT)
    assert sd.usable_days == 0 and sd.reason and "away" in sd.reason
    assert sd.meals_per_day is None


# ---------------------------------------------------------------------------
# spend_down: sentence wording
# ---------------------------------------------------------------------------


def test_sentence_matches_the_spec_example(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 20, 60.0, make_profile(), TWO_WEEKS_OUT)
    assert sd.sentence == (
        "Buy about 2 meals a week with dining dollars and use about 10 exchanges a week"
        " — both reach zero on Dec 18."
    )


def test_sentence_mentions_the_snack_reserve(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 20, 80.0, make_profile(dd_snack_rate=1.0), TWO_WEEKS_OUT)
    assert sd.sentence == (
        "Buy about 2 meals a week with dining dollars and use about 10 exchanges a week"
        " — both reach zero on Dec 18. About $14 is set aside for snacks."
    )


def test_sentence_singular_units(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 2, 30.0, None, TWO_WEEKS_OUT)
    assert sd.sentence == (
        "Buy about 1 meal a week with dining dollars and use about 1 exchange a week"
        " — both reach zero on Dec 18."
    )


def test_sentence_for_rates_below_one_a_week(tmp_path):
    """1 dining-dollar meal over 4 weeks reads as "1 meal every 4 weeks", not "0 meals a week"."""
    sd = bg.spend_down(make_cfg(tmp_path), 40, 20.0, None, date(2026, 11, 21))  # 28 days
    assert sd.dd_meals == 1 and sd.dd_meals_per_week == pytest.approx(0.25)
    assert sd.sentence == (
        "Buy about 1 meal every 4 weeks with dining dollars and use about 10 exchanges a week"
        " — both reach zero on Dec 18."
    )


def test_sentence_when_snacks_would_eat_all_the_dining_dollars(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 20, 80.0, make_profile(dd_snack_rate=10.0), TWO_WEEKS_OUT)
    assert sd.snack_reserve == pytest.approx(140.0)
    assert sd.dd_for_meals == 0.0 and sd.dd_meals == 0 and sd.dd_unallocated == 0.0
    assert sd.dd_meals_per_week == 0.0
    assert sd.meals_per_day == pytest.approx(20 / 14)
    assert sd.sentence == (
        "Snacks alone would use up your dining dollars (about $140 expected, $80 left)"
        " — use about 10 exchanges a week to reach zero on Dec 18."
    )


def test_sentence_when_less_than_one_meal_is_left_after_snacks(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 20, 20.0, make_profile(dd_snack_rate=1.0), TWO_WEEKS_OUT)
    assert sd.dd_meals == 0 and sd.dd_for_meals == pytest.approx(6.0) and sd.dd_unallocated == pytest.approx(6.0)
    assert sd.sentence == (
        "After about $14 for snacks there isn't a full meal of dining dollars left"
        " — use about 10 exchanges a week to reach zero on Dec 18."
    )


def test_sentence_when_dining_dollars_are_too_few_for_a_meal_and_no_snacks(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 20, 4.5, make_profile(), TWO_WEEKS_OUT)
    assert sd.dd_meals == 0 and sd.dd_unallocated == pytest.approx(4.5)
    assert sd.sentence == (
        "Only $4.50 in dining dollars left, not enough for a meal"
        " — use about 10 exchanges a week to reach zero on Dec 18."
    )


def test_sentence_without_dining_dollars(tmp_path):
    for dd_left in (0.0, -3.2):
        sd = bg.spend_down(make_cfg(tmp_path), 20, dd_left, make_profile(dd_snack_rate=1.0), TWO_WEEKS_OUT)
        assert sd.dd_meals == 0 and sd.dd_for_meals == 0.0 and sd.dd_unallocated == 0.0
        assert sd.sentence == "No dining dollars left — use about 10 exchanges a week to reach zero on Dec 18."


def test_sentence_without_exchanges(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 0, 60.0, None, TWO_WEEKS_OUT)
    assert sd.exchanges_per_week == 0.0
    assert sd.sentence == "No exchanges left — buy about 2 meals a week with dining dollars to reach zero on Dec 18."


def test_sentence_with_nothing_left(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 0, 0.0, make_profile(), TWO_WEEKS_OUT)
    assert sd.meals_per_day == 0.0
    assert sd.sentence == "No exchanges or dining dollars left."


def test_sentence_without_exchanges_when_snacks_eat_the_dining_dollars(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 0, 80.0, make_profile(dd_snack_rate=10.0), TWO_WEEKS_OUT)
    assert sd.sentence == (
        "No exchanges left, and snacks alone would use up your dining dollars (about $140 expected, $80 left)."
    )


def test_sentence_in_the_final_days_uses_totals_not_weekly_rates(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 6, 20.0, None, date(2026, 12, 16))  # 3 days left
    assert sd.usable_days == 3
    assert sd.sentence == (
        "Buy 1 meal with dining dollars and use 6 exchanges over the last 3 days — both reach zero on Dec 18."
    )
    last = bg.spend_down(make_cfg(tmp_path), 2, 0.0, None, E)
    assert last.sentence == "No dining dollars left — use 2 exchanges on the last day to reach zero on Dec 18."


def test_sentence_when_the_semester_is_over(tmp_path):
    sd = bg.spend_down(make_cfg(tmp_path), 20, 80.0, make_profile(), E + timedelta(days=3))
    assert sd.sentence == "The semester is over — nothing left to spread out."
