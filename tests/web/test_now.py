"""The `now` section: the verdict, the notes, the number band, the charts and the tables."""

from __future__ import annotations

import re
from datetime import date

from dinemaster.web.charts import kit
from dinemaster.web.charts import now as charts
from dinemaster.web.context import load_context

from conftest import AS_OF


def plain(html: str) -> str:
    """The page's words, without markup or chart internals."""
    text = re.sub(r"<svg.*?</svg>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).replace("&lt;", "<").replace("&#39;", "'")


def test_headline_then_numbers_then_charts_then_tables(client):
    html = client.get("/now").text
    text = plain(html)
    assert "You are on pace to leave 65 meal exchanges unused on Dec 18." in text
    assert "141 exchanges and $293.00 in dining dollars are left, with 90 usable days to go." in text
    order = [html.index(mark) for mark in ('class="verdict', 'class="band', 'id="meal-exchanges"', 'id="day-by-day"', 'id="places"', 'aria-label="Visits and spending by place"')]
    assert order == sorted(order)
    assert html.count("<h1>") == 1 and html.count('<figure class="chart"') == 7 and html.count("<table") == 2


def test_number_band_carries_the_dashboard_figures(client):
    html = client.get("/now").text
    figures = re.findall(r'<span class="figure">([^<]*)</span>\s*<span class="label">([^<]*)</span>', html)
    assert figures == [
        ("141", "exchanges left"),
        ("$293", "dining dollars left"),
        ("21%", "of the semester done"),
        ("+14.4", "against an even pace"),
        ("65", "unused on Dec 18"),
    ]
    text = plain(html)
    assert "About 19 meals." in text and "Likely 60 to 71. <1% chance you use them all." in text
    assert f"--c:{kit.ME}" in html and f"--c:{kit.DD}" in html and "--n:5" in html


def test_notes_say_what_to_know(client):
    text = plain(client.get("/now").text)
    assert "These figures are for Sep 13, the last day in your data." in text
    assert "About $63 in dining dollars is likely to be left on Dec 18. It will be lost." in text
    assert "do not add up" not in text


def test_stale_data_is_called_out(site):
    html = site.client(today=date(2026, 10, 1)).get("/now").text
    text = plain(html)
    assert "Your data is 18 days old. It ends Sep 13. This page describes that day, not today." in text
    assert 'class="fresh old"' in html and '<li class="bad">' in html


def test_balance_charts_show_the_semester_with_today_and_days_away(client):
    html = client.get("/now").text
    section = html[html.index('id="meal-exchanges"') : html.index('id="dining-dollars"')]
    assert "You use 0.79 exchanges a day. At that pace you reach Dec 18 with 69 left." in section
    assert section.count("<svg") == 3 and "Sep 13" in section and "ends Dec 18" in section and "Thanksgiving recess" in section
    assert "what is left" in section and "your pace so far, carried on" in section and "an even pace" in section
    assert 'data-tip="Sun, Sep 13\n141 left\n' in section and "left at your pace so far" in section
    assert ">141</text>" in section  # the line's end is labelled with where it stands
    assert f'stroke="{kit.ME}"' in section and "stroke-dasharray" in section


def test_dining_dollars_can_be_shown_as_meals(client):
    dollars = client.get("/now").text
    assert '<a href="/now#dining-dollars" aria-current="true">Dollars</a>' in dollars
    assert '<a href="/now?unit=meals#dining-dollars">Meals</a>' in dollars and "$293 left" in dollars
    meals = client.get("/now?unit=meals&away=0").text
    assert '<a href="/now?away=0&amp;unit=meals#dining-dollars" aria-current="true">Meals</a>' in meals
    assert "Shown as meals at $15.00 each." in meals and "19.5 left" in meals and "$293 left" not in meals


def test_pooled_chart_only_when_dining_dollars_do_not_carry_over(site):
    assert 'id="both"' in site.client().get("/now").text
    site.edit_config("dd_rollover = false", "dd_rollover = true")
    html = site.client().get("/now").text
    text = plain(html)
    assert 'id="both"' not in html and html.count('<figure class="chart"') == 6
    assert "They carry into spring." in text and "It carries into spring." in text
    assert "you can eat 1.55 meals a day." in text and "counting dining dollars" not in text


def test_usage_charts_and_tables(client):
    html = client.get("/now").text
    text = plain(html)
    assert "To use every exchange by Dec 18 you can eat 1.55 meals a day, or 1.76 counting dining dollars." in text
    assert "From here, a week that uses everything up has about 5.3 two-meal days and 1.7 one-meal days." in text
    assert 'data-tip="Thu, Sep 10\n2 exchange swipes\n7-day average' in html
    assert 'data-tip="Sat, Sep 12\n0 exchange swipes\n1 dining-dollar meal\n' in html
    assert 'data-tip="Week of Sep 7\n1 day: no meal\n5 days: one meal\n1 day: two meals"' in html
    assert "North Hall\n16 swipes" in html and "Crossroads Grill\n$50.00 on 4 meals" in html and "Gastons\n$17.00 on 4 snacks" in html
    assert "Of your dining dollars, $50.00 went on 4 meals and $17.00 on 4 snacks." in text
    assert "Week of Sep 7 7 6 1 $16.75 1 7" in text  # meals, swipes, dining-dollar meals, spent, empty days, days
    assert "North Hall 16 0 0" in text and "Crossroads Grill 0 4 0 $50.00" in text and "Gastons 0 0 4 $17.00" in text


def test_a_past_day_changes_the_figures(client):
    text = plain(client.get("/now?as_of=2026-08-30").text)
    assert "153 exchanges" in text and "8% of the semester done" in text and "Week of Sep 7" not in text


def test_no_data_page(empty_client):
    html = empty_client.get("/now").text
    assert "No dining data yet." in html and "<svg" not in html and 'class="band' not in html
    assert '<a class="btn" href="/data">Go to data</a>' in html


def test_semester_over_and_before_start_do_not_crash(site):
    for today in (date(2027, 1, 5), date(2026, 8, 1)):
        assert site.client(today=today).get("/now").status_code == 200


def test_weekly_buckets_count_days_by_meals(site):
    daily = load_context(site.config_path, today=AS_OF).daily
    weeks = dict(charts.weekly_buckets(daily))
    assert weeks[date(2026, 9, 7)] == {"0": 1, "1": 5, "2": 1, "3+": 0}
    assert weeks[date(2026, 8, 17)] == {"0": 1, "1": 2, "2": 0, "3+": 0}  # a part week: Fri to Sun


def test_charts_with_nothing_in_them_say_so(site):
    ctx = load_context(site.config_path, today=AS_OF)
    empty = ctx.daily.iloc[0:0]
    assert "No meals yet." in charts.daily_meals_chart(ctx.cfg, empty, None, None)
    assert "No weeks yet." in charts.weekly_mix_chart(empty)
    assert "No swipes yet." in charts.me_places_chart(ctx.daily["meals"].iloc[0:0])
    assert "No dining dollars spent yet." in charts.dd_places_chart({})
