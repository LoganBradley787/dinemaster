"""Tests for dinemaster.compare / compare_charts — written before the implementation.

Synthetic, hand-built ledgers only. The config is the project one with every field these
functions read overridden, and `raw_dir` pointed at an empty temp folder so the observed-through
date depends only on the test's own rows and `as_of`.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import pytest

from dinemaster import compare as c
from dinemaster import compare_charts as cc
from dinemaster.charts import COLORS
from dinemaster.config import AwayPeriod, SemesterDef, load_config

COLUMNS = ["account", "pot", "timestamp", "date", "description", "amount", "balance", "kind", "dd_class"]

SPRING = SemesterDef("Spring 2026", date(2026, 1, 12), date(2026, 5, 8))  # starts on a Monday, 117 days
FALL_25 = SemesterDef("Fall 2025", date(2025, 8, 25), date(2025, 12, 12))


def row(pot, ts, description, amount, kind="usage", dd_class=None, account=None):
    timestamp = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return {
        "account": account or ("Block 160 Meals" if pot == "ME" else "Dining Dollars"),
        "pot": pot,
        "timestamp": timestamp,
        "date": timestamp.date(),
        "description": description,
        "amount": float(amount),
        "balance": 0.0,
        "kind": kind,
        "dd_class": dd_class,
    }


def make_df(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


@pytest.fixture
def cfg(tmp_path):
    """Current semester Mon 2026-08-24 .. Fri 2026-12-11 (110 days), as of Sun 2026-09-06 (day 14)."""
    return dataclasses.replace(
        load_config(),
        semester_start=date(2026, 8, 24),
        semester_end=date(2026, 12, 11),
        data_cutoff=date(2026, 9, 1),  # must be ignored by the comparison
        as_of=date(2026, 9, 6),
        exclude_away=True,
        away_periods=(),
        history=(SPRING, FALL_25),
        raw_dir=tmp_path / "no-raw-data",
    )


def spring_rows():
    """Dining dollars only, first row Sat Mar 21 (week 10), last Wed Apr 29 (week 16): 40 covered days, $30 net."""
    return [
        row("DD", "2026-03-21 12:00", "Cafe A", -12, dd_class="meal"),
        row("DD", "2026-03-25 10:00", "Vending", -4, dd_class="snack"),
        row("DD", "2026-03-25 10:05", "Vending", -4, dd_class="snack"),
        row("DD", "2026-03-25 10:06", "Vending", 4, kind="reversal"),
        # one split-tender meal: two accounts, same minute and place
        row("DD", "2026-04-10 18:30", "Cafe A", -8, dd_class="meal"),
        row("DD", "2026-04-10 18:30", "Cafe A", -3, dd_class="meal", account="Promotional Dining Dollars"),
        row("DD", "2026-04-29 21:00", "Vending", -3, dd_class="snack"),
    ]


def current_rows():
    """Through Sep 6: 4 net swipes and $14 of dining dollars. Rows after Sep 6 are beyond the as-of date."""
    return [
        row("ME", "2026-08-23 09:00", "Deposit", 160, kind="load"),  # the day before the semester
        row("DD", "2026-08-23 09:00", "Deposit", 300, kind="load"),
        row("ME", "2026-08-25 12:00", "Dining Hall", -1),
        row("ME", "2026-08-25 18:00", "Dining Hall", -1),
        row("ME", "2026-08-26 12:00", "Grill", -1),
        row("ME", "2026-08-26 12:01", "Grill", 1, kind="reversal"),
        row("DD", "2026-08-26 19:00", "Cafe A", -14, dd_class="meal"),
        row("ME", "2026-09-01 12:00", "Dining Hall", -1),
        row("ME", "2026-09-03 12:00", "Grill", -1),
        row("ME", "2026-09-08 12:00", "Grill", -1),
        row("DD", "2026-09-08 13:00", "Cafe A", -20, dd_class="meal"),
    ]


def by_name(summaries):
    return {s.name: s for s in summaries}


# ---------------------------------------------------------------------------
# semester_summaries
# ---------------------------------------------------------------------------


def test_order_is_history_then_current(cfg):
    out = c.semester_summaries(make_df(spring_rows() + current_rows()), cfg)
    assert [s.name for s in out] == ["Spring 2026", "Fall 2025", "Fall 2026"]
    assert [s.current for s in out] == [False, False, True]


def test_past_semester_dd_only_with_coverage_starting_mid_semester(cfg):
    s = by_name(c.semester_summaries(make_df(spring_rows() + current_rows()), cfg))["Spring 2026"]
    assert (s.start, s.end) == (SPRING.start, SPRING.end)
    assert s.total_days == 117
    assert s.covered_start == date(2026, 3, 21)
    assert s.covered_end == date(2026, 4, 29)
    assert s.covered_days == 40
    assert s.rate_days == 40

    assert s.me is None
    assert s.dd.used == pytest.approx(30.0)  # 12 + 4 + 4 - 4 + 11 + 3, the reversal nets off
    assert s.dd.per_day == pytest.approx(30 / 40)  # covered days, not the 117-day semester
    assert s.dd.per_week == pytest.approx(7 * 30 / 40)

    # split tender is one $11 meal; snack dollars are gross purchases
    assert (s.dd_meal_count, s.dd_meal_amount) == (2, pytest.approx(23.0))
    assert (s.dd_snack_count, s.dd_snack_amount) == (3, pytest.approx(11.0))

    assert [p.place for p in s.top_places] == ["Vending", "Cafe A"]
    vending, cafe = s.top_places
    assert (vending.visits, vending.me_swipes, vending.dd_spend) == (3, 0, pytest.approx(7.0))
    assert (cafe.visits, cafe.me_swipes, cafe.dd_spend) == (2, 0, pytest.approx(23.0))

    assert "Mar 21" in s.note and "Apr 29" in s.note
    assert "40 of 117 days" in s.note
    assert "meal exchange" in s.note.lower()  # says the ME pot is missing


def test_semester_with_no_data_at_all(cfg):
    s = by_name(c.semester_summaries(make_df(spring_rows() + current_rows()), cfg))["Fall 2025"]
    assert s.covered_start is None and s.covered_end is None
    assert s.covered_days == 0 and s.rate_days == 0
    assert s.me is None and s.dd is None
    assert (s.dd_meal_count, s.dd_meal_amount, s.dd_snack_count, s.dd_snack_amount) == (0, 0.0, 0, 0.0)
    assert s.top_places == []
    assert "no transactions" in s.note.lower()


def test_current_semester_is_clipped_to_observed_through(cfg):
    s = c.semester_summaries(make_df(spring_rows() + current_rows()), cfg)[-1]
    assert s.current and s.name == "Fall 2026"
    assert (s.start, s.end) == (date(2026, 8, 24), date(2026, 9, 6))
    assert s.total_days == 110
    # the whole observed window counts, including the quiet days at either edge
    assert (s.covered_start, s.covered_end, s.covered_days) == (date(2026, 8, 24), date(2026, 9, 6), 14)

    assert s.me.used == pytest.approx(4.0)  # Sep 8 swipe is after the as-of date; Aug 26 was reversed
    assert s.me.per_day == pytest.approx(4 / 14)
    assert s.me.per_week == pytest.approx(2.0)
    assert s.dd.used == pytest.approx(14.0)  # counted although it is before data_cutoff
    assert s.dd.per_day == pytest.approx(1.0)
    assert (s.dd_meal_count, s.dd_snack_count) == (1, 0)

    assert [p.place for p in s.top_places] == ["Dining Hall", "Cafe A", "Grill"]
    assert s.top_places[0].me_swipes == 3 and s.top_places[0].visits == 3
    assert "Sep 6" in s.note and "14 of 110 days" in s.note


def test_current_semester_follows_data_coverage_when_it_is_older_than_as_of(cfg):
    late = dataclasses.replace(cfg, as_of=date(2026, 10, 15))
    rows = [r for r in current_rows() if r["date"] <= date(2026, 9, 3)]
    rows.append(row("DD", "2026-09-03 13:00", "Cafe A", -6, dd_class="snack"))  # both pots end Sep 3
    s = c.semester_summaries(make_df(rows), late)[-1]
    assert s.end == s.covered_end == date(2026, 9, 3)
    assert s.covered_days == 11


def test_no_history_configured(cfg):
    out = c.semester_summaries(make_df(current_rows()), dataclasses.replace(cfg, history=()))
    assert len(out) == 1 and out[0].current
    assert out[0].me.used == pytest.approx(4.0)


def test_away_days_leave_the_rate_denominator_only_when_excluded(cfg):
    weekend = (AwayPeriod("Trip", date(2026, 8, 29), date(2026, 8, 30)),)
    df = make_df(current_rows())
    on = c.semester_summaries(df, dataclasses.replace(cfg, away_periods=weekend))[-1]
    assert (on.covered_days, on.rate_days) == (14, 12)
    assert on.me.per_day == pytest.approx(4 / 12)
    assert "2 away days" in on.note

    off = c.semester_summaries(df, dataclasses.replace(cfg, away_periods=weekend, exclude_away=False))[-1]
    assert (off.covered_days, off.rate_days) == (14, 14)
    assert "away" not in off.note


def test_fully_covered_past_semester_has_no_note(cfg):
    mini = SemesterDef("Mini", date(2026, 6, 1), date(2026, 6, 14))
    rows = [
        row("ME", "2026-06-01 12:00", "Dining Hall", -1),
        row("DD", "2026-06-02 12:00", "Cafe A", -9, dd_class="meal"),
        row("ME", "2026-06-14 12:00", "Dining Hall", -1),
    ]
    s = c.semester_summaries(make_df(rows + current_rows()), dataclasses.replace(cfg, history=(mini,)))[0]
    assert (s.covered_days, s.total_days) == (14, 14)
    assert s.me.per_week == pytest.approx(1.0)
    assert s.note == ""


def test_pot_with_only_non_usage_rows_reports_zero_use(cfg):
    mini = SemesterDef("Mini", date(2026, 6, 1), date(2026, 6, 14))
    rows = [
        row("DD", "2026-06-01 09:00", "Deposit", 50, kind="load"),
        row("ME", "2026-06-01 12:00", "Dining Hall", -1),
        row("ME", "2026-06-14 12:00", "Dining Hall", -1),
    ]
    s = c.semester_summaries(make_df(rows), dataclasses.replace(cfg, history=(mini,)))[0]
    assert s.dd is not None and s.dd.used == 0.0 and s.dd.per_day == 0.0


def test_empty_ledger_never_crashes(cfg):
    empty = make_df([])
    out = c.semester_summaries(empty, cfg)
    assert [s.name for s in out] == ["Spring 2026", "Fall 2025", "Fall 2026"]
    assert all(s.covered_start is None and s.me is None and s.dd is None and s.note for s in out)
    weekly = c.weekly_usage(empty, cfg)
    assert weekly.empty and list(weekly.columns) == c.WEEKLY_COLUMNS


def test_before_the_semester_starts(cfg):
    early = dataclasses.replace(cfg, as_of=date(2026, 8, 1))
    s = c.semester_summaries(make_df(spring_rows()), early)[-1]
    assert s.current and s.covered_start is None and s.covered_days == 0
    assert "started" in s.note


@pytest.mark.parametrize(
    "start, expected",
    [(date(2026, 8, 24), "Fall 2026"), (date(2027, 1, 13), "Spring 2027"), (date(2026, 6, 10), "Summer 2026")],
)
def test_current_semester_name_comes_from_its_start_date(cfg, start, expected):
    assert c.current_semester_name(dataclasses.replace(cfg, semester_start=start)) == expected


# ---------------------------------------------------------------------------
# weekly_usage
# ---------------------------------------------------------------------------


def test_weekly_usage_numbers_weeks_from_each_semesters_own_start(cfg):
    weekly = c.weekly_usage(make_df(spring_rows() + current_rows()), cfg)
    assert list(weekly.columns) == c.WEEKLY_COLUMNS
    assert set(weekly["semester"]) == {"Spring 2026", "Fall 2026"}  # Fall 2025 has nothing to plot

    spring = weekly[weekly["semester"] == "Spring 2026"]
    assert set(spring["pot"]) == {"DD"}  # no ME rows that semester, so no ME zeros either
    # covered weeks only (10..16), with the quiet ones filled in as zero
    assert list(spring["week"]) == [10, 11, 12, 13, 14, 15, 16]
    assert list(spring["amount"]) == pytest.approx([12, 4, 0, 11, 0, 0, 3])
    assert list(spring["days"]) == [2, 7, 7, 7, 7, 7, 3]  # Mar 21-22 and Apr 27-29 are part weeks
    assert list(spring["complete"]) == [False, True, True, True, True, True, False]
    assert spring["week_start"].iloc[0] == date(2026, 3, 16)
    assert not spring["current"].any()


def test_weekly_usage_current_semester_stops_at_observed_through(cfg):
    weekly = c.weekly_usage(make_df(spring_rows() + current_rows()), cfg)
    fall = weekly[weekly["semester"] == "Fall 2026"]
    assert fall["current"].all()
    me = fall[fall["pot"] == "ME"]
    dd = fall[fall["pot"] == "DD"]
    assert list(me["week"]) == [1, 2] and list(me["amount"]) == pytest.approx([2, 2])
    assert list(dd["week"]) == [1, 2] and list(dd["amount"]) == pytest.approx([14, 0])
    assert list(me["complete"]) == [True, True]

    midweek = dataclasses.replace(cfg, as_of=date(2026, 9, 2))
    me = c.weekly_usage(make_df(current_rows()), midweek).query("pot == 'ME'")
    assert list(me["days"]) == [7, 3] and list(me["complete"]) == [True, False]
    assert list(me["amount"]) == pytest.approx([2, 1])


def test_weekly_usage_matches_summary_totals(cfg):
    df = make_df(spring_rows() + current_rows())
    weekly = c.weekly_usage(df, cfg)
    totals = weekly.groupby(["semester", "pot"])["amount"].sum()
    for s in c.semester_summaries(df, cfg):
        for pot, usage in (("ME", s.me), ("DD", s.dd)):
            if usage is not None:
                assert totals[(s.name, pot)] == pytest.approx(usage.used)


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------


def test_weekly_overlay_chart_draws_one_line_per_semester(cfg):
    weekly = c.weekly_usage(make_df(spring_rows() + current_rows()), cfg)
    fig = cc.weekly_overlay_chart(weekly, "DD")
    assert isinstance(fig, go.Figure)
    assert [t.name for t in fig.data] == ["Spring 2026", "Fall 2026"]
    current = fig.data[-1]
    assert current.line.color == COLORS["dd"]
    assert fig.data[0].line.color != COLORS["dd"]
    assert list(fig.data[0].x) == [10, 11, 12, 13, 14, 15, 16]

    me = cc.weekly_overlay_chart(weekly, "ME")
    assert [t.name for t in me.data] == ["Fall 2026"]
    assert me.data[0].line.color == COLORS["me"]


def test_weekly_overlay_chart_handles_no_data(cfg):
    fig = cc.weekly_overlay_chart(c.weekly_usage(make_df([]), cfg), "ME")
    assert isinstance(fig, go.Figure) and len(fig.data) == 0
    assert "no data" in fig.layout.title.text


def test_per_day_comparison_chart_has_a_bar_per_semester_and_pot(cfg):
    summaries = c.semester_summaries(make_df(spring_rows() + current_rows()), cfg)
    fig = cc.per_day_comparison_chart(summaries)
    assert isinstance(fig, go.Figure)
    me, dd = fig.data
    assert (me.marker.color, dd.marker.color) == (COLORS["me"], COLORS["dd"])
    names = ["Spring 2026", "Fall 2025", "Fall 2026"]
    assert list(me.x) == names and list(dd.x) == names
    assert list(me.y) == [None, None, pytest.approx(4 / 14)]
    assert list(dd.y) == [pytest.approx(0.75), None, pytest.approx(1.0)]
    assert me.xaxis != dd.xaxis  # two panels, never two scales on one axis


def test_per_day_comparison_chart_handles_no_summaries():
    fig = cc.per_day_comparison_chart([])
    assert isinstance(fig, go.Figure)
    assert "no data" in fig.layout.title.text
