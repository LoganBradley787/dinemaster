"""The `habits` section: the busiest slot, streaks, late night, places and the weekly recap."""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from dinemaster import habits
from dinemaster.web.charts import habits as charts
from dinemaster.web.charts import kit
from dinemaster.web.context import load_context
from dinemaster.web.pages import habits as page

from conftest import AS_OF, CONFIG, write_csv


def plain(html: str) -> str:
    """The page's words, without markup or chart internals."""
    text = re.sub(r"<(svg|script).*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).replace("&lt;", "<").replace("&#39;", "'")


def figures(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<span class="figure">([^<]*)</span>\s*<span class="label">([^<]*)</span>', html)


def test_headline_then_numbers_then_charts_then_recap(client):
    html = client.get("/habits").text
    text = plain(html)
    assert "You eat most often on Friday around 12 PM." in text
    assert "North Hall is where you go most, with 16 visits so far." in text
    order = [html.index(mark) for mark in ('class="verdict', 'class="band', 'id="when"', 'id="late-night"', 'id="places"', 'id="recap"')]
    assert order == sorted(order)
    assert html.count("<h1>") == 1 and html.count('<figure class="chart"') == 3 and html.count("<table") == 1
    assert "is not built yet" not in html


def test_streaks_are_the_number_band(client):
    html = client.get("/habits").text
    assert figures(html)[:4] == [
        ("0", "days in your current meal streak"),
        ("6", "days in your longest meal streak"),
        ("1", "day in your longest gap without a meal"),
        ("5", "days in your longest run at one place"),
    ]
    text = plain(html)
    assert "No meal on Sep 13." in text and "Sep 7 to Sep 12." in text and "North Hall, Sep 7 to Sep 11." in text
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html) == ["days", "days", "days", "days"]
    assert re.findall(r'<p class="fig" data-kind="(\w+)"', html) == ["days", "dollars", "dollars"]  # late night, one size down


def test_heatmap_and_busiest_slot(client):
    html = client.get("/habits").text
    section = html[html.index('id="when"') : html.index('id="late-night"')]
    assert "Busiest slot: Friday around 12 PM (4 meals so far)." in section
    assert "Thursday has the most meals (6) and 12 PM is the busiest hour (16)." in section
    assert 'data-tip="Thursday around 6 PM\n3 meals"' in section and 'data-tip="Saturday around 1 PM\n4 meals"' in section
    assert f'fill="{kit.MEALS}"' in section and ">12p</text>" in section and ">Mon</text>" in section


def test_late_night(client):
    html = client.get("/habits").text
    text = plain(html)
    assert "Late night is 9 PM to 4 AM: 0 exchange swipes and 4 dining-dollar purchases, most often at Gastons (4)." in text
    small = re.findall(r'<span class="v">([^<]*)</span>\s*<span class="l">([^<]*)</span>', html)
    assert small == [("4", "late-night visits"), ("$17.00", "dining dollars spent late"), ("25%", "of all dining-dollar spending")]
    assert "On 4 nights." in text


def test_places_chart_and_table(client):
    html = client.get("/habits").text
    text = plain(html)
    assert "North Hall\n16 exchange swipes\nUsually around 12:05 PM, most often on Friday" in html
    assert "Gastons\n4 dining-dollar purchases, $17.00\n0 meals, 4 snacks\nUsually around 11:10 PM, most often on Friday" in html
    assert "exchange swipes" in html and "dining-dollar purchases" in html and "stripe" in html
    assert "North Hall 16 16 0 0 $0.00 12:05 PM Friday" in text
    assert "Crossroads Grill 4 0 4 0 $50.00 1 PM Saturday" in text
    assert "Gastons 4 0 0 4 $17.00 11:10 PM Friday" in text


def test_recap_shows_the_latest_week_by_default(client):
    html = client.get("/habits").text
    text = plain(html)
    assert "7 meals this week, same as last week." in text and "5 at North Hall." in text and "$4.25 on 1 snack." in text
    assert "Busiest day was Thursday with 2 meals; 1 day had none." in text and "meals ahead of an even pace." in text
    assert "Sep 7 to Sep 13 (7 of 7 days in your data)." in text
    assert '<option value="2026-09-07" selected>Week of Sep 7 (latest)</option>' in html
    assert html.count("<option") == 4 and '<option value="2026-08-17">Week of Aug 17</option>' in html
    assert 'data-tip="Thursday, Sep 10\n2 exchange swipes"' in html and 'data-tip="Saturday, Sep 12\n0 exchange swipes\n1 dining-dollar meal"' in html


def test_week_picker_changes_the_recap(client):
    html = client.get("/habits?week=2026-08-31").text
    text = plain(html)
    assert "7 meals the week of Aug 31, same as the week before." in text and "as of Sep 6." in text
    assert '<option value="2026-08-31" selected>Week of Aug 31</option>' in html and "this week" not in text
    assert 'data-tip="Thursday, Sep 3\n2 exchange swipes"' in html
    first = plain(client.get("/habits?week=2026-08-17").text)
    assert "2 meals the week of Aug 17." in first and "Aug 21 to Aug 23 (3 of 7 days in your data)." in first
    assert 'data-tip="Monday, Aug 17\nNot in your data"' in client.get("/habits?week=2026-08-17").text
    # any day of a week picks that week; a bad or unknown week falls back to the latest
    assert "the week of Aug 31" in plain(client.get("/habits?week=2026-09-02").text)
    for bad in ("nope", "2027-01-04"):
        assert "7 meals this week" in plain(client.get(f"/habits?week={bad}").text)


def test_view_params_are_kept(client):
    html = client.get("/habits?as_of=2026-09-07&away=0&week=2026-08-31").text.replace("&amp;", "&")
    assert "/now?as_of=2026-09-07&away=0" in html
    assert '<input type="hidden" name="as_of" value="2026-09-07">' in html and '<input type="hidden" name="away" value="0">' in html
    assert 'action="/habits#recap"' in html
    assert 'href="/habits?as_of=2026-09-07&away=0&week=2026-08-24#recap"' in html  # Earlier
    assert 'href="/habits?as_of=2026-09-07&away=0#recap"' in html  # Later is the latest week: no week value
    latest = client.get("/habits").text
    assert '<a class="btn" href="/habits?week=2026-08-31#recap">Earlier</a>' in latest
    assert '<span class="btn off" aria-disabled="true">Later</span>' in latest


def test_a_past_day_changes_the_figures(client):
    html = client.get("/habits?as_of=2026-08-30").text
    text = plain(html)
    assert html.count("<option") == 2 and "Week of Sep 7" not in text
    assert "North Hall is where you go most, with 6 visits so far." in text


def test_stale_data_is_called_out(site):
    html = site.client(today=date(2026, 10, 1)).get("/habits").text
    assert "Your data is 18 days old. It ends Sep 13. These habits stop on that day." in plain(html)
    assert '<li class="problem"' in html
    fresh = site.client().get("/habits").text
    assert "These habits run through Sep 13, the last day in your data." in plain(fresh) and '<li class="days"' in fresh


def test_no_data_page(empty_client):
    html = empty_client.get("/habits").text
    assert "No dining data yet." in html and "<svg" not in html and 'class="band' not in html


def test_semester_over_and_before_start(site):
    assert site.client(today=date(2027, 1, 5)).get("/habits").status_code == 200
    html = site.client(today=date(2026, 8, 1)).get("/habits").text
    text = plain(html)
    assert "No streaks to show: no observed days yet." in text and "No observed weeks yet." in text
    assert "<select" not in html


def test_nothing_late_and_no_meals(tmp_path):
    """Deposits and one afternoon snack only: no meals, no streaks, nothing late."""
    root = tmp_path / "quiet"
    write_csv(root / "raw-data", "Block 160 Meals_statement_2026-08-01_to_2026-09-13.csv", [("2026-08-20 09:00:00", "Deposit", 160, 160)])
    write_csv(root / "raw-data", "Dining Dollars_statement_2026-08-01_to_2026-09-13.csv",
              [("2026-08-20 09:00:00", "Deposit", 360.0, 360.0), ("2026-09-01 15:00:00", "Vending", -2.0, 358.0)])  # fmt: skip
    (root / "config.toml").write_text(CONFIG)
    from conftest import Site

    site = Site(root)
    html = site.client().get("/habits").text
    text = plain(html)
    assert "No meals yet." in text and "No streaks to show: no meals recorded yet." in text
    assert "Nothing bought late at night (9 PM to 4 AM) so far." in text
    assert "Vending 1 0 0 1 $2.00 3 PM Tuesday" in text and "No meals the week of" not in text
    assert "No meals this week" in text or "No meals so far this week" in text


def test_shown_hours_trim_empty_ends_in_dining_day_order(site):
    ctx = load_context(site.config_path, today=AS_OF)
    matrix = habits.hour_weekday_matrix(ctx.df, ctx.cfg)
    hours = charts.shown_hours(matrix, 4)
    assert hours == list(range(10, 20)) and len(hours) == charts.MIN_HOURS
    late = matrix.copy()
    late.loc["Sat", 1] = 2  # a meal at 1 AM belongs to the end of the day
    assert charts.shown_hours(late, 4)[-3:] == [0, 1, 2] and charts.shown_hours(late, 4)[0] == 11
    assert charts.shown_hours(matrix * 0, 4) == [(4 + i) % 24 for i in range(24)]
    assert "late night" in charts.heat_chart(late, 4, habits.late_hours(21, 4))


def test_charts_with_nothing_in_them_say_so(site):
    ctx = load_context(site.config_path, today=AS_OF)
    assert "No meals yet." in charts.heat_chart(habits.hour_weekday_matrix(ctx.df.iloc[0:0], ctx.cfg))
    assert "No visits yet." in charts.places_chart(pd.DataFrame(columns=habits.PLACE_STATS_COLUMNS))
    assert "No days in your data for this week." in charts.week_chart(ctx.daily, date(2027, 3, 1))
    assert page.picked_week([date(2026, 9, 7)], None) == date(2026, 9, 7)
