"""The `compare` section: this semester next to past ones, and plain words where the data is thin."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
import pytest

from dinemaster import compare as cmp
from dinemaster.web.charts import compare as charts
from dinemaster.web.charts import kit
from dinemaster.web.context import load_context

from conftest import AS_OF, write_csv

HISTORY = """
[[history.semesters]]
name = "Fall 2025"
start = 2025-08-21
end = 2025-12-18

[[history.semesters]]
name = "Spring 2026"
start = 2026-01-12
end = 2026-05-09
"""


def plain(html: str) -> str:
    """The page's words, without markup or chart internals."""
    text = re.sub(r"<svg.*?</svg>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).replace("&lt;", "<").replace("&#39;", "'").replace("\xa0", " ")


def add_history(site, spring: bool = True) -> None:
    """Two past semesters in the config. Fall 2025 has no data at all; Spring 2026 has dining
    dollars only, one $10 meal every Wednesday from Mar 4 to Apr 29 (so the export is partial)."""
    site.config_path.write_text(site.config_path.read_text() + HISTORY)
    if not spring:
        return
    rows, bal, day = [], 200.0, date(2026, 3, 4)
    while day <= date(2026, 4, 29):
        bal -= 10.0
        rows.append((f"{day} 12:30:00", "Crossroads Grill", -10.0, bal))
        day += timedelta(days=7)
    write_csv(site.root / "raw-data", "Dining Dollars_statement_2026-01-01_to_2026-05-30.csv", rows)


@pytest.fixture
def past(site):
    add_history(site)
    return site


def test_headline_then_numbers_then_charts_then_tables(past):
    html = past.client().get("/compare").text
    text = plain(html)
    assert "You are spending $19.54 a week in dining dollars, $8.49 more than in Spring 2026." in text
    assert "Meal exchanges cannot be compared. No past semester has meal exchange records." in text
    order = [html.index(mark) for mark in ('class="verdict', 'class="notes', 'class="band', 'id="rates"', 'id="exchanges-by-week"',
                                           'id="dollars-by-week"', 'id="places"', 'aria-label="Semester summaries"')]  # fmt: skip
    assert order == sorted(order)
    assert html.count("<h1>") == 1 and html.count('<figure class="chart"') == 7 and html.count("<table") == 2


def test_band_figures_and_their_meanings(past):
    html = past.client().get("/compare").text
    figures = re.findall(r'<span class="figure">([^<]*)</span>\s*<span class="label">([^<]*)</span>', html)
    assert figures == [("5.5", "exchanges a week"), ("$19.54", "dining dollars a week"), ("24", "days in the data")]
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html) == ["meals", "dollars", "days"]
    text = plain(html)
    assert "No past semester has meal exchange records." in text and "Spring 2026: $11.05." in text
    assert "Of 120 this semester. Spring 2026: 57 of 118." in text


def test_coverage_is_said_plainly(past):
    html = past.client().get("/compare").text
    text = plain(html)
    assert "Fall 2026: In progress. The data runs through Sep 13, 24 of 120 days." in text
    assert "Fall 2025: No transactions in the ledger for these dates." in text
    assert "Spring 2026: The data covers only Mar 4 to Apr 29, 57 of 118 days. Rates use those days. No meal exchange records." in text
    notes = html[html.index('class="notes') : html.index('class="band')]
    assert re.findall(r'<li class="(\w+)" data-sign="(.?)"', notes) == [("days", ""), ("unknown", "?"), ("look", "!")]
    assert "—" not in text and "–" not in text


def test_a_day_or_a_week(past):
    client = past.client()
    week = client.get("/compare").text
    assert '<a href="/compare#rates" aria-current="true">A week</a>' in week and '<a href="/compare?per=day#rates">A day</a>' in week
    assert "Meal exchanges a week" in week and 'data-tip="Fall 2026\n5.5 a week\n19 swipes over 24 days"' in week
    day = client.get("/compare?per=day&away=0").text
    assert '<a href="/compare?away=0&amp;per=day#rates" aria-current="true">A day</a>' in day
    text = plain(day)
    assert "You are spending $2.79 a day in dining dollars, $1.21 more than in Spring 2026." in text
    assert "Meal exchanges a day" in day and 'data-tip="Fall 2026\n0.79 a day\n19 swipes over 24 days"' in day
    assert re.findall(r'<span class="figure">([^<]*)</span>', day)[:2] == ["0.79", "$2.79"]


def test_rate_charts_mark_missing_semesters(past):
    html = past.client().get("/compare").text
    rates = html[html.index('id="rates"') : html.index('id="exchanges-by-week"')]
    me, dd = rates.split("Dining dollars a week")
    assert me.count(">no records</text>") == 6 and "a past semester" not in me  # two past semesters, three sizes
    assert dd.count(">no records</text>") == 3 and "a past semester" in dd
    assert 'data-tip="Spring 2026\n$11.05 a week\n$90.00 over 57 days\n9 meals ($90.00), 0 snacks ($0.00)"' in dd
    assert f'fill="{kit.DOLLARS}"' in dd and f'fill="{kit.tint(kit.DOLLARS, 0.5)}"' in dd and f'fill="{kit.MEALS}"' in me


def test_weekly_overlays(past):
    html = past.client().get("/compare").text
    me = html[html.index('id="exchanges-by-week"') : html.index('id="dollars-by-week"')]
    dd = html[html.index('id="dollars-by-week"') : html.index('id="places"')]
    assert 'data-tip="Week 1\nFall 2026, week of Aug 21: 6 swipes"' in me
    assert 'data-tip="Week 4\nFall 2026, week of Sep 11: 1 swipe (part week, 3 of 7 days)"' in me
    assert "Spring 2026" not in me and "A full week this semester took 6 exchanges." in plain(me)
    assert 'data-tip="Week 8\nSpring 2026, week of Mar 2: $10.00 (part week, 5 of 7 days)"' in dd
    assert 'data-tip="Week 2\nFall 2026, week of Aug 28: $16.75"' in dd
    assert "stroke-dasharray" in dd and "stroke-dasharray" not in me
    assert dd.count('fill="var(--bg)"') >= 2 * 3  # hollow points: the part weeks of both semesters, in three sizes
    assert "Hollow points are part weeks" in dd
    assert "A full week in Spring 2026 took $10.00." in plain(dd)


def test_top_places_per_semester(past):
    html = past.client().get("/compare").text
    places = html[html.index('id="places"') : html.index('id="semesters"')]
    assert [h.strip() for h in re.findall(r"<h3>([^<]+)</h3>", places)] == ["Fall 2026 (now)", "Spring 2026", "Fall 2025"]
    assert "North Hall\n16 visits\n16 exchange swipes" in places
    assert "Crossroads Grill\n9 visits\n$90.00 in dining dollars on 9 purchases" in places
    assert "No places on record." in places
    text = plain(places)
    assert "Fall 2026 North Hall 16 16" in text and "Spring 2026 Crossroads Grill 9 0 $90.00" in text


def test_summary_table_has_every_figure(past):
    html = past.client().get("/compare").text
    text = plain(html[html.index('id="semesters"') :])
    assert "Fall 2026 (now) Spring 2026 Fall 2025" in text
    assert "Data covers Aug 21 to Sep 13 Mar 4 to Apr 29 no data" in text
    assert "Days covered 24 of 120 57 of 118 0 of 120" in text
    assert "Exchange swipes 19 no records no data" in text
    assert "Exchanges a day 0.79 no records no data" in text and "Exchanges a week 5.54 no records no data" in text
    assert "Dining dollars spent $67.00 $90.00 no data" in text
    assert "Dining dollars a day $2.79 $1.58 no data" in text and "Dining dollars a week $19.54 $11.05 no data" in text
    assert "Dining-dollar meals 4 ($50.00) 9 ($90.00) no data" in text and "Dining-dollar snacks 4 ($17.00) 0 ($0.00) no data" in text
    assert "Top place North Hall (16) Crossroads Grill (9) none" in text


def test_no_history_shows_this_semester_alone(client):
    html = client.get("/compare").text
    text = plain(html)
    assert "There are no past semesters to compare with." in text and "This page shows Fall 2026 on its own." in text
    assert "No past semesters are set up. Add [[history.semesters]] entries to config.toml to compare." in text
    assert "this semester</h2>" in html and html.count('<figure class="chart"') == 5
    assert re.findall(r'<span class="figure">([^<]*)</span>', html) == ["5.5", "$19.54", "24"]


def test_past_semesters_without_data(site):
    add_history(site, spring=False)
    text = plain(site.client().get("/compare").text)
    assert "Past semesters have no data to compare with." in text
    assert "Spring 2026: No transactions in the ledger for these dates." in text


def test_a_past_day_and_away_days_change_the_figures(past):
    client = past.client()
    text = plain(client.get("/compare?as_of=2026-08-30").text)
    assert "The data runs through Aug 30, 10 of 120 days." in text and "24 of 120" not in text
    assert "/now?as_of=2026-08-30" in client.get("/compare?as_of=2026-08-30").text
    past.edit_config("start = 2026-11-25\nend = 2026-11-29", "start = 2026-09-05\nend = 2026-09-06")
    away = plain(past.client().get("/compare").text)
    assert "2 away days left out of the rates." in away and "Days the rates use 22 57 0" in away
    assert "2 away days" not in plain(past.client().get("/compare?away=0").text)


def test_stale_data_is_called_out(past):
    html = past.client(today=date(2026, 10, 1)).get("/compare").text
    assert "Your data is 18 days old. It ends Sep 13. This semester's figures stop on that day." in plain(html)
    assert '<li class="problem" data-sign="✕"' in html


def test_semester_over_and_before_start(past):
    over = plain(past.client(today=date(2027, 1, 5)).get("/compare").text)
    assert "You spent $19.54 a week in dining dollars" in over
    before = past.client(today=date(2026, 8, 1)).get("/compare")
    assert before.status_code == 200 and "The semester hasn't started yet." in plain(before.text)


def test_no_data_page(empty_client):
    html = empty_client.get("/compare").text
    assert "No dining data yet." in html and "<svg" not in html and 'class="band' not in html


def test_charts_with_nothing_in_them_say_so(past):
    ctx = load_context(past.config_path, today=AS_OF)
    summaries = cmp.semester_summaries(ctx.df, ctx.cfg)
    nothing = pd.DataFrame(columns=cmp.WEEKLY_COLUMNS)
    assert "No meal exchange records in any semester." in charts.weekly_chart(nothing, "ME", ["Fall 2026"])
    assert "No dining dollar records in any semester." in charts.rate_chart(summaries[:1], "DD", "week")
    assert "No places on record." in charts.places_chart(summaries[0])


def test_a_past_semester_keeps_its_line_style(past):
    assert charts.past_style(0, "DD") == charts.past_style(3, "DD") != charts.past_style(1, "DD")
    for i in range(3):
        tone, dash = charts.past_style(i, "ME")
        assert tone.meaning is kit.MEALS.meaning and dash and -0.12 <= tone.amount <= 0.65
