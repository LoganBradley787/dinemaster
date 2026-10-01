"""Tests for dinemaster.habits and dinemaster.habits_charts (written before the implementation).

Every DataFrame is synthetic and hand-built with the ingest "Public API" columns. The observed
window is controlled through the real freshness path: `cfg_at` points `raw_dir` at a temp folder
holding empty exports whose names cover the whole semester, so `observed_through` == `as_of`.

Calendar used throughout: 2026-08-31 is a Monday (so 2026-09-07 and 2026-09-14 are too).
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import pytest

from dinemaster import habits as h
from dinemaster import habits_charts as hc
from dinemaster.config import AwayPeriod, HabitsConfig, load_config

COLUMNS = ["account", "pot", "timestamp", "date", "description", "amount", "balance", "kind", "dd_class"]

DEN, LAUNCH, GASTONS = "GH The Den", "GH Launch Kitchen", "GH Gastons Market"


def _row(account, pot, ts, description, amount, kind, dd_class=None):
    timestamp = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return {
        "account": account, "pot": pot, "timestamp": timestamp, "date": timestamp.date(),
        "description": description, "amount": amount, "balance": 0.0, "kind": kind, "dd_class": dd_class,
    }


def swipe(ts, place=DEN):
    return _row("Block 160 Meals", "ME", ts, place, -1.0, "usage")


def reversal(ts, place=DEN):
    return _row("Block 160 Meals", "ME", ts, place, 1.0, "reversal")


def dd(ts, place, dollars, dd_class, account="Dining Dollars"):
    return _row(account, "DD", ts, place, -dollars, "usage", dd_class)


def dd_refund(ts, place, dollars):
    return _row("Dining Dollars", "DD", ts, place, dollars, "reversal")


def make_df(rows):
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


@pytest.fixture
def cfg_at(tmp_path):
    """Factory: a Config whose observed-through day is exactly `as_of` and with no away periods."""
    far_future = datetime(2030, 1, 1).timestamp()
    for account in ("Block 160 Meals", "Dining Dollars"):
        f = tmp_path / f"{account}_statement_2026-08-01_to_2026-12-18.csv"
        f.write_text("Date,Description,Amount,Balance\n")
        os.utime(f, (far_future, far_future))
    base = replace(load_config(), raw_dir=tmp_path, away_periods=(), exclude_away=True)

    def build(as_of: date, **overrides):
        return replace(base, as_of=as_of, **overrides)

    return build


# ---------------------------------------------------------------------------
# hour_weekday_matrix
# ---------------------------------------------------------------------------


def test_matrix_shape_and_labels_with_no_data(cfg_at):
    m = h.hour_weekday_matrix(make_df([]), cfg_at(date(2026, 9, 6)))
    assert m.shape == (7, 24)
    assert list(m.index) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert list(m.columns) == list(range(24))
    assert int(m.to_numpy().sum()) == 0


def test_matrix_handles_a_frame_with_no_columns(cfg_at):
    m = h.hour_weekday_matrix(pd.DataFrame(), cfg_at(date(2026, 9, 6)))
    assert m.shape == (7, 24) and int(m.to_numpy().sum()) == 0


def test_matrix_counts_me_swipes_and_dd_meals_but_not_snacks(cfg_at):
    df = make_df([
        swipe("2026-08-31 12:15"),                         # Mon 12
        swipe("2026-09-07 12:40", LAUNCH),                 # Mon 12 (a week later)
        dd("2026-09-01 18:05", LAUNCH, 13.5, "meal"),      # Tue 18
        dd("2026-09-01 22:10", GASTONS, 4.0, "snack"),     # snack: not a meal
    ])
    m = h.hour_weekday_matrix(df, cfg_at(date(2026, 9, 8)))
    assert m.loc["Mon", 12] == 2
    assert m.loc["Tue", 18] == 1
    assert m.loc["Tue", 22] == 0
    assert int(m.to_numpy().sum()) == 3


def test_matrix_reversal_nets_out_of_the_swipes_own_cell(cfg_at):
    # Swiped at 12:58, refunded at 13:04: the *12 o'clock* cell must drop to zero and the
    # 13 o'clock cell must not go negative.
    df = make_df([
        swipe("2026-08-31 12:58", LAUNCH),
        reversal("2026-08-31 13:04", LAUNCH),
        swipe("2026-08-31 13:10", DEN),
    ])
    m = h.hour_weekday_matrix(df, cfg_at(date(2026, 9, 1)))
    assert m.loc["Mon", 12] == 0
    assert m.loc["Mon", 13] == 1
    assert int(m.to_numpy().sum()) == 1
    assert (m.to_numpy() >= 0).all()


def test_matrix_reversal_cancels_only_one_of_two_swipes_at_a_place(cfg_at):
    df = make_df([
        swipe("2026-08-31 12:00", DEN),
        swipe("2026-08-31 18:00", DEN),
        reversal("2026-08-31 18:03", DEN),   # undoes the 18:00 swipe (most recent), not the noon one
    ])
    m = h.hour_weekday_matrix(df, cfg_at(date(2026, 9, 1)))
    assert m.loc["Mon", 12] == 1
    assert m.loc["Mon", 18] == 0


def test_matrix_reversal_with_nothing_to_cancel_is_ignored(cfg_at):
    df = make_df([reversal("2026-08-31 09:00", DEN), swipe("2026-08-31 12:00", DEN)])
    m = h.hour_weekday_matrix(df, cfg_at(date(2026, 9, 1)))
    assert int(m.to_numpy().sum()) == 1
    assert (m.to_numpy() >= 0).all()


def test_matrix_counts_a_split_tender_dd_meal_once(cfg_at):
    df = make_df([
        dd("2026-09-01 18:05", LAUNCH, 9.0, "meal", account="Dining Dollars"),
        dd("2026-09-01 18:05", LAUNCH, 4.5, "meal", account="Promotional Dining Dollars"),
    ])
    m = h.hour_weekday_matrix(df, cfg_at(date(2026, 9, 2)))
    assert m.loc["Tue", 18] == 1


def test_matrix_window_is_data_cutoff_through_observed_day(cfg_at):
    df = make_df([
        swipe("2026-07-30 12:00"),   # before data_cutoff (2026-08-01): ignored
        swipe("2026-08-10 12:00"),   # after the cutoff but before the semester: counted
        swipe("2026-09-01 12:00"),   # observed
        swipe("2026-09-03 12:00"),   # after the observed day: unknown, ignored
    ])
    m = h.hour_weekday_matrix(df, cfg_at(date(2026, 9, 2)))
    assert int(m.to_numpy().sum()) == 2


def test_matrix_ignores_loads_and_adjustments(cfg_at):
    df = make_df([
        _row("Block 160 Meals", "ME", "2026-08-31 09:00", "Deposit", 160.0, "load"),
        _row("Dining Dollars", "DD", "2026-08-31 09:00", "revoke promo", -5.0, "adjustment"),
        swipe("2026-08-31 12:00"),
    ])
    assert int(h.hour_weekday_matrix(df, cfg_at(date(2026, 9, 1))).to_numpy().sum()) == 1


def test_busiest_slot(cfg_at):
    df = make_df([swipe("2026-08-31 12:15"), swipe("2026-09-07 12:40"), swipe("2026-09-01 18:00")])
    cfg = cfg_at(date(2026, 9, 8))
    assert h.busiest_slot(h.hour_weekday_matrix(df, cfg)) == ("Mon", 12, 2)
    assert h.busiest_slot(h.hour_weekday_matrix(make_df([]), cfg)) is None


# ---------------------------------------------------------------------------
# streaks
# ---------------------------------------------------------------------------


def test_streaks_empty_data_has_zero_runs_and_a_reason(cfg_at):
    s = h.streaks(make_df([]), cfg_at(date(2026, 9, 6)))
    for run in (s.same_place, s.meal_streak, s.gap, s.current):
        assert run.length == 0 and run.start is None and run.end is None and run.place is None
    assert s.reason


def test_streaks_before_the_semester_starts(cfg_at):
    s = h.streaks(make_df([swipe("2026-08-10 12:00")]), cfg_at(date(2026, 8, 15)))
    assert s.meal_streak.length == 0 and s.gap.length == 0 and s.days == 0
    assert s.reason


def test_streaks_single_meal_day(cfg_at):
    # Semester starts Fri 2026-08-21; the only meal is on the 24th; observed through the 26th.
    s = h.streaks(make_df([swipe("2026-08-24 12:00", DEN)]), cfg_at(date(2026, 8, 26)))
    assert (s.meal_streak.length, s.meal_streak.start, s.meal_streak.end) == (1, date(2026, 8, 24), date(2026, 8, 24))
    assert (s.same_place.place, s.same_place.length) == (DEN, 1)
    assert (s.same_place.start, s.same_place.end) == (date(2026, 8, 24), date(2026, 8, 24))
    # Aug 21-23 (3 days) beats Aug 25-26 (2 days).
    assert (s.gap.length, s.gap.start, s.gap.end) == (3, date(2026, 8, 21), date(2026, 8, 23))
    assert s.current.length == 0
    assert s.days == 6
    assert s.reason is None


def test_streaks_semester_of_one_observed_day(cfg_at):
    s = h.streaks(make_df([swipe("2026-08-21 12:00", DEN)]), cfg_at(date(2026, 8, 21)))
    assert s.meal_streak.length == 1 and s.current.length == 1 and s.gap.length == 0
    assert s.gap.start is None
    assert (s.current.start, s.current.end) == (date(2026, 8, 21), date(2026, 8, 21))


def test_streaks_longest_meal_run_and_current_run(cfg_at):
    rows = [swipe(f"2026-08-{d} 12:00", DEN) for d in (21, 22, 23)]          # 3-day run
    rows += [swipe(f"2026-08-{d} 12:00", LAUNCH) for d in (27, 28)]           # 2-day run ending at O
    s = h.streaks(make_df(rows), cfg_at(date(2026, 8, 28)))
    assert (s.meal_streak.length, s.meal_streak.start, s.meal_streak.end) == (3, date(2026, 8, 21), date(2026, 8, 23))
    assert (s.gap.length, s.gap.start, s.gap.end) == (3, date(2026, 8, 24), date(2026, 8, 26))
    assert (s.current.length, s.current.start, s.current.end) == (2, date(2026, 8, 27), date(2026, 8, 28))


def test_streaks_tie_between_runs_goes_to_the_most_recent(cfg_at):
    rows = [swipe(f"2026-08-{d} 12:00", DEN) for d in (21, 22)]
    rows += [swipe(f"2026-08-{d} 12:00", LAUNCH) for d in (25, 26)]
    s = h.streaks(make_df(rows), cfg_at(date(2026, 8, 28)))
    # Two 2-day meal runs and two 2-day gaps (Aug 23-24, Aug 27-28): the later one wins each time.
    assert (s.meal_streak.length, s.meal_streak.start) == (2, date(2026, 8, 25))
    assert (s.gap.length, s.gap.start, s.gap.end) == (2, date(2026, 8, 27), date(2026, 8, 28))
    assert (s.same_place.place, s.same_place.length, s.same_place.start) == (LAUNCH, 2, date(2026, 8, 25))


def test_streaks_same_place_tie_on_the_same_days_is_alphabetical(cfg_at):
    rows = []
    for d in (21, 22):
        rows += [swipe(f"2026-08-{d} 12:00", LAUNCH), swipe(f"2026-08-{d} 18:00", DEN)]
    s = h.streaks(make_df(rows), cfg_at(date(2026, 8, 22)))
    assert s.same_place.length == 2
    assert s.same_place.place == min(DEN, LAUNCH)


def test_streaks_same_place_run_can_be_shorter_than_the_meal_run(cfg_at):
    rows = [
        swipe("2026-08-21 12:00", DEN),
        swipe("2026-08-22 12:00", DEN),
        swipe("2026-08-22 18:00", LAUNCH),
        swipe("2026-08-23 12:00", LAUNCH),
        swipe("2026-08-24 12:00", LAUNCH),
        dd("2026-08-25 12:00", LAUNCH, 13.5, "meal"),    # a DD meal extends a place run
        dd("2026-08-26 22:00", LAUNCH, 3.0, "snack"),    # a snack does not
    ]
    s = h.streaks(make_df(rows), cfg_at(date(2026, 8, 26)))
    assert s.meal_streak.length == 5
    assert (s.same_place.place, s.same_place.length) == (LAUNCH, 4)
    assert (s.same_place.start, s.same_place.end) == (date(2026, 8, 22), date(2026, 8, 25))


def test_streaks_reversed_swipe_is_not_a_meal_day(cfg_at):
    rows = [
        swipe("2026-08-21 12:00", DEN),
        swipe("2026-08-22 12:00", DEN),
        reversal("2026-08-22 12:05", DEN),
        swipe("2026-08-23 12:00", DEN),
    ]
    s = h.streaks(make_df(rows), cfg_at(date(2026, 8, 23)))
    assert s.meal_streak.length == 1
    assert s.same_place.length == 1
    assert (s.gap.length, s.gap.start) == (1, date(2026, 8, 22))
    assert s.current.length == 1


def test_streaks_days_after_the_observed_day_are_unknown_not_zero(cfg_at):
    rows = [swipe("2026-08-21 12:00"), swipe("2026-08-22 12:00"), swipe("2026-08-30 12:00")]
    s = h.streaks(make_df(rows), cfg_at(date(2026, 8, 22)))
    assert s.gap.length == 0                       # Aug 23-29 are not observed yet
    assert s.current.length == 2 and s.observed_through == date(2026, 8, 22)


AWAY = (AwayPeriod("Fall break", date(2026, 8, 24), date(2026, 8, 27)),)


def _away_rows():
    # meal Aug 21, nothing Aug 22-23, away Aug 24-27 (nothing), nothing Aug 28, meals Aug 29-30
    return [swipe("2026-08-21 12:00"), swipe("2026-08-29 12:00"), swipe("2026-08-30 12:00")]


def test_streaks_away_days_are_left_out_of_gaps_when_toggle_on(cfg_at):
    cfg = cfg_at(date(2026, 8, 30), away_periods=AWAY, exclude_away=True)
    s = h.streaks(make_df(_away_rows()), cfg)
    # The gap bridges the away period but only counts Aug 22, 23 and 28.
    assert (s.gap.length, s.gap.start, s.gap.end) == (3, date(2026, 8, 22), date(2026, 8, 28))
    assert s.days == 10 - 4


def test_streaks_away_days_are_ordinary_when_toggle_off(cfg_at):
    cfg = cfg_at(date(2026, 8, 30), away_periods=AWAY, exclude_away=False)
    s = h.streaks(make_df(_away_rows()), cfg)
    assert (s.gap.length, s.gap.start, s.gap.end) == (7, date(2026, 8, 22), date(2026, 8, 28))
    assert s.days == 10


def test_streaks_disabled_away_period_is_ordinary(cfg_at):
    off = (replace(AWAY[0], enabled=False),)
    s = h.streaks(make_df(_away_rows()), cfg_at(date(2026, 8, 30), away_periods=off, exclude_away=True))
    assert s.gap.length == 7


def test_streaks_meal_run_bridges_an_empty_away_period(cfg_at):
    rows = [swipe("2026-08-22 12:00"), swipe("2026-08-23 12:00"), swipe("2026-08-28 12:00")]
    on = h.streaks(make_df(rows), cfg_at(date(2026, 8, 28), away_periods=AWAY, exclude_away=True))
    assert (on.meal_streak.length, on.meal_streak.start, on.meal_streak.end) == (3, date(2026, 8, 22), date(2026, 8, 28))
    assert on.current.length == 3
    off = h.streaks(make_df(rows), cfg_at(date(2026, 8, 28), away_periods=AWAY, exclude_away=False))
    assert off.meal_streak.length == 2 and off.current.length == 1


def test_streaks_a_meal_eaten_on_an_away_day_still_counts(cfg_at):
    rows = [swipe("2026-08-23 12:00"), swipe("2026-08-24 12:00"), swipe("2026-08-28 12:00")]
    s = h.streaks(make_df(rows), cfg_at(date(2026, 8, 28), away_periods=AWAY, exclude_away=True))
    # Aug 24 is away but has a meal, so it is a real day; Aug 25-27 are skipped.
    assert (s.meal_streak.length, s.meal_streak.start, s.meal_streak.end) == (3, date(2026, 8, 23), date(2026, 8, 28))
    assert s.days == 8 - 3


def test_streaks_current_run_looks_past_trailing_away_days(cfg_at):
    rows = [swipe("2026-08-22 12:00"), swipe("2026-08-23 12:00")]
    s = h.streaks(make_df(rows), cfg_at(date(2026, 8, 26), away_periods=AWAY, exclude_away=True))
    assert (s.current.length, s.current.end) == (2, date(2026, 8, 23))


# ---------------------------------------------------------------------------
# late_night
# ---------------------------------------------------------------------------


def test_late_hours_wrap_midnight():
    assert h.late_hours(21, 4) == [21, 22, 23, 0, 1, 2, 3]
    assert h.late_hours(0, 3) == [0, 1, 2]
    assert h.late_hours(22, 0) == [22, 23]
    assert h.late_hours(5, 5) == []


def test_late_night_window_wraps_midnight(cfg_at):
    df = make_df([
        swipe("2026-09-01 20:59", DEN),                       # just before the window
        swipe("2026-09-01 21:00", DEN),                       # first minute of the window
        dd("2026-09-01 23:30", GASTONS, 6.0, "snack"),
        dd("2026-09-02 00:06", GASTONS, 4.0, "snack"),        # after midnight: same night
        dd("2026-09-02 03:59", GASTONS, 2.0, "snack"),        # last minute of the window
        dd("2026-09-02 04:00", GASTONS, 8.0, "snack"),        # window closed
        dd("2026-09-02 12:00", LAUNCH, 20.0, "meal"),
    ])
    ln = h.late_night(df, cfg_at(date(2026, 9, 2)))
    assert (ln.start_hour, ln.end_hour) == (21, 4)
    assert ln.count == 4
    assert ln.me_swipes == 1 and ln.dd_purchases == 3
    assert ln.dd_dollars == pytest.approx(12.0)
    assert ln.dd_share == pytest.approx(12.0 / 40.0)
    assert (ln.top_place, ln.top_place_count) == (GASTONS, 3)
    assert ln.nights == 1                                      # 9pm Sep 1 to 4am Sep 2 is one night
    assert ln.reason is None


def test_late_night_window_that_does_not_wrap(cfg_at):
    cfg = cfg_at(date(2026, 9, 2), habits=HabitsConfig(late_night_start=0, late_night_end=3))
    df = make_df([
        dd("2026-09-01 23:30", GASTONS, 6.0, "snack"),
        dd("2026-09-02 00:06", GASTONS, 4.0, "snack"),
        dd("2026-09-02 03:00", GASTONS, 2.0, "snack"),
    ])
    ln = h.late_night(df, cfg)
    assert ln.count == 1 and ln.dd_dollars == pytest.approx(4.0) and ln.nights == 1


def test_late_night_nets_reversed_swipes_and_refunds(cfg_at):
    df = make_df([
        swipe("2026-09-01 22:00", DEN),
        reversal("2026-09-01 22:02", DEN),
        dd("2026-09-01 22:30", GASTONS, 10.0, "snack"),
        dd_refund("2026-09-01 22:31", GASTONS, 4.0),
        dd("2026-09-02 12:00", LAUNCH, 14.0, "meal"),
    ])
    ln = h.late_night(df, cfg_at(date(2026, 9, 2)))
    assert ln.me_swipes == 0 and ln.dd_purchases == 1 and ln.count == 1
    assert ln.dd_dollars == pytest.approx(6.0)
    assert ln.dd_share == pytest.approx(6.0 / 20.0)


def test_late_night_counts_split_tender_once(cfg_at):
    df = make_df([
        dd("2026-09-01 22:30", GASTONS, 5.0, "snack", account="Dining Dollars"),
        dd("2026-09-01 22:30", GASTONS, 3.0, "snack", account="Promotional Dining Dollars"),
    ])
    ln = h.late_night(df, cfg_at(date(2026, 9, 2)))
    assert ln.count == 1 and ln.dd_dollars == pytest.approx(8.0) and ln.dd_share == pytest.approx(1.0)


def test_late_night_top_place_tie_goes_to_more_dd_dollars(cfg_at):
    df = make_df([swipe("2026-09-01 22:00", DEN), dd("2026-09-01 22:30", GASTONS, 5.0, "snack")])
    ln = h.late_night(df, cfg_at(date(2026, 9, 2)))
    assert (ln.top_place, ln.top_place_count) == (GASTONS, 1)


def test_late_night_empty_data(cfg_at):
    ln = h.late_night(make_df([]), cfg_at(date(2026, 9, 2)))
    assert ln.count == 0 and ln.me_swipes == 0 and ln.dd_purchases == 0 and ln.nights == 0
    assert ln.dd_dollars == 0.0 and ln.dd_share is None and ln.top_place is None and ln.top_place_count == 0
    assert ln.reason


def test_late_night_none_but_daytime_spend(cfg_at):
    ln = h.late_night(make_df([dd("2026-09-01 12:00", LAUNCH, 14.0, "meal")]), cfg_at(date(2026, 9, 2)))
    assert ln.count == 0 and ln.dd_share == pytest.approx(0.0) and ln.top_place is None
    assert ln.reason is None


# ---------------------------------------------------------------------------
# place_stats
# ---------------------------------------------------------------------------

PLACE_COLUMNS = [
    "swipes", "dd_meals", "dd_snacks", "dd_spend", "visits", "typical_hour", "favorite_weekday", "favorite_weekday_num",
]


def test_place_stats_empty(cfg_at):
    ps = h.place_stats(make_df([]), cfg_at(date(2026, 9, 2)))
    assert ps.empty and list(ps.columns) == PLACE_COLUMNS and ps.index.name == "place"


def test_place_stats_columns_and_values(cfg_at):
    df = make_df([
        swipe("2026-08-31 12:00", DEN),                        # Mon
        swipe("2026-09-07 12:30", DEN),                        # Mon
        swipe("2026-09-02 18:00", DEN),                        # Wed
        swipe("2026-09-03 11:00", DEN),                        # Thu (reversed below)
        reversal("2026-09-03 11:04", DEN),
        dd("2026-09-01 13:00", LAUNCH, 13.5, "meal"),          # Tue
        dd("2026-09-04 22:00", GASTONS, 9.0, "snack", account="Dining Dollars"),              # Fri, split tender
        dd("2026-09-04 22:00", GASTONS, 1.0, "snack", account="Promotional Dining Dollars"),
        dd("2026-09-05 23:00", GASTONS, 5.0, "snack"),         # Sat
        dd_refund("2026-09-05 23:01", GASTONS, 2.0),
    ])
    ps = h.place_stats(df, cfg_at(date(2026, 9, 8)))
    assert list(ps.columns) == PLACE_COLUMNS
    assert list(ps.index) == [DEN, GASTONS, LAUNCH]            # most visits first

    den = ps.loc[DEN]
    assert (den["swipes"], den["dd_meals"], den["dd_snacks"], den["visits"]) == (3, 0, 0, 3)
    assert den["dd_spend"] == pytest.approx(0.0)
    assert den["typical_hour"] == pytest.approx(12.5)          # median of 12:00, 12:30, 18:00
    assert (den["favorite_weekday"], den["favorite_weekday_num"]) == ("Mon", 0)

    gas = ps.loc[GASTONS]
    assert (gas["swipes"], gas["dd_snacks"], gas["visits"]) == (0, 2, 2)
    assert gas["dd_spend"] == pytest.approx(13.0)              # 10 + 5 - 2 refund
    assert gas["favorite_weekday"] == "Fri"                    # 1-1 tie: earliest weekday

    launch = ps.loc[LAUNCH]
    assert (launch["dd_meals"], launch["visits"]) == (1, 1)
    assert launch["dd_spend"] == pytest.approx(13.5)
    assert ps["swipes"].dtype.kind == "i" and ps["visits"].dtype.kind == "i"


def test_place_stats_typical_hour_does_not_average_across_midnight(cfg_at):
    # Visits at 23:00 and 01:00 are both "late"; the typical time is midnight, not noon.
    df = make_df([dd("2026-09-01 23:00", GASTONS, 5.0, "snack"), dd("2026-09-03 01:00", GASTONS, 5.0, "snack")])
    ps = h.place_stats(df, cfg_at(date(2026, 9, 4)))
    assert ps.loc[GASTONS, "typical_hour"] == pytest.approx(0.0)


def test_place_stats_respects_the_observed_window(cfg_at):
    df = make_df([swipe("2026-07-30 12:00", DEN), swipe("2026-09-01 12:00", DEN), swipe("2026-09-05 12:00", LAUNCH)])
    ps = h.place_stats(df, cfg_at(date(2026, 9, 2)))
    assert list(ps.index) == [DEN] and ps.loc[DEN, "visits"] == 1


def test_format_hour():
    assert h.format_hour(0) == "12 AM"
    assert h.format_hour(12) == "12 PM"
    assert h.format_hour(12.5) == "12:30 PM"
    assert h.format_hour(23.75) == "11:45 PM"
    assert h.format_hour(9) == "9 AM"
    assert h.format_hour(None) == ""


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------


def test_heatmap_chart(cfg_at):
    df = make_df([swipe("2026-08-31 12:15"), swipe("2026-09-07 12:40"), dd("2026-09-01 18:05", LAUNCH, 13.5, "meal")])
    fig = hc.heatmap_chart(h.hour_weekday_matrix(df, cfg_at(date(2026, 9, 8))))
    assert isinstance(fig, go.Figure)
    heat = fig.data[0]
    assert isinstance(heat, go.Heatmap)
    assert list(heat.y) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert len(heat.x) == 24
    assert heat.z[0][12] == 2 and heat.z[1][18] == 1
    assert fig.layout.yaxis.autorange == "reversed"            # Monday on top


def test_heatmap_chart_empty(cfg_at):
    for matrix in (h.hour_weekday_matrix(make_df([]), cfg_at(date(2026, 9, 8))), pd.DataFrame(), None):
        fig = hc.heatmap_chart(matrix)
        assert isinstance(fig, go.Figure)
        assert "no data" in fig.layout.title.text


def test_place_chart(cfg_at):
    df = make_df([
        swipe("2026-08-31 12:00", DEN), swipe("2026-09-02 18:00", DEN),
        dd("2026-09-01 13:00", LAUNCH, 13.5, "meal"),
    ])
    fig = hc.place_chart(h.place_stats(df, cfg_at(date(2026, 9, 8))))
    assert isinstance(fig, go.Figure)
    assert [t.name for t in fig.data] == ["ME swipes", "DD purchases"]
    assert all(t.orientation == "h" for t in fig.data)
    assert fig.layout.barmode == "stack"
    assert list(fig.data[0].y)[-1] == DEN                      # busiest place is drawn at the top


def test_place_chart_empty(cfg_at):
    for stats in (h.place_stats(make_df([]), cfg_at(date(2026, 9, 8))), None):
        fig = hc.place_chart(stats)
        assert isinstance(fig, go.Figure) and "no data" in fig.layout.title.text
