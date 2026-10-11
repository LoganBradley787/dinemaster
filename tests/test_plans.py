"""Tests for dinemaster.plans (which plan size fits)."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from dinemaster import plans
from dinemaster.config import PlanOption, load_config
from dinemaster.forecast import PotForecast

PLANS = (
    PlanOption("All Access", None, 150.0, 3670.0),
    PlanOption("Block 160", 160.0, 300.0, 2190.0),
    PlanOption("Block 100", 100.0, 300.0, 1600.0),
)


def me_forecast(p50: float, lo: float, hi: float) -> PotForecast:
    line = pd.Series([109.0, p50])
    return PotForecast("ME", 109.0, line, line, line, (10, 90), p50, lo, hi, 1.0, None, None, None, None)


def cfg(**overrides):
    return replace(load_config(), **{"starting_me": 160.0, "meal_price": 15.0, "plans": PLANS, **overrides})


def test_each_plan_at_the_projected_usage():
    fits = plans.plan_fits(cfg(), me_forecast(29.0, 20.0, 37.0))

    assert (fits.used, fits.used_lo, fits.used_hi) == (131.0, 123.0, 140.0)
    unlimited, mine, smaller = fits.rows
    assert (unlimited.unused, unlimited.short, unlimited.food_cost) == (0.0, 0.0, 3520.0)
    assert mine.current and (mine.unused, mine.short, mine.food_cost) == (29.0, 0.0, 1890.0)
    assert mine.per_meal == pytest.approx(1890 / 131) and mine.versus_current == 0
    assert not smaller.current and (smaller.unused, smaller.short, smaller.extra) == (0.0, 31.0, 465.0)
    assert smaller.food_cost == 1765.0 and smaller.versus_current == -125.0


def test_nothing_to_compare():
    assert "no plans listed" in plans.plan_fits(cfg(plans=()), me_forecast(29.0, 20.0, 37.0)).reason
    empty = pd.Series(dtype=float)
    unavailable = PotForecast("ME", 160.0, empty, empty, empty, (10, 90), None, None, None, None, None, None, None, "no data")
    assert plans.plan_fits(cfg(), unavailable).reason == "no data"
