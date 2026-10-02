"""Generate a made-up dining history so you can try DineMaster without your own exports.

    uv run python demo/make_demo.py
    DINEMASTER_CONFIG=demo/config.toml uv run streamlit run app.py

Everything it writes (demo/config.toml, demo/raw-data/, demo/calendar/) is fictional and
dated relative to today, so the demo always looks like a semester about six weeks in.
"""

from __future__ import annotations

import csv
import random
import shutil
from datetime import date, datetime, time, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 42

# Average meals per weekday (Mon..Sun): busy midweek, quiet weekends
WEEKDAY_MEALS = [1.6, 1.2, 1.4, 1.8, 0.8, 0.6, 0.7]
MEAL_SPOTS = ["Hilltop Grill", "Hilltop Grill", "Noodle Bar", "Noodle Bar", "Green Bowl", "Sunrise Cafe", "Taco Truck"]
SNACK_SPOTS = ["Corner Market", "Corner Market", "Corner Market", "Vending - Library"]

CONFIG = """# Demo configuration written by demo/make_demo.py — fictional data.

[semester]
start = {start}
end = {end}
data_cutoff = {cutoff}

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
name = "Long weekend home"
start = {away_start}
end = {away_end}
enabled = true

[classification]
meal_threshold = 8.0
snack_patterns = ["Market", "Vending"]
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

[calendar]
ics_dir = "calendar"
away_patterns = ["(?i)break", "(?i)recess"]
marker_patterns = ["(?i)reading day", "(?i)exam"]

[[history.semesters]]
name = "Last semester"
start = {prev_start}
end = {prev_end}
"""


def at(day: date, lo: float, hi: float, rng: random.Random) -> datetime:
    """A minute-precision timestamp on `day` between hours lo and hi."""
    minutes = int(rng.uniform(lo, hi) * 60)
    return datetime.combine(day, time(0)) + timedelta(minutes=minutes)


def meals_for(day: date, rng: random.Random) -> int:
    mean = WEEKDAY_MEALS[day.weekday()]
    return min(3, sum(rng.random() < mean / 3 for _ in range(3)) + (rng.random() < 0.08))


def write_statement(account: str, rows: list[tuple[datetime, str, float]], first: date, last: date, opening: float = 0.0) -> None:
    """Write rows the way the dining site exports them: newest first, with a running balance."""
    rows.sort(key=lambda r: r[0])
    balance, out = opening, []
    for ts, desc, amount in rows:
        balance = round(balance + amount, 2)
        out.append((ts.strftime("%Y-%m-%d %H:%M:00"), desc, amount, balance))
    path = HERE / "raw-data" / f"{account}_statement_{first}_to_{last}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Description", "Amount", "Balance"])
        writer.writerows(reversed(out))


def ics(events: list[tuple[str, date, date]]) -> str:
    """A minimal calendar file of all-day events (end date inclusive here, exclusive in the file)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//dinemaster//demo//EN"]
    for i, (name, start, end) in enumerate(events):
        lines += [
            "BEGIN:VEVENT",
            f"UID:demo-{i}@dinemaster",
            f"DTSTAMP:{start:%Y%m%d}T000000Z",
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{end + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{name}",
            "END:VEVENT",
        ]
    return "\r\n".join(lines + ["END:VCALENDAR", ""])


def main() -> None:
    rng = random.Random(SEED)
    now = datetime.now()
    today = now.date()
    start, end = today - timedelta(days=41), today + timedelta(days=78)
    prev_start, prev_end = start - timedelta(days=230), start - timedelta(days=110)

    for sub in ("raw-data", "calendar", "data"):
        shutil.rmtree(HERE / sub, ignore_errors=True)
    (HERE / "raw-data").mkdir()
    (HERE / "calendar").mkdir()

    load_time = datetime.combine(start - timedelta(days=1), time(9, 4))
    meals: list[tuple[datetime, str, float]] = [(load_time, "Deposit", 160)]
    dollars: list[tuple[datetime, str, float]] = [(load_time, "Deposit", 300.0)]
    promo: list[tuple[datetime, str, float]] = [(datetime.combine(start + timedelta(days=14), time(15, 25)), "Deposit", 60.0)]

    # Last semester's tail: dining dollars only, spent a bit faster, ending near zero
    day = prev_end - timedelta(days=50)
    dollars.append((datetime.combine(day - timedelta(days=1), time(9, 0)), "Deposit", 190.0))
    spent = 0.0
    while day <= prev_end and spent < 180:
        if rng.random() < 0.55:
            amount = round(rng.uniform(3, 12), 2)
            spent += amount
            dollars.append((at(day, 19, 23.5, rng), rng.choice(SNACK_SPOTS + ["Hilltop Grill"]), -amount))
        day += timedelta(days=1)
    dollars.append((datetime.combine(prev_end + timedelta(days=1), time(13, 40)), "revoke unused balance", -round(190 - spent, 2)))

    # This semester
    slots = [(11.2, 13.6), (17.4, 20.0), (20.6, 22.4)]
    for offset in range((today - start).days + 1):
        day = start + timedelta(days=offset)
        for lo, hi in slots[: meals_for(day, rng)]:
            ts, spot = at(day, lo, hi, rng), rng.choice(MEAL_SPOTS)
            meals.append((ts, spot, -1))
            if rng.random() < 0.03:  # a mis-swipe, refunded a few minutes later
                meals.append((ts + timedelta(minutes=5), spot, 1))
        weekend = day.weekday() >= 4
        if rng.random() < (0.42 if weekend else 0.24):
            dollars.append((at(day, 20.5, 23.8, rng), rng.choice(SNACK_SPOTS), -round(rng.uniform(2.5, 9.5), 2)))
        if rng.random() < 0.07:
            dollars.append((at(day, 12, 19, rng), rng.choice(MEAL_SPOTS), -round(rng.uniform(9, 14), 2)))

    export_from = prev_end - timedelta(days=52)
    for account, rows in (("Block 160 Meals", meals), ("Dining Dollars", dollars), ("Promotional Dining Dollars", promo)):
        write_statement(account, [r for r in rows if r[0] <= now], export_from, today)

    (HERE / "calendar" / "academic.ics").write_text(
        ics(
            [
                ("Fall break", today + timedelta(days=16), today + timedelta(days=19)),
                ("Reading day", end - timedelta(days=10), end - timedelta(days=10)),
                ("Final exams", end - timedelta(days=8), end),
            ]
        )
    )
    (HERE / "config.toml").write_text(
        CONFIG.format(
            start=start,
            end=end,
            cutoff=start - timedelta(days=14),
            away_start=today + timedelta(days=52),
            away_end=today + timedelta(days=56),
            prev_start=prev_start,
            prev_end=prev_end,
        )
    )
    print(f"Demo semester {start} to {end} written to {HERE}")
    print("Run it with:  DINEMASTER_CONFIG=demo/config.toml uv run streamlit run app.py")


if __name__ == "__main__":
    main()
