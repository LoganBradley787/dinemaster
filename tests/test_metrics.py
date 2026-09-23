"""Tests for dinemaster.metrics — written before the implementation (TDD).

All DataFrames are built by hand with the columns documented in the spec's
Ingest "Public API" section: account, pot, timestamp, date, description,
amount, balance, kind, dd_class. No import of dinemaster.ingest.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pandas as pd
import pytest

from dinemaster.config import load_config
from dinemaster import metrics as m

COLUMNS = ["account", "pot", "timestamp", "date", "description", "amount", "balance", "kind", "dd_class"]


def row(account, pot, ts, description, amount, balance, kind="usage", dd_class=None):
    timestamp = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    return {
        "account": account,
        "pot": pot,
        "timestamp": timestamp,
        "date": timestamp.date(),
        "description": description,
        "amount": amount,
        "balance": balance,
        "kind": kind,
        "dd_class": dd_class,
    }


def make_df(rows):
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    return pd.DataFrame(rows, columns=COLUMNS)


@pytest.fixture
def base_cfg():
    return load_config()


# ---------------------------------------------------------------------------
# Day counting
# ---------------------------------------------------------------------------


def test_day_counts_basic(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23))
    dc = m.day_counts(cfg)
    assert dc.t == 120
    assert dc.de == 34
    assert dc.dr == 86
    assert dc.a == date(2026, 9, 23)
    assert dc.notice is None
    # Thanksgiving (Nov 25-29, 5 days) is entirely after A, entirely remaining.
    assert dc.away_tot == 5
    assert dc.away_el == 0
    assert dc.away_rem == 5
    assert dc.t_away == 115
    assert dc.de_away == 34
    assert dc.dr_away == 81


def test_day_counts_as_of_before_semester_clamped(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 1, 1))
    dc = m.day_counts(cfg)
    assert dc.a == date(2026, 8, 20)  # S - 1
    assert dc.de == 0
    assert dc.dr == 120
    assert dc.notice is not None


def test_day_counts_as_of_after_semester_clamped(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2027, 1, 1))
    dc = m.day_counts(cfg)
    assert dc.a == date(2026, 12, 18)  # E
    assert dc.dr == 0
    assert dc.de == 120
    assert dc.notice is not None


def test_day_counts_dr_zero_at_semester_end(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 12, 18))
    dc = m.day_counts(cfg)
    assert dc.dr == 0
    assert dc.notice is None  # exactly E is in-range, no clamp needed


# ---------------------------------------------------------------------------
# Usage & balances
# ---------------------------------------------------------------------------


def test_balances_simple_usage(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23))
    rows = [
        row("Block 160 Meals", "ME", "2026-08-20 00:00:00", "Deposit", 160.0, 160.0, kind="load"),
        row("Dining Dollars", "DD", "2026-08-20 00:00:00", "Deposit", 360.0, 360.0, kind="load"),
    ]
    bal = 160
    for i in range(1, 44):  # 43 ME swipes of -1
        bal -= 1
        rows.append(row("Block 160 Meals", "ME", f"2026-09-01 12:{i % 60:02d}:00", "Meal Swipe", -1.0, bal))
    dd_bal = 360.0
    for desc, amt, ts in [
        ("Dining Hall", -30.0, "2026-09-05 12:00:00"),
        ("Dining Hall", -12.5, "2026-09-10 12:00:00"),
        ("Vending Machine", -3.33, "2026-09-15 12:00:00"),
        ("Bodega", -9.0, "2026-09-20 12:00:00"),
    ]:
        dd_bal += amt
        rows.append(row("Dining Dollars", "DD", ts, desc, amt, round(dd_bal, 2)))
    df = make_df(rows)
    b = m.balances(df, cfg)
    assert b.me_used == pytest.approx(43.0)
    assert b.dd_used == pytest.approx(54.83)
    assert b.me_left == pytest.approx(117.0)
    assert b.dd_left == pytest.approx(305.17)
    assert b.me_warning is None
    assert b.dd_warning is None


def test_balances_reconciliation_warning_on_mismatch(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23))
    # balance column disagrees with the amount trail -> should warn.
    rows = [row("Block 160 Meals", "ME", "2026-09-01 12:00:00", "Meal Swipe", -1.0, 999.0)]
    df = make_df(rows)
    b = m.balances(df, cfg)
    assert b.me_warning is not None


def test_balances_deposit_mismatch_warning(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23))
    rows = [row("Dining Dollars", "DD", "2026-08-21 08:00:00", "Deposit", 100.0, 100.0, kind="load")]
    df = make_df(rows)
    b = m.balances(df, cfg)
    # deposits (100) != configured starting_dd (360) -> warn
    assert b.dd_warning is not None


def test_reversal_reduces_usage(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23))
    rows = [
        row("Block 160 Meals", "ME", "2026-09-01 12:00:00", "Meal Swipe", -1.0, 159.0),
        row("Block 160 Meals", "ME", "2026-09-01 12:05:00", "Meal Swipe", -1.0, 158.0),
        row("Block 160 Meals", "ME", "2026-09-02 09:00:00", "Reversal", 1.0, 159.0, kind="reversal"),
    ]
    df = make_df(rows)
    b = m.balances(df, cfg)
    assert b.me_used == pytest.approx(1.0)
    assert b.me_left == pytest.approx(159.0)


def test_dd_split_tender_purchase_counted_once():
    cfg = dataclasses.replace(load_config(), as_of=date(2026, 9, 23))
    ts = "2026-09-05 12:00:00"
    rows = [
        row("Dining Dollars", "DD", ts, "Combo Meal", -5.0, 355.0, dd_class="meal"),
        row("Promotional Dining Dollars", "DD", ts, "Combo Meal", -4.0, 56.0, dd_class="meal"),
    ]
    df = make_df(rows)
    bd = m.dd_breakdown(df, cfg)
    assert bd.meal_count == 1
    assert bd.meal_amount == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# daily()
# ---------------------------------------------------------------------------


def test_daily_covers_full_range_and_balances(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 8, 25))
    rows = [
        row("Block 160 Meals", "ME", "2026-08-22 12:00:00", "Meal Swipe", -1.0, 159.0),
        row("Dining Dollars", "DD", "2026-08-23 12:00:00", "Dining Hall", -10.0, 350.0, dd_class="meal"),
        row("Dining Dollars", "DD", "2026-08-23 13:00:00", "Vending Machine", -2.0, 348.0, dd_class="snack"),
    ]
    df = make_df(rows)
    d = m.daily(df, cfg)
    assert list(d.index) == [date(2026, 8, 21) + pd.Timedelta(days=i) for i in range(5)]
    assert d.loc[date(2026, 8, 21), "me_bal"] == pytest.approx(160.0)
    assert d.loc[date(2026, 8, 22), "me_swipes"] == pytest.approx(1.0)
    assert d.loc[date(2026, 8, 22), "me_bal"] == pytest.approx(159.0)
    assert d.loc[date(2026, 8, 23), "dd_meals"] == 1
    assert d.loc[date(2026, 8, 23), "dd_snacks"] == 1
    assert d.loc[date(2026, 8, 23), "meals"] == pytest.approx(1.0)  # 1 dd meal, 0 me swipes
    assert d.loc[date(2026, 8, 23), "dd_bal"] == pytest.approx(348.0)
    assert d.loc[date(2026, 8, 25), "me_bal"] == pytest.approx(159.0)  # unchanged, holds forward


# ---------------------------------------------------------------------------
# Series helpers
# ---------------------------------------------------------------------------


def test_ideal_series_bounds(base_cfg):
    s = m.ideal_series(base_cfg, start_amount=160.0, apply_away=False)
    assert s.index[0] == date(2026, 8, 21)
    assert s.index[-1] == date(2026, 12, 18)
    assert s.iloc[-1] == pytest.approx(0.0)
    assert s.iloc[0] == pytest.approx(160.0 * 119 / 120)


def test_ideal_series_flat_across_away_days(base_cfg):
    cfg = base_cfg
    s = m.ideal_series(cfg, start_amount=160.0, apply_away=True)
    nov24 = s.loc[date(2026, 11, 24)]
    for d in [date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 28), date(2026, 11, 29)]:
        assert s.loc[d] == pytest.approx(nov24)
    assert s.loc[date(2026, 11, 30)] < nov24


def test_projection_series_starts_at_left(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23))
    s = m.projection_series(cfg, a=date(2026, 9, 23), left=117.0, rate=1.2647058823529411, apply_away=False)
    assert s.iloc[0] == pytest.approx(117.0)
    assert (s >= -1e-9).all()


# ---------------------------------------------------------------------------
# run_out_two_per_day
# ---------------------------------------------------------------------------


def test_run_out_two_per_day_me_only(base_cfg):
    cfg = base_cfg
    r = m.run_out_two_per_day(cfg, a=date(2026, 9, 23), available=117.0, apply_away=False)
    assert r.date == date(2026, 11, 20)


def test_run_out_two_per_day_with_dd(base_cfg):
    cfg = base_cfg
    r = m.run_out_two_per_day(cfg, a=date(2026, 9, 23), available=137.0, apply_away=False)
    assert r.date == date(2026, 11, 30)


def test_run_out_two_per_day_with_dd_away(base_cfg):
    cfg = base_cfg
    r = m.run_out_two_per_day(cfg, a=date(2026, 9, 23), available=137.0, apply_away=True)
    assert r.date == date(2026, 12, 5)


def test_run_out_two_per_day_already_exhausted(base_cfg):
    cfg = base_cfg
    r = m.run_out_two_per_day(cfg, a=date(2026, 9, 23), available=1.0, apply_away=False)
    assert r.date is None
    assert r.reason is not None


# ---------------------------------------------------------------------------
# burn rate / projection
# ---------------------------------------------------------------------------


def test_burn_rate_none_when_no_usage():
    assert m.burn_rate(0.0, 34) is None


def test_burn_rate_none_when_no_elapsed_days():
    assert m.burn_rate(10.0, 0) is None


def test_burn_projection_smoke_dd(base_cfg):
    cfg = base_cfg
    rate = m.burn_rate(54.83, 34)
    assert rate == pytest.approx(1.6126470588235293)
    result = m.burn_projection(cfg, a=date(2026, 9, 23), rate=rate, left=305.17, dr_eff=86, apply_away=False)
    assert result.leftover_display == pytest.approx(166.48, abs=0.5)
    assert result.lasts_past_end is True
    assert result.runout_date == date(2027, 4, 1)  # projection continues past E
    assert result.spare_days == pytest.approx(305.17 / rate - 86)


def test_burn_projection_not_enough_data(base_cfg):
    result = m.burn_projection(base_cfg, a=date(2026, 9, 23), rate=None, left=100.0, dr_eff=86, apply_away=False)
    assert result.reason == "not enough data"
    assert result.runout_date is None
    assert result.leftover_display is None


def test_burn_projection_runs_out_before_end(base_cfg):
    cfg = base_cfg
    # Huge rate -> exhausts quickly, well before semester end.
    result = m.burn_projection(cfg, a=date(2026, 9, 23), rate=50.0, left=100.0, dr_eff=86, apply_away=False)
    assert result.lasts_past_end is False
    assert result.runout_date is not None
    assert result.spare_days < 0
    assert result.shortfall > 0


# ---------------------------------------------------------------------------
# day_mix
# ---------------------------------------------------------------------------


def test_day_mix_normal():
    dm = m.day_mix(120.0, 86.0)  # M < 2R, M > R -> mix of 1s and 2s, no note
    assert dm.two_meal_days == pytest.approx(120 - 86)
    assert dm.one_meal_days == pytest.approx(2 * 86 - 120)
    assert dm.note is None


def test_day_mix_surplus():
    dm = m.day_mix(200.0, 50.0)  # M > 2R
    assert dm.note is not None
    assert "surplus" in dm.note.lower()


def test_day_mix_shortfall():
    dm = m.day_mix(10.0, 50.0)  # M < R
    assert dm.note is not None
    assert "shortfall" in dm.note.lower()


def test_day_mix_zero_days():
    dm = m.day_mix(10.0, 0.0)
    assert dm.note == "semester over"


# ---------------------------------------------------------------------------
# what_if
# ---------------------------------------------------------------------------


def test_what_if_me_then_dd(base_cfg):
    cfg = base_cfg
    result = m.what_if(cfg, a=date(2026, 9, 23), me_left=5.0, dd_left=100.0, meals_per_day=1.0, apply_away=False)
    # ME (5 meals) exhausts after 5 days, then DD starts draining.
    assert result.me_runout == date(2026, 9, 28)
    assert result.dd_runout is not None
    assert result.me_end == 0.0


def test_what_if_zero_rate_no_consumption(base_cfg):
    result = m.what_if(base_cfg, a=date(2026, 9, 23), me_left=5.0, dd_left=100.0, meals_per_day=0.0, apply_away=False)
    assert result.me_end == 5.0
    assert result.dd_end == 100.0
    assert result.reason is not None


# ---------------------------------------------------------------------------
# compute_metrics — full smoke test scenario
# ---------------------------------------------------------------------------


def _smoke_df():
    rows = [
        row("Block 160 Meals", "ME", "2026-08-20 00:00:00", "Deposit", 160.0, 160.0, kind="load"),
        row("Dining Dollars", "DD", "2026-08-20 00:00:00", "Deposit", 360.0, 360.0, kind="load"),
    ]
    bal = 160
    for i in range(1, 44):
        bal -= 1
        rows.append(row("Block 160 Meals", "ME", f"2026-09-01 12:{i % 60:02d}:00", "Meal Swipe", -1.0, bal))
    dd_bal = 360.0
    for desc, amt, ts, dd_class in [
        ("Dining Hall", -30.0, "2026-09-05 12:00:00", "meal"),
        ("Dining Hall", -12.5, "2026-09-10 12:00:00", "meal"),
        ("Vending Machine", -3.33, "2026-09-15 12:00:00", "snack"),
        ("Bodega", -9.0, "2026-09-20 12:00:00", "snack"),
    ]:
        dd_bal += amt
        rows.append(row("Dining Dollars", "DD", ts, desc, amt, round(dd_bal, 2), dd_class=dd_class))
    return make_df(rows)


def test_compute_metrics_smoke_scenario(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23))
    df = _smoke_df()
    result = m.compute_metrics(df, cfg)

    assert result.day_counts.dr == 86
    assert result.day_counts.de == 34
    assert result.day_counts.t == 120

    assert result.balances.me_left == pytest.approx(117.0)
    assert result.balances.dd_left == pytest.approx(305.17)

    pace_plain = result.pace["plain"]
    assert pace_plain.me_only == pytest.approx(1.36, abs=0.01)
    assert pace_plain.with_dd == pytest.approx(1.59, abs=0.01)

    run_out_plain = result.run_out["plain"]
    assert run_out_plain["me_only"].date == date(2026, 11, 20)
    assert run_out_plain["with_dd"].date == date(2026, 11, 30)

    run_out_away = result.run_out["away"]
    assert run_out_away["with_dd"].date == date(2026, 12, 5)

    dd_burn_plain = result.burn["plain"]["dd"]
    assert dd_burn_plain.leftover_display == pytest.approx(166.0, abs=1.0)

    me_burn_plain = result.burn["plain"]["me"]
    assert me_burn_plain.rate == pytest.approx(1.2647, abs=0.001)


def test_compute_metrics_zero_usage_edge_case(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23))
    df = make_df([])
    result = m.compute_metrics(df, cfg)
    assert result.balances.me_used == 0.0
    assert result.burn["plain"]["me"].reason == "not enough data"
    assert result.burn["plain"]["dd"].reason == "not enough data"


def test_compute_metrics_as_of_before_start(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 1, 1))
    df = make_df([])
    result = m.compute_metrics(df, cfg)
    assert result.day_counts.de == 0
    assert result.day_counts.notice is not None


def test_compute_metrics_as_of_after_end(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2027, 1, 1))
    df = make_df([])
    result = m.compute_metrics(df, cfg)
    assert result.day_counts.dr == 0
    # Dr = 0 -> pace shows "semester over" instead of a value.
    assert result.pace["plain"].me_only is None
    assert result.pace["plain"].reason == "semester over"


def test_compute_metrics_dr_zero_semester_over(base_cfg):
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 12, 18))
    df = make_df([])
    result = m.compute_metrics(df, cfg)
    assert result.day_counts.dr == 0
    assert result.pace["plain"].reason == "semester over"
    assert result.day_mix["plain"]["me_only"].note == "semester over"


def test_compute_metrics_m_greater_than_2r(base_cfg):
    # Force a huge ME balance so pooled meals M > 2R (surplus case).
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23), starting_me=1000.0)
    df = make_df([])
    result = m.compute_metrics(df, cfg)
    dm = result.day_mix["plain"]["me_only"]
    assert "surplus" in dm.note.lower()


def test_compute_metrics_m_less_than_r(base_cfg):
    # Very small starting balance relative to days remaining -> shortfall.
    cfg = dataclasses.replace(base_cfg, as_of=date(2026, 9, 23), starting_me=10.0)
    df = make_df([])
    result = m.compute_metrics(df, cfg)
    dm = result.day_mix["plain"]["me_only"]
    assert "shortfall" in dm.note.lower()


def test_burn_projection_me_runout_date_past_end(base_cfg):
    rate = m.burn_rate(43, 34)
    result = m.burn_projection(base_cfg, a=date(2026, 9, 23), rate=rate, left=117, dr_eff=86, apply_away=False)
    # 117 / 1.2647 = 92.5 days of supply from 9/24 -> crosses zero on day 93 (Dec 25)
    assert result.runout_date == date(2026, 12, 25)
    assert result.spare_days == pytest.approx(6.5, abs=0.1)
    assert result.lasts_past_end is True
