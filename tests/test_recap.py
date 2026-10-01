"""Tests for dinemaster.recap — written before the implementation.

Every DataFrame is built by hand with the ingest "Public API" columns. The semester is pinned
to Fri 2026-08-21 .. Fri 2026-12-18, so the Monday-start weeks used below are:

    Aug 17  (only Fri 21 - Sun 23 fall inside the semester: the first, partial week)
    Aug 24, Aug 31, Sep 7, Sep 14  (ordinary weeks)

The observed-through day `O` is controlled by dropping empty export files (whose names carry a
date range) into a temporary raw-data directory, the same way freshness is tested.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from dinemaster import metrics
from dinemaster.config import AwayPeriod, load_config
from dinemaster.recap import Recap, recap_lines, weekly_recap, weeks_available

COLUMNS = ["account", "pot", "timestamp", "date", "description", "amount", "balance", "kind", "dd_class"]

DEN = "GH The Den"
NEWCOMB = "Newcomb Dining"
CAFE = "West Range Cafe"
GASTONS = "Gastons"


def _row(account, pot, ts, description, amount, kind, dd_class):
    timestamp = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return {
        "account": account, "pot": pot, "timestamp": timestamp, "date": timestamp.date(),
        "description": description, "amount": amount, "balance": 0.0, "kind": kind, "dd_class": dd_class,
    }


def swipe(ts, place=DEN):
    """One meal-exchange swipe."""
    return _row("Block 160 Meals", "ME", ts, place, -1.0, "usage", None)


def unswipe(ts, place=DEN):
    """A reversed swipe (nets against usage)."""
    return _row("Block 160 Meals", "ME", ts, place, 1.0, "reversal", None)


def dd(ts, place, dollars, dd_class, account="Dining Dollars"):
    """One dining-dollar ledger row; `dollars` is the positive amount spent from `account`."""
    return _row(account, "DD", ts, place, -dollars, "usage", dd_class)


def make_df(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def make_cfg(tmp_path, observed: date, as_of: date | None = None, **overrides):
    """Config whose observed-through day is `observed` (exports cover exactly through it)."""
    downloaded = datetime.combine(observed + timedelta(days=30), datetime.min.time())
    for account in ("Block 160 Meals", "Dining Dollars"):
        path = tmp_path / f"{account}_statement_2026-08-01_to_{observed.isoformat()}.csv"
        path.write_text("Date,Description,Amount,Balance\n")
        os.utime(path, (downloaded.timestamp(), downloaded.timestamp()))
    settings = dict(
        raw_dir=tmp_path,
        semester_start=date(2026, 8, 21),
        semester_end=date(2026, 12, 18),
        data_cutoff=date(2026, 8, 1),
        starting_me=160.0,
        starting_dd=360.0,
        meal_price=15.0,
        exclude_away=True,
        away_periods=(),
        as_of=as_of or observed,
    )
    settings.update(overrides)
    return replace(load_config(), **settings)


def week_of_aug_24():
    """A full week: 7 ME swipes (one more swiped and reversed), 1 DD meal, 2 snacks."""
    return [
        swipe("2026-08-24 12:05", NEWCOMB), swipe("2026-08-24 18:30", DEN),          # Mon: 2
        swipe("2026-08-25 12:10", DEN), dd("2026-08-25 15:00", GASTONS, 4.50, "snack"),  # Tue: 1 + snack
        # Wed: nothing
        swipe("2026-08-27 08:15", DEN), swipe("2026-08-27 12:20", DEN),              # Thu: 3 (+1 reversed)
        swipe("2026-08-27 18:45", NEWCOMB),
        swipe("2026-08-27 18:46", NEWCOMB), unswipe("2026-08-27 18:47", NEWCOMB),
        swipe("2026-08-28 12:00", NEWCOMB), dd("2026-08-28 19:00", CAFE, 12.00, "meal"),  # Fri: 2
        dd("2026-08-29 22:00", "Vending 12", 3.25, "snack"),                         # Sat: snack only
        # Sun: nothing
    ]


def first_weekend():
    """The semester's first three days (Fri Aug 21 - Sun Aug 23): 4 swipes."""
    return [
        swipe("2026-08-21 18:00", NEWCOMB),
        swipe("2026-08-22 12:00", DEN), swipe("2026-08-22 18:00", DEN),
        swipe("2026-08-23 12:00", DEN),
    ]


# ---------------------------------------------------------------------------
# weeks_available
# ---------------------------------------------------------------------------


def test_weeks_available_lists_mondays_from_semester_start_to_observed_through(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9))  # a Wednesday
    df = make_df(first_weekend() + week_of_aug_24())
    assert weeks_available(df, cfg) == [date(2026, 8, 17), date(2026, 8, 24), date(2026, 8, 31), date(2026, 9, 7)]


def test_weeks_available_stops_at_export_coverage_not_as_of(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 8, 30), as_of=date(2026, 9, 20))
    df = make_df(first_weekend() + week_of_aug_24())
    assert weeks_available(df, cfg) == [date(2026, 8, 17), date(2026, 8, 24)]


def test_weeks_available_empty_without_data_or_before_semester(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9))
    assert weeks_available(make_df([]), cfg) == []
    early = make_cfg(tmp_path, observed=date(2026, 9, 9), as_of=date(2026, 8, 1))
    assert weeks_available(make_df(first_weekend()), early) == []


# ---------------------------------------------------------------------------
# a complete week
# ---------------------------------------------------------------------------


def test_complete_week_totals(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9))
    r = weekly_recap(make_df(first_weekend() + week_of_aug_24()), cfg, date(2026, 8, 24))

    assert isinstance(r, Recap)
    assert r.reason is None
    assert (r.week_start, r.start, r.end) == (date(2026, 8, 24), date(2026, 8, 24), date(2026, 8, 30))
    assert r.days == 7
    assert r.complete is True
    assert r.is_latest is False and r.in_progress is False
    assert r.me_swipes == 7  # 8 swipes, 1 reversed
    assert r.dd_meals == 1
    assert r.meals == 8
    assert r.snack_count == 2
    assert r.snack_spend == pytest.approx(7.75)
    assert r.dd_spend == pytest.approx(19.75)
    assert (r.top_spot, r.top_spot_count) == (DEN, 4)
    assert r.busiest_day == (date(2026, 8, 27), 3)
    assert r.zero_meal_days == 3  # Wed, Sat (snack only), Sun
    assert all(isinstance(v, int) for v in (r.meals, r.me_swipes, r.dd_meals, r.snack_count, r.zero_meal_days))


def test_week_start_that_is_not_a_monday_means_the_week_containing_it(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9))
    df = make_df(first_weekend() + week_of_aug_24())
    assert weekly_recap(df, cfg, date(2026, 8, 27)) == weekly_recap(df, cfg, date(2026, 8, 24))


def test_top_spot_tie_goes_to_the_alphabetically_first_place(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 8, 30))
    df = make_df([swipe("2026-08-24 12:00", NEWCOMB), swipe("2026-08-25 12:00", DEN)])
    r = weekly_recap(df, cfg, date(2026, 8, 24))
    assert (r.top_spot, r.top_spot_count) == (DEN, 1)
    assert r.busiest_day == (date(2026, 8, 24), 1)  # ties go to the earliest day


# ---------------------------------------------------------------------------
# partial weeks
# ---------------------------------------------------------------------------


def test_current_week_is_clipped_to_observed_through(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9))  # Wednesday
    rows = [
        swipe("2026-09-07 12:00"), swipe("2026-09-07 18:00"),  # Mon: 2
        swipe("2026-09-09 12:00", NEWCOMB),                    # Wed: 1 (Tue: 0)
        swipe("2026-09-10 12:00"), swipe("2026-09-10 18:00"),  # Thu: after O, not known yet
    ]
    r = weekly_recap(make_df(rows), cfg, date(2026, 9, 7))

    assert (r.start, r.end, r.days) == (date(2026, 9, 7), date(2026, 9, 9), 3)
    assert r.complete is False
    assert r.is_latest is True and r.in_progress is True
    assert r.meals == 3
    assert r.zero_meal_days == 1  # Tuesday only; Thu-Sun are unknown, not zero
    assert r.busiest_day == (date(2026, 9, 7), 2)
    assert r.lines[0].startswith("3 meals so far this week")


def test_current_week_is_clipped_to_export_coverage_when_as_of_is_later(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9), as_of=date(2026, 9, 12))
    r = weekly_recap(make_df([swipe("2026-09-07 12:00")]), cfg, date(2026, 9, 7))
    assert r.end == date(2026, 9, 9)
    assert r.zero_meal_days == 2


def test_first_partial_week_of_the_semester(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9))
    rows = [swipe("2026-08-19 12:00")] + first_weekend()  # the Aug 19 swipe is before the semester
    r = weekly_recap(make_df(rows + week_of_aug_24()), cfg, date(2026, 8, 17))

    assert (r.week_start, r.start, r.end, r.days) == (date(2026, 8, 17), date(2026, 8, 21), date(2026, 8, 23), 3)
    assert r.complete is False
    assert r.in_progress is False
    assert r.meals == 4
    assert r.zero_meal_days == 0
    assert r.prev_meals is None and r.prev_meals_same_days is None
    assert r.lines[0] == "4 meals the week of Aug 17."


def test_week_outside_the_observed_window_gives_a_reason_not_a_crash(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9))
    df = make_df(first_weekend())
    for monday in (date(2026, 9, 14), date(2026, 8, 10)):
        r = weekly_recap(df, cfg, monday)
        assert r.reason
        assert r.start is None and r.end is None
        assert r.complete is False
        assert (r.meals, r.days, r.zero_meal_days) == (0, 0, 0)
        assert r.top_spot is None and r.busiest_day is None and r.pace_delta is None
        assert r.lines == []


def test_empty_ledger_gives_a_reason(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9))
    r = weekly_recap(make_df([]), cfg, date(2026, 9, 7))
    assert r.reason
    assert r.lines == []


# ---------------------------------------------------------------------------
# a week with no usage
# ---------------------------------------------------------------------------


def test_week_with_no_usage(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 13))
    r = weekly_recap(make_df(first_weekend() + week_of_aug_24()), cfg, date(2026, 8, 31))

    assert r.reason is None
    assert r.complete is True
    assert (r.meals, r.me_swipes, r.dd_meals, r.snack_count) == (0, 0, 0, 0)
    assert r.snack_spend == 0.0 and r.dd_spend == 0.0
    assert str(r.snack_spend) == "0.0"  # not -0.0
    assert r.top_spot is None and r.top_spot_count == 0
    assert r.busiest_day is None
    assert r.zero_meal_days == 7
    assert r.prev_meals == 8
    assert r.lines[0] == "No meals the week of Aug 31, 8 fewer than the week before."
    assert "No snacks." in r.lines
    assert 3 <= len(r.lines) <= 5


def test_away_days_are_not_zero_meal_days_when_away_is_excluded(tmp_path):
    away = (AwayPeriod("Trip", date(2026, 9, 4), date(2026, 9, 6)),)
    df = make_df(first_weekend() + week_of_aug_24())
    on = make_cfg(tmp_path, observed=date(2026, 9, 13), away_periods=away, exclude_away=True)
    off = make_cfg(tmp_path, observed=date(2026, 9, 13), away_periods=away, exclude_away=False)
    assert weekly_recap(df, on, date(2026, 8, 31)).zero_meal_days == 4
    assert weekly_recap(df, off, date(2026, 8, 31)).zero_meal_days == 7


def test_transactions_before_data_cutoff_are_ignored(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9), data_cutoff=date(2026, 8, 25))
    r = weekly_recap(make_df(week_of_aug_24()), cfg, date(2026, 8, 24))
    assert r.me_swipes == 5  # Monday's two swipes fall before the cutoff
    assert r.busiest_day == (date(2026, 8, 27), 3)


# ---------------------------------------------------------------------------
# previous-week comparison
# ---------------------------------------------------------------------------


def test_previous_week_comparison_between_complete_weeks(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 6))  # Sunday: the Aug 31 week is the latest
    this_week = [swipe(f"2026-09-0{d} 12:00") for d in (1, 2, 3, 4, 5)] + [swipe("2026-09-01 18:00")]
    r = weekly_recap(make_df(week_of_aug_24() + this_week), cfg, date(2026, 8, 31))

    assert r.complete is True and r.is_latest is True and r.in_progress is False
    assert r.meals == 6
    assert r.prev_meals == 8
    assert r.prev_meals_same_days == 8
    assert r.lines[0] == "6 meals this week, 2 fewer than last week."


def test_previous_week_that_was_partial_is_reported_but_not_compared(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9))
    r = weekly_recap(make_df(first_weekend() + week_of_aug_24()), cfg, date(2026, 8, 24))

    assert r.prev_meals == 4          # Fri-Sun only
    assert r.prev_meals_same_days is None  # Mon-Thu of that week were before the semester
    assert r.lines[0] == "8 meals the week of Aug 24."


def test_week_in_progress_is_compared_with_the_same_days_last_week(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 1))  # Tuesday
    this_week = [swipe("2026-08-31 12:00"), swipe("2026-08-31 18:00"), swipe("2026-09-01 12:00"),
                 swipe("2026-09-01 18:00")]
    r = weekly_recap(make_df(week_of_aug_24() + this_week), cfg, date(2026, 8, 31))

    assert r.meals == 4
    assert r.prev_meals == 8             # all of last week
    assert r.prev_meals_same_days == 3   # last Monday + Tuesday
    assert r.lines[0] == "4 meals so far this week, 1 more than the same days last week."


# ---------------------------------------------------------------------------
# split-tender dining-dollar purchases
# ---------------------------------------------------------------------------


def test_split_tender_purchases_count_once(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 8, 30))
    rows = [
        # one $12 meal paid from two accounts
        dd("2026-08-24 12:00", CAFE, 9.00, "meal"),
        dd("2026-08-24 12:00", CAFE, 3.00, "meal", account="Promotional Dining Dollars"),
        # one $5 snack paid from two accounts
        dd("2026-08-25 16:00", GASTONS, 2.00, "snack"),
        dd("2026-08-25 16:00", GASTONS, 3.00, "snack", account="Promotional Dining Dollars"),
        # a separate purchase at the same place a minute later is its own meal
        dd("2026-08-24 12:01", CAFE, 10.00, "meal"),
    ]
    r = weekly_recap(make_df(rows), cfg, date(2026, 8, 24))

    assert r.dd_meals == 2 and r.meals == 2
    assert (r.top_spot, r.top_spot_count) == (CAFE, 2)
    assert r.snack_count == 1
    assert r.snack_spend == pytest.approx(5.00)
    assert r.dd_spend == pytest.approx(27.00)
    assert "$5 on 1 snack." in r.lines


# ---------------------------------------------------------------------------
# pace
# ---------------------------------------------------------------------------


def test_pace_delta_is_the_strict_delta_as_of_week_end(tmp_path):
    away = (AwayPeriod("Thanksgiving recess", date(2026, 11, 25), date(2026, 11, 29)),)
    df = make_df(first_weekend() + week_of_aug_24())  # 11 swipes used through Aug 30
    on = make_cfg(tmp_path, observed=date(2026, 9, 9), away_periods=away, exclude_away=True)
    off = make_cfg(tmp_path, observed=date(2026, 9, 9), away_periods=away, exclude_away=False)

    # As of Aug 30: 110 of 120 days remain (105 of 115 usable with the recess excluded).
    assert weekly_recap(df, off, date(2026, 8, 24)).pace_delta == pytest.approx(149 - 160 * 110 / 120)
    assert weekly_recap(df, on, date(2026, 8, 24)).pace_delta == pytest.approx(149 - 160 * 105 / 115)

    as_of_week_end = metrics.compute_metrics(df, replace(on, as_of=date(2026, 8, 30)))
    assert weekly_recap(df, on, date(2026, 8, 24)).pace_delta == pytest.approx(
        as_of_week_end.targets["away"].strict_delta
    )


# ---------------------------------------------------------------------------
# sentence wording
# ---------------------------------------------------------------------------


def base_recap(**changes) -> Recap:
    r = Recap(
        week_start=date(2026, 9, 7), start=date(2026, 9, 7), end=date(2026, 9, 13), days=7,
        complete=True, is_latest=True, in_progress=False,
        meals=9, me_swipes=8, dd_meals=1, snack_spend=13.75, snack_count=3, dd_spend=26.0,
        top_spot=DEN, top_spot_count=3, busiest_day=(date(2026, 9, 8), 3), zero_meal_days=2,
        prev_meals=7, prev_meals_same_days=7, pace_delta=10.44,
    )
    return replace(r, **changes)


def test_lines_for_a_typical_latest_week():
    assert recap_lines(base_recap()) == [
        "9 meals this week, 2 more than last week.",
        "3 at GH The Den.",
        "$14 on 3 snacks.",
        "Busiest day was Tuesday with 3 meals; 2 days had none.",
        "10.4 meals ahead of an even pace.",
    ]


def test_lines_for_an_older_week_name_the_week():
    lines = recap_lines(base_recap(is_latest=False))
    assert lines[0] == "9 meals the week of Sep 7, 2 more than the week before."
    assert lines[-1] == "10.4 meals ahead of an even pace as of Sep 13."


@pytest.mark.parametrize(
    "meals, prev, expected",
    [
        (9, 7, "9 meals this week, 2 more than last week."),
        (9, 8, "9 meals this week, 1 more than last week."),
        (7, 9, "7 meals this week, 2 fewer than last week."),
        (1, 2, "1 meal this week, 1 fewer than last week."),
        (5, 5, "5 meals this week, same as last week."),
        (0, 0, "No meals this week, same as last week."),
        (0, 3, "No meals this week, 3 fewer than last week."),
        (4, None, "4 meals this week."),
    ],
)
def test_meal_line_singular_plural_and_comparison(meals, prev, expected):
    assert recap_lines(base_recap(meals=meals, prev_meals=prev, prev_meals_same_days=prev))[0] == expected


@pytest.mark.parametrize(
    "spend, count, expected",
    [
        (13.75, 3, "$14 on 3 snacks."),
        (4.5, 1, "$4.50 on 1 snack."),
        (5.0, 1, "$5 on 1 snack."),
        (0.0, 0, "No snacks."),
    ],
)
def test_snack_line_singular_plural_and_money(spend, count, expected):
    assert recap_lines(base_recap(snack_spend=spend, snack_count=count))[2] == expected


@pytest.mark.parametrize(
    "busiest, zero_days, expected",
    [
        ((date(2026, 9, 8), 3), 2, "Busiest day was Tuesday with 3 meals; 2 days had none."),
        ((date(2026, 9, 12), 1), 1, "Busiest day was Saturday with 1 meal; 1 day had none."),
        ((date(2026, 9, 7), 2), 0, "Busiest day was Monday with 2 meals."),
    ],
)
def test_day_line_singular_plural(busiest, zero_days, expected):
    assert recap_lines(base_recap(busiest_day=busiest, zero_meal_days=zero_days))[3] == expected


@pytest.mark.parametrize(
    "delta, expected",
    [
        (10.44, "10.4 meals ahead of an even pace."),
        (-3.0, "3.0 meals behind an even pace."),
        (1.0, "1.0 meals ahead of an even pace."),
        (0.02, "Right on an even pace."),
        (-0.04, "Right on an even pace."),
    ],
)
def test_pace_line_ahead_behind(delta, expected):
    assert recap_lines(base_recap(pace_delta=delta))[-1] == expected


def test_partial_week_equal_to_the_same_days_last_week():
    r = base_recap(complete=False, in_progress=True, end=date(2026, 9, 9), days=3, meals=4, prev_meals=9,
                   prev_meals_same_days=4)
    assert recap_lines(r)[0] == "4 meals so far this week, level with the same days last week."


def test_lines_skip_what_is_unknown():
    r = base_recap(meals=0, me_swipes=0, dd_meals=0, top_spot=None, top_spot_count=0, busiest_day=None,
                   zero_meal_days=7, snack_spend=0.0, snack_count=0, prev_meals=None,
                   prev_meals_same_days=None, pace_delta=None)
    assert recap_lines(r) == ["No meals this week.", "No snacks."]
    assert recap_lines(replace(r, reason="nothing observed", start=None, end=None)) == []


def test_weekly_recap_lines_match_recap_lines_and_stay_short(tmp_path):
    cfg = make_cfg(tmp_path, observed=date(2026, 9, 9), starting_me=20.0)
    r = weekly_recap(make_df(first_weekend() + week_of_aug_24()), cfg, date(2026, 8, 24))
    assert r.lines == recap_lines(r)
    assert 3 <= len(r.lines) <= 5
    assert r.lines[-1] == "9.3 meals behind an even pace as of Aug 30."  # 9 left vs 20 * 110/120
