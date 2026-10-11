"""The `forecast` section: the verdict, the numbers, a chart for each pot, and the day-of-week view."""

from __future__ import annotations

import re
from datetime import date

from dinemaster.web.charts import forecast as charts
from dinemaster.web.charts import kit
from dinemaster.web.context import load_context
from dinemaster.web.pages import forecast as page

from conftest import AS_OF


def plain(html: str) -> str:
    """The page's words, without markup or chart internals."""
    text = re.sub(r"<svg.*?</svg>", " ", html, flags=re.S)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace(" ", " ")


def test_headline_then_numbers_then_pots_then_week(client):
    html = client.get("/forecast").text
    text = plain(html)
    assert "The forecast leaves about 65 meal exchanges unused on Dec 18." in text
    assert "Dining dollars should last too, with about $63 left on Dec 18." in text
    marks = ('class="verdict', 'class="band', 'id="meal-exchanges"', 'id="dining-dollars"', 'id="week"', 'aria-label="A typical week, day by day"')
    order = [html.index(mark) for mark in marks]
    assert order == sorted(order)
    assert html.count("<h1>") == 1 and html.count('<figure class="chart"') == 4 and html.count("<table") == 1
    assert "is not built yet" not in html and "—" not in text


def test_number_band(client):
    html = client.get("/forecast").text
    figures = re.findall(r'<span class="figure">([^<]*)</span>\s*<span class="label">([^<]*)</span>', html)
    assert [(v.replace("&gt;", ">"), label) for v, label in figures] == [
        (">99%", "chance exchanges last"),
        ("65", "exchanges unused on Dec 18"),
        ("97%", "chance dining dollars last"),
        ("$63", "dining dollars left on Dec 18"),
        ("Thu", "your heaviest day"),
    ]
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html) == ["good", "look", "good", "dollars", "days"]
    text = plain(html)
    assert "Likely 60 to 71. <1% chance you use them all." in text and "Running out early is unlikely." in text


def test_notes(client):
    html = client.get("/forecast").text
    text = plain(html)
    assert "The data ends Sep 13. Every day after that is forecast." in text
    assert "Running out is not the risk. The exchanges left on Dec 18 expire unused." in text
    assert "About $63 in dining dollars is likely to be left on Dec 18. It will be lost." in text
    assert re.findall(r'<li class="(\w+)" data-sign', html) == ["days", "look", "dollars"]
    assert '<a href="/plan">See how to use them</a>' in html


def test_each_pot_has_its_chart_and_three_figures(client):
    html = client.get("/forecast").text
    me = html[html.index('id="meal-exchanges"') : html.index('id="dining-dollars"')]
    dd = html[html.index('id="dining-dollars"') : html.index('id="week"')]
    text = plain(me)
    assert "The middle 80% of simulated semesters end between 60 and 71." in text
    assert "Chance meal exchanges last >99%" in text and "Likely left on Dec 18 60 to 71 Middle estimate 65." in text
    assert "Running out before Dec 18 Unlikely Under 1 in 20 simulations." in text
    assert me.count("<svg") == 3 and "data ends Sep 13" in me and "ends Dec 18" in me and "Thanksgiving recess" in me
    assert "what the data shows" in me and "expected" in me and "likely range, the middle 80%" in me
    assert 'data-tip="Sun, Sep 13\n141 left"' in me and re.search(r'data-tip="Mon, Sep 14\n\d+ expected\nlikely \d+ to \d+"', me)
    assert re.search(r'data-tip="Thu, Nov 26\n\d+ expected\nlikely \d+ to \d+\naway"', me)
    assert ">141</text>" in me and f'stroke="{kit.MEALS}"' in me and "stroke-dasharray" in me
    text = plain(dd)
    assert "Chance dining dollars last 97%" in text and "Likely left on Dec 18 $20 to $100" in text and "Middle estimate $63." in text and f'stroke="{kit.DOLLARS}"' in dd
    assert "Solid line: what the data shows, through Sep 13. Dashed line and band: the forecast for Sep 14 – Dec 18" in text
    assert "The band is the middle 80% of 200 simulated semesters." in text


def test_dining_dollars_can_be_shown_as_meals(client):
    dollars = client.get("/forecast").text
    assert '<a href="/forecast#dining-dollars" aria-current="true">Dollars</a>' in dollars
    assert '<a href="/forecast?unit=meals#dining-dollars">Meals</a>' in dollars and "$293 left" in dollars
    meals = client.get("/forecast?unit=meals&away=0").text
    assert '<a href="/forecast?away=0&amp;unit=meals#dining-dollars" aria-current="true">Meals</a>' in meals
    assert "Shown as meals at $15.00 each." in meals and "19.5 left" in meals and "$293 left" not in meals
    assert re.search(r"Middle estimate \d+\.\d meals\.", plain(meals))


def test_view_params_are_kept_in_links(client):
    html = client.get("/forecast?as_of=2026-09-07&unit=meals").text.replace("&amp;", "&")
    assert "/plan?as_of=2026-09-07" in html and "/forecast?as_of=2026-09-07#dining-dollars" in html
    assert "/forecast?as_of=2026-09-07&unit=meals#dining-dollars" in html


def test_week_view(client):
    html = client.get("/forecast").text
    text = plain(html)
    assert re.search(r"Thursday is your heaviest, at about \d\.\d\d meals a day\. .* the lightest, at \d\.\d\d\.", text)
    assert "The forecast and the daily plan both follow this pattern." in text
    assert "Thursday 3 2.00 2.00 0.00 $0.00 $0.00 1.58 $1.12 23%" in text and "Saturday 4 1.00 0.00 1.00 $12.50 $0.00" in text
    assert "Friday 4 1.00 1.00 0.00 $4.25 $4.25" in text
    for column in ("Days observed", "Forecast meals a day", "Forecast dining dollars a day", "Share of week"):
        assert column in text
    assert "Observed: Aug 21 – Sep 13 (24 days). Away days are left out." in text and "a day 21 days old counts half" in text
    assert 'data-tip="Thursday, 3 observed days\n2 exchange swipes a day\n0 dining-dollar meals a day\nThe forecast uses 1.58 meals a day' in html
    assert 'data-tip="Saturday, 4 observed days\n$12.50 a day\n$0.00 of it on snacks\nThe forecast uses' in html
    assert "the rate the forecast uses" in html and "dining-dollar meals" in html and "snacks" in html


def test_shares_add_up(site):
    profile = load_context(site.config_path, today=AS_OF).forecast.profile
    table = page._week_table(profile)
    assert len(table["rows"]) == 7 and len(table["columns"]) == 10
    assert abs(sum(int(row[-1].rstrip("%")) for row in table["rows"]) - 100) <= 3


def test_away_and_as_of_change_the_figures(client):
    base, away_off, earlier = (plain(client.get(url).text) for url in ("/forecast", "/forecast?away=0", "/forecast?as_of=2026-08-30"))
    assert "about 65 meal exchanges unused" in base and "about 65 meal exchanges unused" not in away_off
    assert "Away days are left out." not in away_off
    assert "through Aug 30" in earlier and "Observed: Aug 21–30 (10 days)." in earlier


def test_stale_data_is_called_out(site):
    html = site.client(today=date(2026, 10, 1)).get("/forecast").text
    assert "Your data is 18 days old. It ends Sep 13. Every day after that is forecast, not known." in plain(html)
    assert '<li class="problem" data-sign="✕"' in html and "data ends Sep 13" in html


def test_rollover_changes_the_dining_dollar_note(site):
    site.edit_config("dd_rollover = false", "dd_rollover = true")
    assert "It carries into spring." in plain(site.client().get("/forecast").text)


def test_nothing_to_forecast(site):
    # the data does not reach into the semester: no observed days at all
    site.edit_config("start = 2026-08-21", "start = 2026-09-20")
    html = site.client().get("/forecast").text
    text = plain(html)
    assert "No forecast yet." in text and "The data does not reach into the semester." in text
    assert 'class="band' not in html and "<table" not in html
    assert "Meal exchanges: no forecast." in text and "Dining dollars: no forecast." in text
    assert html.count("No observed days yet.") >= 3  # both week charts say so


def test_semester_over(site):
    site.edit_config("end = 2026-12-18", "end = 2026-09-13")
    site.edit_config("start = 2026-11-25\nend = 2026-11-29", "start = 2026-09-05\nend = 2026-09-06")
    html = site.client().get("/forecast").text
    text = plain(html)
    assert "The semester is over. Nothing is left to forecast." in text
    assert "141 meal exchanges and $293.00 in dining dollars were left." in text
    assert "chance exchanges last" not in text and "Solid line:" not in text and "stroke-dasharray=\"7 5\"" not in html
    assert "Thursday" in text  # the week view is still there
    assert site.client(today=date(2027, 1, 5)).get("/forecast").status_code == 200


def test_before_the_semester_does_not_crash(site):
    assert site.client(today=date(2026, 8, 1)).get("/forecast").status_code == 200


def test_no_data_page(empty_client):
    html = empty_client.get("/forecast").text
    assert "No dining data yet." in html and "<svg" not in html and 'class="band' not in html


def test_pot_chart_without_a_forecast_says_so(site):
    ctx = load_context(site.config_path, today=AS_OF)
    from dinemaster import forecast as fcast

    none = fcast._unavailable("ME", 141.0, (10, 90), "no observed days yet")
    svg = charts.pot_chart(ctx.cfg, none, ctx.daily["me_bal"], kit.MEALS, "Meal exchanges")
    assert "No forecast yet." in svg and "expected" not in svg and "141 left" in svg
    assert "No observed days yet." in charts.week_meals_chart(ctx.forecast.profile.iloc[0:0])


def test_a_pot_at_risk_gets_run_out_dates(site):
    # a plan far too small for the usage: the exchanges run out
    site.edit_config("starting_me = 160", "starting_me = 40")
    for name in site.root.glob("raw-data/Block*"):
        rows = name.read_text().splitlines()
        fixed = [rows[0]]
        for row in rows[1:]:
            when, what, amount, bal = row.rsplit(",", 3)
            fixed.append(f"{when},{what},{40 if what == 'Deposit' else amount},{int(bal) - 120}")
        name.write_text("\n".join(fixed) + "\n")
    html = site.client().get("/forecast").text
    text = plain(html)
    assert re.search(r"Meal exchanges are likely to run out around \w{3} \d+\.", text)
    assert "If they run out, likely" in text and "Most often around" in text
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html)[:2] == ["problem", "meals"]
    assert "exchanges left on Dec 18" in text and "expire unused" not in text
