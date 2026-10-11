"""The `math` section: every formula both ways, with the column in use marked."""

from __future__ import annotations

import re
from datetime import date

from dinemaster.web.charts import math as charts

from conftest import write_csv


def plain(html: str) -> str:
    """The page's words, without markup or chart internals."""
    text = re.sub(r"<svg.*?</svg>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).replace("&lt;", "<").replace("&#39;", "'")


def links(html: str) -> list[str]:
    return [h.replace("&amp;", "&") for h in re.findall(r'href="([^"]+)"', html)]


def test_headline_numbers_chart_then_six_tables(client):
    html = client.get("/math").text
    text = plain(html)
    assert "Leaving out 5 days away, you can eat 1.55 meals a day and use every exchange by Dec 18." in text
    assert "Counting every day it is 1.47." in text and "The app uses the column marked in use: leaving out days away." in text
    marks = ['class="verdict', 'class="band', 'class="choices', 'id="meals-a-day"', 'id="days"', 'id="targets"', 'id="pace"', 'id="day-mix"', 'id="run-out"', 'id="burn"']
    order = [html.index(mark) for mark in marks]
    assert order == sorted(order)
    assert html.count("<h1>") == 1 and html.count('<figure class="chart"') == 1 and html.count("<table") == 6
    assert "—" not in html and "n/a" not in text


def test_band_and_notes(client):
    html = client.get("/math").text
    figures = re.findall(r'<span class="figure">([^<]*)</span>\s*<span class="label">([^<]*)</span>', html)
    assert figures == [
        ("+14.4", "against an even pace"),
        ("1.55", "meals a day allowed"),
        ("0.79", "exchanges a day so far"),
        ("$2.79", "dining dollars a day so far"),
        ("91", "usable days to go"),
    ]
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html) == ["look", "meals", "meals", "dollars", "days"]
    text = plain(html)
    assert "1.76 counting dining dollars." in text and "96 counting 5 days away." in text and "Leaves 69 on Dec 18." in text
    assert "These figures are for Sep 13, the last day in your data." in text
    assert '<li class="days"' in html and "carry into spring" not in text


def test_every_formula_both_ways_with_its_arithmetic(client):
    text = plain(client.get("/math").text)
    for expected in (
        # the day-counting convention
        "The day the figures are for counts as gone by. Days to go start the day after it and include the last day, Dec 18.",
        "Days away: Thanksgiving recess (Nov 25 to Nov 29).",
        "Days in the semester Aug 21 to Dec 18, both ends counted. 120 115 120 − 5 away",
        "Days to go From the day after Sep 13 through Dec 18. 96 91 96 − 5 away",
        "80.0% 96 ÷ 120 79.1% 91 ÷ 115",
        "Meal exchanges left Starting exchanges − exchanges used. 141 160 − 19",
        "rounded down to whole meals. 19 $293.00 ÷ $15.00",
        # targets, strict and pooled
        "128.0 160 × 80.0% 126.6 160 × 79.1%",
        "+13.0 more left 141 − 128.0 +14.4 more left 141 − 126.6",
        "184.0 160 + 24 184.0 160 + 24",
        "128.2 184 × 80.0% − 19 126.6 184 × 79.1% − 19",
        "+12.8 more left 141 − 128.2 +14.4 more left 141 − 126.6",
        # allowed pace
        "1.47 141 ÷ 96 1.55 141 ÷ 91",
        "1.67 (141 + 19) ÷ 96 1.76 (141 + 19) ÷ 91",
        # day mix
        "51.0 2 × 96 − 141 41.0 2 × 91 − 141",
        "3.3 45.0 × 7 ÷ 96 3.8 50.0 × 7 ÷ 91",
        "4.7 64.0 × 7 ÷ 96 5.3 69.0 × 7 ÷ 91",
        # run-out at two a day
        "Nov 22 26 days short 70 days of two Nov 22 26 days short 70 days of two",
        "Dec 2 16 days short 80 days of two Dec 7 11 days short 80 days of two",
        # burn rate and projection, each pot
        "0.79 a day 19 ÷ 24 0.79 a day 19 ÷ 24",
        "Mar 11, 2027 after Dec 18 Mar 16, 2027 after Dec 18",
        "82.1 days spare 141 ÷ 0.79 − 96 87.1 days spare 141 ÷ 0.79 − 91",
        "65.0 141 − 0.79 × 96 69.0 141 − 0.79 × 91",
        "$2.79 a day $67.00 ÷ 24",
        "$25.00 $293.00 − $2.79 × 96 $38.96 $293.00 − $2.79 × 91",
        "About $25 unspent will be lost. About $39 unspent will be lost.",
    ):
        assert expected in text, expected


def test_each_block_says_what_it_answers(client):
    text = plain(client.get("/math").text)
    for line in (
        "How many days each formula counts, and what is left to spread over them.",
        "How many exchanges an even pace would leave today, and how far from that you are.",
        "How many meals a day use everything up by Dec 18.",
        "How many of the days to go can be one-meal days and how many two-meal days, if every meal is used.",
        "The last day you could eat two meals, if you ate two every day from here on.",
        "Where each pot ends up if you keep using it at your own pace so far.",
    ):
        assert line in text


def test_the_column_in_use_is_marked_and_follows_the_away_setting(client):
    html = client.get("/math").text
    assert html.count('<th scope="col" class="n on">Leaving out days away<span class="tag">in use</span></th>') == 6
    assert '<th scope="col" class="n">Counting every day</th>' in html
    assert '<a href="/math?away=1" aria-current="true">Left out</a>' in html and '<a href="/math?away=0">Counted</a>' in html
    counted = client.get("/math?away=0").text
    assert counted.count('<th scope="col" class="n on">Counting every day<span class="tag">in use</span></th>') == 6
    assert '<a href="/math?away=0" aria-current="true">Counted</a>' in counted
    text = plain(counted)
    assert "Counting every day, you can eat 1.47 meals a day and use every exchange by Dec 18." in text
    assert "Leaving out 5 days away it is 1.55." in text and "96 days to go" in text
    figures = re.findall(r'<span class="figure">([^<]*)</span>', counted)
    assert figures == ["+13", "1.47", "0.79", "$2.79", "96"]
    # the active cell of a row is the first one now
    assert re.search(r'<td class="n on">\s*<span class="v">1.47</span>', counted) and re.search(r'<td class="n">\s*<span class="v">1.55</span>', counted)


def test_the_working_can_be_hidden_and_the_choice_is_kept(client):
    shown = client.get("/math").text
    assert 'class="calc"' in shown and 'class="how"' in shown
    assert '<a href="/math" aria-current="true">Shown</a>' in shown and '<a href="/math?working=0">Hidden</a>' in shown
    hidden = client.get("/math?working=0&as_of=2026-09-07").text
    text = plain(hidden)
    assert 'class="calc"' not in hidden and 'class="how"' not in hidden and "÷ 96" not in text
    assert "Exchanges only 1.4" in text  # the results stay
    hrefs = links(hidden)
    assert "/math?as_of=2026-09-07&working=0" in hrefs and "/math?as_of=2026-09-07" in hrefs  # Hidden (on), Shown
    assert "/math?as_of=2026-09-07&away=0&working=0" in hrefs and "/math?as_of=2026-09-07&away=1&working=0" in hrefs
    assert "/plan?as_of=2026-09-07" in hrefs
    internal = [h for h in hrefs if h.startswith("/") and not h.startswith("/static")]
    assert [h for h in internal if "as_of=2026-09-07" not in h] == ["/math"]  # only the top bar's reset drops the date


def test_a_past_day_changes_the_figures(client):
    text = plain(client.get("/math?as_of=2026-08-30").text)
    assert "Through Aug 30. That day counts as gone by." in text and "160 − 7" in text and "1.55 141 ÷ 91" not in text


def test_the_pace_chart_has_tooltips_and_a_legend(client):
    html = client.get("/math").text
    section = html[html.index('id="meals-a-day"') : html.index('id="days"')]
    assert "You use 0.79 exchanges a day. The plan allows 1.55." in section
    assert 'data-tip="Your exchange pace so far\n0.79 meals a day\n' in section
    assert "Exchanges left, divided by days to go, leaving out days away." in section
    assert "Two meals a day\n2 meals a day" in section
    assert "your pace" in section and "what the plan allows" in section and "a fixed pace, for reference" in section
    assert "No pace to show. The semester is over." in charts.pace_chart(None, None, None, "counting every day")


def test_group_chips_name_what_the_rows_measure(client):
    html = client.get("/math").text
    assert html.count('style="background:var(--days)"') == 4  # the calendar, two day mixes, the run-out
    assert html.count('style="background:var(--meals)"') == 4  # two targets, the pace, the exchange burn
    assert html.count('style="background:var(--dollars)"') == 1
    css = client.get("/static/sections/math.css").text
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|(?<![-\w])(white|black)(?![-\w])", css)


def test_stale_data_is_called_out(site):
    html = site.client(today=date(2026, 10, 1)).get("/math").text
    assert "Your data is 18 days old. It ends Sep 13. This page describes that day, not today." in plain(html)
    assert '<li class="problem"' in html


def test_semester_over_says_so_instead_of_dividing_by_zero(site):
    # exports that reach the last day, so the figures are worked out there and no days are left
    write_csv(site.root / "raw-data", "Block 160 Meals_statement_2026-09-14_to_2026-12-18.csv", [("2026-12-18 12:00:00", "North Hall", -1, 140)])
    write_csv(site.root / "raw-data", "Dining Dollars_statement_2026-09-14_to_2026-12-18.csv", [("2026-12-18 13:00:00", "Crossroads Grill", -12.5, 280.5)])
    r = site.client(today=date(2027, 1, 5)).get("/math")
    text = plain(r.text)
    assert r.status_code == 200
    assert "The semester is over. There is nothing left to pace." in text
    assert "Exchanges left ÷ days to go. The semester is over. The semester is over." in text
    assert "One-meal days a week One-meal days × 7 ÷ days to go. The semester is over." in text
    assert "Fewer than 2 are left" not in text and "meals a day allowed" not in text
    assert "Your exchange pace so far" in r.text  # the pace so far is still known, so the chart still draws
    assert "after the semester ends" in text  # the clamped as-of notice


def test_far_past_the_last_export_the_page_still_describes_the_last_known_day(site):
    r = site.client(today=date(2027, 1, 5)).get("/math")
    assert r.status_code == 200 and "It ends Sep 13." in plain(r.text)


def test_before_the_semester_and_nothing_used(site):
    r = site.client(today=date(2026, 8, 1)).get("/math")
    text = plain(r.text)
    assert r.status_code == 200 and "Not enough data yet." in text and "Days gone by Through Aug 20. That day counts as gone by. 0 0" in text


def test_no_days_away_means_both_columns_agree(site):
    site.edit_config("enabled = true", "enabled = false")
    text = plain(site.client().get("/math").text)
    assert "You can eat 1.47 meals a day and use every exchange by Dec 18." in text
    assert "No days away are set, so both columns below say the same thing." in text
    assert "No days away are set, so the two columns are the same." in text and "1.47 141 ÷ 96 1.47 141 ÷ 96" in text


def test_dollars_that_carry_over_are_noted(site):
    site.edit_config("dd_rollover = false", "dd_rollover = true")
    html = site.client().get("/math").text
    text = plain(html)
    assert "Your dining dollars carry into spring. The rows that count them as meals are here for checking only." in text
    assert "About $39 carries into spring." in text and "counting dining dollars." not in text
    assert '<li class="dollars"' in html


def test_day_mix_says_when_one_or_two_a_day_cannot_fit(site):
    site.edit_config("starting_me = 160", "starting_me = 400")
    text = plain(site.client().get("/math").text)
    assert "More than two a day. Using it all needs some three-meal days." in text and "—" not in text


def test_no_data_page(empty_client):
    html = empty_client.get("/math").text
    assert "No dining data yet." in html and "<table" not in html and "<svg" not in html and 'class="band' not in html
    assert '<a class="btn" href="/data">Go to data</a>' in html
