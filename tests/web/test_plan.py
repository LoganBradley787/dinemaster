"""The `plan` section: the day by day plan, the rate ladder, the spend-down, the what-if and the plan sizes."""

from __future__ import annotations

import re
from datetime import date

import pytest

from dinemaster.web.charts import kit
from dinemaster.web.charts import plan as charts
from dinemaster.web.pages import plan as page

PLANS = """
[[plans]]
name = "All Access"
dining_dollars = 150.0
price = 3670.0

[[plans]]
name = "Block 160"
exchanges = 160
dining_dollars = 360.0
price = 2190.0

[[plans]]
name = "Block 100"
exchanges = 100
dining_dollars = 360.0
price = 1600.0
"""


def plain(html: str) -> str:
    """The page's words, without markup, script or chart internals."""
    text = re.sub(r"<script.*?</script>|<svg.*?</svg>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).replace("&lt;", "<").replace("&#39;", "'")


def hrefs(html: str) -> list[str]:
    return [h.replace("&amp;", "&") for h in re.findall(r'href="([^"]+)"', html)]


def part(html: str, start: str, end: str) -> str:
    return html[html.index(f'id="{start}"') : html.index(f'id="{end}"')]


@pytest.fixture
def with_plans(site):
    site.config_path.write_text(site.config_path.read_text() + PLANS)
    return site


def test_first_screen_says_what_to_do_today_and_this_week(client):
    html = client.get("/plan").text
    text = plain(html)
    assert "2 meals today, 1 tomorrow." in text
    assert (
        "Using everything up takes 160 meals (141 exchanges and 19 meals from dining dollars) over 91 days, Sep 14 to Dec 18. "
        "They are weighted toward your usual heavy days, never more than 3 in a day."
    ) in text
    figures = re.findall(r'<span class="figure">([^<]*)</span>\s*<span class="label">([^<]*)</span>', html)
    assert figures == [
        ("12", "meals in the next 7 days"),
        ("10.8", "exchanges a week uses them all"),
        ("65", "unused on Dec 18 at your usual pace"),
        ("91", "usable days left"),
    ]
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html) == ["meals", "meals", "look", "days"]
    assert "You use about 5.8 a week." in text and "Sep 14 to Dec 18." in text
    order = [html.index(mark) for mark in ('class="verdict', 'class="band', 'id="days"', 'id="weekdays"', 'id="exchanges"', 'id="spend-down"', 'id="what-if"', 'id="plan-size"')]
    assert order == sorted(order) and html.count("<h1>") == 1
    assert "—" not in html and "not built yet" not in html


def test_the_three_parts_are_easy_to_find(client):
    html = client.get("/plan").text
    links = hrefs(html)
    for anchor in ("days", "exchanges", "spend-down", "what-if", "plan-size"):
        assert f"/plan#{anchor}" in links and f'id="{anchor}"' in html
    for heading in ("the next 14 days", "by day of the week", "using the exchanges before they expire", "spend-down", "what if", "plan size"):
        assert f"<h2>{heading}</h2>" in html


def test_next_14_days_as_weeks_with_today_marked(client):
    html = client.get("/plan").text
    grid = part(html, "days", "weekdays")
    assert grid.count('role="row"') == 3 and grid.count('role="cell"') == 14  # a heading row and two weeks from Monday
    assert '<div role="cell" class="day today">' in grid and ">Today</span>" in grid
    text = plain(grid)
    assert "Today 2 meals Sep 15 1 meal Sep 16 2 meals Sep 17 3 meals" in text and "Sep 27 0 meals" in text
    assert grid.count("<i></i>") == 24  # one block a meal over the fortnight
    assert "One block is one meal. Exchanges and the meals your dining dollars can buy." in text


def test_exchanges_only_or_with_dining_dollars(site):
    client = site.client()
    both = client.get("/plan").text
    assert '<a href="/plan#days" aria-current="true">With dining dollars</a>' in both
    assert '<a href="/plan?dd=0#days">Exchanges only</a>' in both
    only = client.get("/plan?dd=0").text
    text = plain(only)
    assert '<a href="/plan?dd=0#days" aria-current="true">Exchanges only</a>' in only and '<a href="/plan#days">With dining dollars</a>' in only
    assert "Using everything up takes 141 meals (141 exchanges) over 91 days" in text and "Exchanges only." in text
    assert part(only, "days", "weekdays").count("<i></i>") < part(both, "days", "weekdays").count("<i></i>")
    # dining dollars that carry over need no using up: exchanges only is then the default
    site.edit_config("dd_rollover = false", "dd_rollover = true")
    carried = site.client().get("/plan").text
    assert '<a href="/plan#days" aria-current="true">Exchanges only</a>' in carried and '<a href="/plan?dd=1#days">With dining dollars</a>' in carried
    assert "Using everything up takes 141 meals (141 exchanges)" in plain(carried)
    assert "They carry into spring, so no dining-dollar meals are planned." in plain(carried)


def test_plan_by_weekday_chart_and_table(client):
    html = client.get("/plan").text
    section = part(html, "weekdays", "exchanges")
    text = plain(section)
    assert "Each remaining day gets a share in proportion to how much you usually eat on that weekday." in text
    assert "Monday 1.7 0.98 14" in text and "Thursday 3 1.58 13" in text and "Sunday 0 0.32 12" in text
    assert section.count("<svg") == 3 and "planned from here" in section and "your usual" in section
    assert 'data-tip="Monday\n1.7 planned a day\n0.98 usual\n14 days left"' in section
    assert f'fill="{kit.MEALS}"' in section and f'fill="{kit.DIM}"' in section


def test_rate_ladder_and_where_exchanges_go_unused(client):
    html = client.get("/plan").text
    section = part(html, "exchanges", "spend-down")
    text = plain(section)
    assert "You are on pace to leave 65 exchanges unused on Dec 18 (likely 60 to 71)." in text
    assert "You use about 5.8 a week. All 141 takes 10.8." in text
    assert "6 a week leaves 63, 8 leaves 37, 9 leaves 24, 11 uses them all." in text
    assert "One more swipe a week saves 13 exchanges." in text
    assert "Exchanges a week Unused on Dec 18 6 63 8 37 9 24 11 0" in text
    assert 'data-tip="6 exchanges a week\n63 exchanges unused on Dec 18"' in section and "uses them all</text>" in section
    assert "8 days so far had no swipe, and 8 of them were Sat to Sun days." in text
    assert "On Mon to Fri, 16 of 16 (1.19 a day). 24 Sat to Sun days are left." in text


def test_ladder_shows_what_the_unused_exchanges_are_worth(site):
    site.edit_config("starting_me = 160", "starting_me = 160\nprice = 2190.0")
    html = site.client().get("/plan").text
    text = plain(html)
    assert "about $743 of the plan" in text and "Worth about $743 of the plan." in text
    assert "Unused on Dec 18 Worth 6 63 $721" in text and "63 unused, $721</text>" in html


def test_no_ladder_when_the_exchanges_are_not_going_unused(site):
    site.edit_config("starting_me = 160", "starting_me = 60")
    html = site.client().get("/plan").text
    assert 'id="exchanges"' not in html and "/plan#exchanges" not in hrefs(html)
    assert "look" not in re.findall(r'<p class="stat" data-kind="(\w+)"', html)


def test_spend_down(client):
    html = client.get("/plan").text
    section = part(html, "spend-down", "what-if")
    text = plain(section)
    assert "Buy about 1 meal a week with dining dollars and use about 11 exchanges a week. Both reach zero on Dec 18. About $60 is set aside for snacks." in text
    figures = re.findall(r'<span class="v">([^<]*)</span>\s*<span class="l">([^<]*)</span>', section)
    assert figures == [
        ("10.8", "exchanges a week"),
        ("1.2", "dining-dollar meals a week"),
        ("1.71", "meals a day"),
        ("$60", "set aside for snacks"),
        ("$7.74", "dining dollars left unplanned"),
    ]
    assert re.findall(r'<p class="fig" data-kind="(\w+)"', section) == ["meals", "dollars", "meals", "dollars", "dollars"]
    assert "91 usable days (13.0 weeks) from Sep 14 through Dec 18. Of $293.00 in dining dollars, about $60 is expected to go to snacks" in text
    assert "That leaves $232.74 for 15 meals at $15.00." in text
    assert 'data-tip="Meals\n$225.00 for 15 meals at $15.00"' in section and 'data-tip="Left unplanned\n$7.74"' in section


def test_what_if_rate_changes_the_figures(client):
    html = client.get("/plan").text
    section = part(html, "what-if", "plan-size")
    text = plain(section)
    assert "Projects from Sep 14, the day after your data ends, through Dec 18. Meals use exchanges first, then dining dollars at $15.00 a meal." in text
    assert "2 meals a day" in text and "0 exchanges left on Dec 18 They run out on Nov 23." in text
    assert "$0.00 dining dollars left on Dec 18 It runs out on Dec 8." in text
    assert '<input class="slider" id="rate" type="range" name="rate" min="0" max="4" step="0.25" value="2.0">' in section
    assert '<form class="whatif" id="what-if-form" method="get" action="/plan#what-if">' in section and 'type="submit"' in section
    assert section.count('<figure class="chart"') == 2 and "at 2 a day" in section
    assert 'data-tip="1 meal a day\n50 left on Dec 18\nlasts to the end"' in section

    half = part(client.get("/plan?rate=0.5").text, "what-if", "plan-size")
    assert f'fill="{kit.tint(kit.MEALS, charts.PICKED)}"' in half and f'fill="{kit.tint(kit.DOLLARS, charts.PICKED)}"' in half  # the picked bar
    slow = plain(half)
    assert "0.5 meals a day" in slow and "95.5 exchanges left on Dec 18 They last to the end." in slow
    assert "$293.00 dining dollars left on Dec 18 It lasts to the end." in slow
    none = plain(part(client.get("/plan?rate=0").text, "what-if", "plan-size"))
    assert "Nothing is used at 0 meals a day, so both pots stay as they are." in none and "141 exchanges left on Dec 18" in none


def test_what_if_rate_is_read_safely():
    assert [page.read_rate(raw) for raw in (None, "", "lots", "nan", "1", "1.3", "-2", "99", "0")] == [2.0, 2.0, 2.0, 2.0, 1.0, 1.25, 0.0, 4.0, 0.0]


def test_view_choices_are_kept_in_links_and_in_the_form(client):
    html = client.get("/plan?as_of=2026-09-10&away=0&dd=0&rate=1.5").text
    links = hrefs(html)
    assert "viewing Sep 10, days away counted" in html
    assert "/plan?as_of=2026-09-10&away=0&dd=0&rate=1.5#what-if" in links  # jump links keep everything
    assert "/plan?as_of=2026-09-10&away=0&rate=1.5#days" in links  # the toggle keeps the rate
    assert "/now?as_of=2026-09-10&away=0" in links and "/data?as_of=2026-09-10&away=0" in links
    assert "/plan?as_of=2026-09-10&away=0&dd=0&rate=1.5&theme=light" in links
    internal = [h for h in links if h.startswith("/") and not h.startswith("/static")]
    assert all("as_of=2026-09-10" in h for h in internal if h != "/plan")
    for name, value in (("as_of", "2026-09-10"), ("away", "0"), ("dd", "0")):
        assert f'<input type="hidden" name="{name}" value="{value}">' in html
    assert 'name="rate" min="0" max="4" step="0.25" value="1.5"' in html and "1.5 meals a day" in plain(html)


def test_a_past_day_and_away_days_change_the_plan(client):
    text = plain(client.get("/plan?as_of=2026-09-07&away=0&dd=0").text)
    assert "1 meal tomorrow." in text and "Using everything up takes 146 meals (146 exchanges) over 102 days, Sep 8 to Dec 18." in text
    assert "102 days left" in text and "usable days left" not in text


def test_plan_size(with_plans):
    html = with_plans.client().get("/plan").text
    section = html[html.index('id="plan-size"') :]
    text = plain(section)
    assert "This semester you are on pace to use about 95 exchanges (likely 89 to 100). At that usage Block 100 costs the least: $590 less than yours." in text
    assert "All Access $3,670 unlimited $150 unlimited $3,520 $37.05 $1,690 more" in text
    assert "Block 160 (yours) $2,190 160 $360 65 unused $1,830 $19.26" in text
    assert "Block 100 $1,600 100 $360 5 unused $1,240 $13.05 $590 less" in text
    assert "Food cost is the plan price, less the dining dollars it comes with" in text
    assert 'data-tip="Block 160 (yours)\n$2,190 less $360 in dining dollars\n$1,830"' in section
    assert section.count('<figure class="chart"') == 1 and section.count("<table") == 1


def test_plan_size_counts_the_meals_a_small_plan_would_not_cover(with_plans):
    with_plans.edit_config('name = "Block 100"\nexchanges = 100', 'name = "Block 50"\nexchanges = 50')
    html = with_plans.client().get("/plan").text
    assert "Block 50 $1,600 50 $360 45 short ($675 at $15 a meal) $1,915 $20.16 $85 more" in plain(html)
    assert 'data-tip="Block 50\n45 meals it would not cover\n$675 at $15.00 each"' in html and 'class="stripe"' in html
    assert "At that usage your plan costs the least." in plain(html)


def test_plan_size_without_plans_says_so(client):
    assert "Nothing to compare: no plans listed. Add [[plans]] entries to config.toml." in plain(client.get("/plan").text)


def test_stale_data_leaves_out_the_day_by_day_plan(site):
    html = site.client(today=date(2026, 10, 1)).get("/plan").text
    text = plain(html)
    assert "Your data ends Sep 13, 18 days ago." in text and "That is too old to say what today should look like." in text
    assert '<li class="problem" data-sign="✕"' in html and "The day by day plan is left out until there is new data." in text
    assert '<a href="/data">Go to data</a>' in html
    assert 'id="days"' not in html and 'id="weekdays"' not in html and "/plan#days" not in hrefs(html)
    for kept in ('id="exchanges"', 'id="spend-down"', 'id="what-if"', 'id="plan-size"'):
        assert kept in html
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html) == ["meals", "look", "days"]


def test_data_a_day_behind_says_what_the_plan_assumes(site):
    text = plain(site.client(today=date(2026, 9, 16)).get("/plan").text)
    assert "The data ends Sep 13. The plan starts today and assumes Sep 14 to 15 went the way those days of the week usually do" in text
    assert "Get new data for a sharper plan." in text


def test_semester_over_has_nothing_to_plan(site):
    site.edit_config("end = 2026-12-18", "end = 2026-09-13")
    html = site.client().get("/plan").text
    text = plain(html)
    assert "The semester is over." in text and "No days left to plan." in text
    assert "The semester is over. Nothing left to spread out." in text
    assert "The semester is over, so no rate changes what is left." in text
    assert 'id="days"' not in html and 'id="exchanges"' not in html and 'class="fig"' in html
    figures = re.findall(r'<span class="figure">([^<]*)</span>\s*<span class="label">([^<]*)</span>', html)
    assert figures == [("141", "exchanges left"), ("$293", "dining dollars left"), ("0", "usable days left")]


def test_long_after_and_before_the_semester_do_not_crash(site):
    for today in (date(2027, 1, 5), date(2026, 8, 1)):
        assert site.client(today=today).get("/plan").status_code == 200
    early = plain(site.client(today=date(2026, 8, 1)).get("/plan?as_of=2026-08-01").text)
    assert "Plan starts Fri, Aug 21: 2 meals that day." in early and "spread evenly, with no weekday pattern yet" in early


def test_a_surplus_no_day_can_hold_is_called_out(site):
    site.edit_config("end = 2026-12-18", "end = 2026-09-30")
    html = site.client().get("/plan").text
    assert re.search(r"Even at 3 meals every day, \d+ meals would go unused by semester end\.", plain(html))
    assert '<li class="look" data-sign="!"' in html


def test_no_data_page(empty_client):
    html = empty_client.get("/plan").text
    assert "No dining data yet." in html and "<svg" not in html and 'class="band' not in html and "<form" not in html
    assert '<a class="btn" href="/data">Go to data</a>' in html


def test_every_colour_on_the_page_is_a_palette_meaning(with_plans):
    from dinemaster import palette

    for today in (date(2026, 9, 14), date(2026, 10, 1)):
        html = with_plans.client(today=today).get("/plan?rate=1&dd=0").text
        found = set(re.findall(r"#[0-9A-Fa-f]{6}\b", html))
        assert found and all(palette.meaning_of(c) is not None for c in found)
        assert "rgb(" not in html and set(palette.VAR.findall(html)) <= set(palette.theme_vars("dark"))


def test_sentences_turns_dashes_into_full_stops():
    assert page.sentences("Swipe first — use about 11 a week.") == "Swipe first. Use about 11 a week."
    assert page.sentences("Sat–Sun") == "Sat to Sun"


def test_charts_with_nothing_in_them_say_so():
    assert "No days left to plan." in charts.weekday_chart([None] * 7, [0.0] * 7, [0] * 7)
