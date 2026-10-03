"""Smoke tests for app.py using streamlit.testing.v1.AppTest.

Each test writes its own temp config.toml (pointing raw_dir/ledger_path at
tmp_path) and points the DINEMASTER_CONFIG env var at it, so the app under
test never touches the project's real config.toml or raw-data/. All CSV rows
here are synthetic.
"""

from __future__ import annotations

import csv
import dataclasses
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from dinemaster import charts
from dinemaster.config import Marker, load_config

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

[forecast]
half_life_days = 21
prior_days = 2.0
simulations = 200
seed = 7
band = [10, 90]

[budget]
max_meals_per_day = 3

[calendar]
ics_dir = "calendar"
ics_urls = []
away_patterns = ["(?i)break", "(?i)recess"]
marker_patterns = ["(?i)reading day", "(?i)exam"]

[habits]
late_night_start = 21
late_night_end = 4

[[history.semesters]]
name = "Spring 2026"
start = 2026-01-14
end = 2026-05-09
"""

TABS = ["Balances", "Forecast", "Daily plan", "Usage", "Habits", "Compare", "Plan math", "What-if", "Data"]

# The fixed "today" the richer tests pin the as-of date to, and the day their exports end.
AS_OF = date(2026, 9, 14)
COVERED_THROUGH = date(2026, 9, 13)

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


def write_semester(tmp_path: Path) -> None:
    """Three and a bit weeks of made-up usage with a weekday pattern, a late-night snack habit, and a
    little dining-dollar history from the spring. Exports end on COVERED_THROUGH."""
    raw_dir = tmp_path / "raw-data"
    me_rows, dd_rows = [], [
        ("2026-04-06 12:30:00", "Crossroads", -11.0, 89.0),
        ("2026-04-08 22:15:00", "Gastons", -4.0, 85.0),
        ("2026-04-20 12:40:00", "Crossroads", -12.0, 73.0),
    ]
    me_bal, dd_bal = 160, 360.0
    day = date(2026, 8, 21)
    while day <= COVERED_THROUGH:
        swipes = {3: 2, 5: 0, 6: 0}.get(day.weekday(), 1)  # two on Thursdays, none at weekends
        for i in range(swipes):
            me_bal -= 1
            me_rows.append((f"{day} {12 + 6 * i}:05:00", "Newcomb" if i == 0 else "Runk", -1, me_bal))
        if day.weekday() == 5:  # a dining-dollar meal on Saturdays
            dd_bal -= 12.5
            dd_rows.append((f"{day} 13:00:00", "Crossroads", -12.5, dd_bal))
        if day.weekday() == 4:  # a late snack on Fridays
            dd_bal -= 4.25
            dd_rows.append((f"{day} 23:10:00", "Gastons", -4.25, dd_bal))
        day += timedelta(days=1)
    write_csv(raw_dir, f"Block 160 Meals_statement_2026-08-01_to_{COVERED_THROUGH}.csv", me_rows)
    write_csv(raw_dir, f"Dining Dollars_statement_2026-03-15_to_{COVERED_THROUGH}.csv", dd_rows)


def run_app_as_of(monkeypatch: pytest.MonkeyPatch, config_path: Path, as_of: date = AS_OF) -> AppTest:
    """Run the app, then pin the session's as-of date so the result doesn't depend on the real clock."""
    at = run_app(monkeypatch, config_path)
    assert not at.exception
    at.session_state["set_as_of"] = as_of
    at.run()
    return at


def text_of(at: AppTest) -> str:
    """Every piece of markdown-ish text on the page, joined, for substring checks."""
    kinds = (at.markdown, at.caption, at.subheader, at.info, at.warning)
    return "\n".join(el.value for kind in kinds for el in kind)


def write_ics(tmp_path: Path, name: str, events: list[tuple[str, str, str]]) -> None:
    """A calendar file of all-day events: (title, first day, day after the last day) as YYYYMMDD."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN"]
    for title, start, end in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{title}-{start}@test",
            f"SUMMARY:{title}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            "END:VEVENT",
        ]
    folder = tmp_path / "calendar"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text("\r\n".join([*lines, "END:VCALENDAR", ""]))


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
    assert [t.label for t in at.tabs] == TABS


def test_app_runs_with_empty_raw_dir(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    # Deliberately do not create raw-data/ at all.

    at = run_app(monkeypatch, config_path)

    assert not at.exception
    infos = [i.value for i in at.info]
    assert any("No dining data found" in msg for msg in infos)


def test_empty_raw_dir_still_shows_every_tab(tmp_path, monkeypatch):
    at = run_app(monkeypatch, write_config(tmp_path))

    assert not at.exception
    assert [t.label for t in at.tabs] == TABS
    text = text_of(at)
    assert "No forecast yet: no transactions loaded yet." in text
    assert "No observed weeks yet." in text
    assert "No calendars read." in text


def test_forecast_tab_shows_the_weekday_pattern_and_the_likely_range(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    write_semester(tmp_path)

    at = run_app_as_of(monkeypatch, config_path)

    assert not at.exception
    text = text_of(at)
    assert "**Thursday** is your heaviest" in text
    assert "Observed: Aug 21 – Sep 13 (24 days)" in text  # days after the exports end are forecast, not zeros
    assert "forecast for Sep 14 – Dec 18" in text
    labels = [metric.label for metric in at.metric]
    assert "Chance meal exchanges last" in labels
    assert "Chance dining dollars last" in labels
    assert labels.count("Likely left on Dec 18") == 2
    weekday_table = next(d.value for d in at.dataframe if "Days observed" in d.value.columns)
    assert list(weekday_table["Day"]) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert weekday_table["Days observed"].sum() == 24
    by_day = weekday_table.set_index("Day")["Meals / day"]
    assert by_day["Thursday"] == 2.0 and by_day["Sunday"] == 0.0


def test_forecast_metrics_escape_dollars_and_word_the_chance_like_the_tiles(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    write_semester(tmp_path)

    at = run_app_as_of(monkeypatch, config_path)

    assert not at.exception
    # Metric values render as markdown: "$a – $b" with bare dollar signs would be typeset as math.
    dd_range = next(m for m in at.metric if m.label == "Likely left on Dec 18" and "$" in m.value)
    assert "\\$" in dd_range.value and "$" not in dd_range.value.replace("\\$", "")
    assert "$" not in dd_range.delta.replace("\\$", "")
    # A simulated chance is never shown as certain; the tiles say ">99%" for the same number.
    chances = [m.value for m in at.metric if m.label.startswith("Chance ")]
    assert chances == [">99%", "97%"]  # meal exchanges (every simulation lasted), dining dollars


def test_daily_plan_tab_follows_the_weekday_pattern_and_toggles_dining_dollars(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    write_semester(tmp_path)

    at = run_app_as_of(monkeypatch, config_path)

    assert not at.exception
    headline = at.subheader[0].value
    assert "today" in headline and "tomorrow" in headline
    with_dd = text_of(at)
    assert "meals from dining dollars" in with_dd
    assert "Spend-down: both pots at zero on the last day" in with_dd
    assert "both reach zero on Dec 18" in with_dd
    assert "\\$" in with_dd and "$" not in with_dd.replace("\\$", "")  # every dollar sign is escaped
    fortnight = next(t.value for t in at.table if "Mon" in t.value.columns and str(t.value.index[0]).startswith("Week of"))
    assert fortnight.loc["Week of Sep 14", "Mon"].startswith("Sep 14 · ")
    assert sum(cell != "" for cell in fortnight.to_numpy().ravel()) == 14

    at.radio(key="plan_mode").set_value("Exchanges only").run()

    assert not at.exception
    assert "meals from dining dollars" not in text_of(at)


def test_habits_tab_has_streaks_late_night_and_a_week_picker(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    write_semester(tmp_path)

    at = run_app_as_of(monkeypatch, config_path)

    assert not at.exception
    labels = [metric.label for metric in at.metric]
    assert {"Current meal streak", "Longest meal streak", "Longest gap without a meal", "Late-night visits"} <= set(labels)
    picker = at.selectbox(key="recap_week")
    assert picker.value == date(2026, 9, 7)  # the latest week with observed days
    assert len(picker.options) == 4  # weeks of Aug 17, Aug 24, Aug 31, Sep 7
    assert "meals this week" in text_of(at)

    picker.set_value(date(2026, 8, 31)).run()

    assert not at.exception
    assert "meals the week of Aug 31" in text_of(at)


def test_compare_tab_lists_the_past_semester_with_its_coverage_note(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    write_semester(tmp_path)

    at = run_app_as_of(monkeypatch, config_path)

    assert not at.exception
    table = next(d.value for d in at.dataframe if "Semester" in d.value.columns)
    assert list(table["Semester"]) == ["Spring 2026", "Fall 2026 (now)"]
    spring = table.set_index("Semester").loc["Spring 2026"]
    assert spring["ME swipes"] == "—"  # no meal-exchange export for the spring
    assert spring["DD spent"] == "$27.00"
    text = text_of(at)
    assert "**Spring 2026** — Ledger covers Apr 6 – Apr 20 only" in text
    assert "No meal exchange records." in text


def test_calendar_file_adds_a_toggleable_away_period_and_markers(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    write_semester(tmp_path)
    write_ics(
        tmp_path,
        "school.ics",
        [
            ("Fall Break", "20261010", "20261014"),  # Oct 10-13 -> a new away period
            ("Thanksgiving Recess", "20261125", "20261130"),  # same days as the configured period -> not added
            ("Reading Day", "20261209", "20261210"),
            ("Final Exams", "20261210", "20261219"),
        ],
    )
    (tmp_path / "calendar" / "broken.ics").write_text("this is not a calendar")

    at = run_app_as_of(monkeypatch, config_path)

    assert not at.exception
    assert not list(at.sidebar.checkbox)  # settings live on the Configuration page, not the sidebar
    text = text_of(at)
    assert "Read: `school.ics`" in text
    assert "Calendar: broken.ics: couldn't be read as a calendar file; it was skipped." in text
    found = next(d.value for d in at.dataframe if "status" in d.value.columns)
    assert list(found["name"]) == ["Fall Break", "Thanksgiving Recess", "Reading Day", "Final Exams"]
    assert found["status"].str.startswith("added").tolist() == [True, False, False, False]
    with_break = next(t.value for t in at.table if "Mon" in t.value.columns and "Days left" in t.value.index)

    # Switching the calendar's away period off under Configuration gives its four days back to the plan.
    at.session_state["away_enabled_cal_2026-10-10_2026-10-13"] = False
    at.run()

    assert not at.exception
    without_break = next(t.value for t in at.table if "Mon" in t.value.columns and "Days left" in t.value.index)
    days_left = lambda table: sum(int(n) for n in table.loc["Days left"])  # noqa: E731
    assert days_left(without_break) == days_left(with_break) + 4


def test_markers_are_drawn_on_balance_charts():
    cfg = dataclasses.replace(
        load_config(),
        semester_start=date(2026, 8, 21),
        semester_end=date(2026, 12, 18),
        away_periods=(),
        markers=(
            Marker("Final Exams", date(2026, 12, 10), date(2026, 12, 18)),
            Marker("Reading Day", date(2026, 12, 9), date(2026, 12, 9)),
            Marker("Last spring's exams", date(2026, 5, 1), date(2026, 5, 8)),  # outside the semester: skipped
        ),
    )
    empty = pd.Series(dtype=float)

    fig = charts.balance_chart(cfg, date(2026, 10, 1), empty, empty, None, charts.COLORS["me"], "ME", "Meals left")

    labels = [a.text for a in fig.layout.annotations]
    assert labels == ["today", "semester end", "Reading Day", "Final Exams"]
    reading_day, finals = fig.layout.shapes[-2:]
    assert (reading_day.type, reading_day.x0, reading_day.x1) == ("line", "2026-12-09", "2026-12-09")
    assert (finals.type, finals.x0, finals.x1) == ("rect", "2026-12-10", "2026-12-18")
    assert fig.layout.annotations[-2].yshift != fig.layout.annotations[-1].yshift  # neighbours don't overlap

    assert len(charts.add_markers(charts.go.Figure(), dataclasses.replace(cfg, markers=())).layout.shapes) == 0
