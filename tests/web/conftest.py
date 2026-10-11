"""Fixtures for the web tests: a made-up semester in a temp folder, its config, and a TestClient.

Nothing here reads the project's config.toml, raw-data/ or data/. Every CSV row is synthetic.
The clock is pinned to AS_OF so pages do not depend on the day the tests run.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dinemaster.web.app import create_app

AS_OF = date(2026, 9, 14)  # a Monday: the pinned "today"
COVERED_THROUGH = date(2026, 9, 13)  # the last day the made-up exports cover

CONFIG = """
[semester]
start = 2026-08-21
end = 2026-12-18
data_cutoff = 2026-08-01

[plan]
starting_me = 160
starting_dd = 360.0
meal_price = 15.0
dd_rollover = false
dd_leftover_flag = 15.0

[plan.dd_rounding]
mode = "floor"
granularity = 1.0

[away]
exclude = true

[[away.periods]]
name = "Thanksgiving recess"
start = 2026-11-25
end = 2026-11-29
enabled = true

[classification]
meal_threshold = 8.0
snack_patterns = ["Gastons", "Vending"]
load_patterns = ["^Deposit$"]
adjustment_patterns = ["(?i)revoke"]

[files]
raw_dir = "raw-data"
ledger_path = "data/ledger.csv"
account_regex = "^(?P<account>.+?)_statement"

[files.columns]
timestamp = "Date"
description = "Description"
amount = "Amount"
balance = "Balance"

[[files.pots]]
pattern = "(?i)meal"
pot = "ME"

[[files.pots]]
pattern = "(?i)dining dollars"
pot = "DD"

[forecast]
half_life_days = 21
prior_days = 2.0
simulations = 200
seed = 7
band = [10, 90]

[calendar]
ics_dir = "calendar"
ics_urls = []
away_patterns = ["(?i)break", "(?i)recess"]
marker_patterns = ["(?i)reading day", "(?i)exam"]
"""


def write_csv(folder: Path, name: str, rows: list[tuple]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / name, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Description", "Amount", "Balance"])
        writer.writerows(rows)


def write_semester(root: Path) -> None:
    """Three and a bit weeks of made-up usage: one swipe on most days, two on Thursdays, none at
    weekends, a dining-dollar meal on Saturdays and a late snack on Fridays."""
    me_bal, dd_bal = 160, 360.0
    me_rows = [("2026-08-20 09:00:00", "Deposit", 160, me_bal)]
    dd_rows = [("2026-08-20 09:00:00", "Deposit", 360.0, dd_bal)]
    day = date(2026, 8, 21)
    while day <= COVERED_THROUGH:
        for i in range({3: 2, 5: 0, 6: 0}.get(day.weekday(), 1)):
            me_bal -= 1
            me_rows.append((f"{day} {12 + 6 * i}:05:00", "North Hall" if i == 0 else "South Hall", -1, me_bal))
        if day.weekday() == 5:
            dd_bal -= 12.5
            dd_rows.append((f"{day} 13:00:00", "Crossroads Grill", -12.5, dd_bal))
        if day.weekday() == 4:
            dd_bal -= 4.25
            dd_rows.append((f"{day} 23:10:00", "Gastons", -4.25, dd_bal))
        day += timedelta(days=1)
    write_csv(root / "raw-data", f"Block 160 Meals_statement_2026-08-01_to_{COVERED_THROUGH}.csv", me_rows)
    write_csv(root / "raw-data", f"Dining Dollars_statement_2026-08-01_to_{COVERED_THROUGH}.csv", dd_rows)


class Site:
    """A temp project folder: `config_path`, and `client()` for a TestClient with the clock pinned."""

    def __init__(self, root: Path):
        self.root = root
        self.config_path = root / "config.toml"
        self.config_path.write_text(CONFIG)

    def edit_config(self, old: str, new: str) -> None:
        text = self.config_path.read_text()
        assert old in text
        self.config_path.write_text(text.replace(old, new))

    def client(self, today: date = AS_OF) -> TestClient:
        return TestClient(create_app(self.config_path, today=lambda: today), follow_redirects=False)


@pytest.fixture
def site(tmp_path) -> Site:
    """A project folder with the made-up semester in it."""
    write_semester(tmp_path)
    return Site(tmp_path)


@pytest.fixture
def empty_site(tmp_path) -> Site:
    """A project folder with a config and no exports (its own folder, so a test can use both sites)."""
    root = tmp_path / "empty"
    root.mkdir()
    return Site(root)


@pytest.fixture
def client(site) -> TestClient:
    return site.client()


@pytest.fixture
def empty_client(empty_site) -> TestClient:
    return empty_site.client()
