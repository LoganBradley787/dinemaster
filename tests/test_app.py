"""Smoke tests for app.py using streamlit.testing.v1.AppTest.

Each test writes its own temp config.toml (pointing raw_dir/ledger_path at
tmp_path) and points the DINEMASTER_CONFIG env var at it, so the app under
test never touches the project's real config.toml or raw-data/. All CSV rows
here are synthetic.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

CONFIG_TEMPLATE = """
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
snack_patterns = ["Gastons", "Supply 1819", "Vending", "Bodega"]
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
"""

HEADER = ["Date", "Description", "Amount", "Balance"]


def write_csv(dir_path: Path, filename: str, rows: list[tuple[str, str, float, float]]) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    with open(dir_path / filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow(row)


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(CONFIG_TEMPLATE)
    return config_path


def run_app(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> AppTest:
    monkeypatch.setenv("DINEMASTER_CONFIG", str(config_path))
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    return at


def test_app_runs_with_synthetic_data(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    raw_dir = tmp_path / "raw-data"
    write_csv(
        raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-02-28.csv",
        [
            ("2026-08-21 08:00:00", "Newcomb", -1, 159),
            ("2026-08-22 08:00:00", "Newcomb", -1, 158),
            ("2026-09-10 08:00:00", "Runk", -1, 157),
        ],
    )
    write_csv(
        raw_dir,
        "Dining Dollars_statement_2026-01-01_to_2026-02-28.csv",
        [
            ("2026-08-21 12:00:00", "Crossroads", -10.0, 350.0),
            ("2026-08-22 12:00:00", "Bodega", -5.0, 345.0),
            ("2026-09-05 12:00:00", "Deposit", 20.0, 365.0),
        ],
    )

    at = run_app(monkeypatch, config_path)

    assert not at.exception
    assert any("DineMaster" in t.value for t in at.title)


def test_app_runs_with_empty_raw_dir(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    # Deliberately do not create raw-data/ at all.

    at = run_app(monkeypatch, config_path)

    assert not at.exception
    infos = [i.value for i in at.info]
    assert any("No dining data found" in msg for msg in infos)
