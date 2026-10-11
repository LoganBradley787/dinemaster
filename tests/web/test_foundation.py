"""The frame every section shares: routes, the top bar, view parameters, saved settings, POST."""

from __future__ import annotations

import dataclasses
import re
from datetime import date
from pathlib import Path

import pytest

import dinemaster.__main__ as launcher
from dinemaster.web import fmt, settings
from dinemaster.web.context import CalendarCache, ViewParams, load_context
from dinemaster.web.pages import data as data_page
from dinemaster.web.sections import SECTIONS

from conftest import AS_OF, COVERED_THROUGH

STATIC = Path(settings.__file__).parent / "static"
# A section is still a stub while its template carries the "not built yet" line; building one removes it.
TEMPLATES = Path(settings.__file__).parent / "templates"
STUBS = [s.slug for s in SECTIONS if "is not built yet." in (TEMPLATES / f"{s.slug}.html").read_text()]


def hrefs(html: str) -> list[str]:
    return [h.replace("&amp;", "&") for h in re.findall(r'href="([^"]+)"', html)]


# --- routes -------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/kit", *[s.path for s in SECTIONS]])
def test_every_page_renders_with_and_without_data(client, empty_client, path):
    for c in (client, empty_client):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "<title>" in r.text and 'class="pivot"' in r.text


def test_sections_are_the_seven_in_the_spec():
    assert [s.slug for s in SECTIONS] == ["now", "forecast", "plan", "habits", "compare", "math", "data"]


@pytest.mark.parametrize("slug", STUBS)
def test_unbuilt_sections_say_so(client, slug):
    assert f"The {slug} page is not built yet." in client.get(f"/{slug}").text


def test_unknown_page_is_404(client):
    assert client.get("/nope").status_code == 404


def test_static_files_are_served_and_free_of_streamlit(client):
    css = client.get("/static/metro.css")
    assert css.status_code == 200 and "@keyframes metro-flip" in css.text and "prefers-reduced-motion" in css.text
    assert "stApp" not in css.text and "data-testid" not in css.text
    assert client.get("/static/app.js").status_code == 200


def test_top_bar_has_the_wordmark_pivot_and_freshness(client):
    html = client.get("/now").text
    assert '<a class="wordmark" href="/">DineMaster</a>' in html
    assert "as of Sep 14" in html and "data through Sep 13, yesterday" in html
    assert '<a href="/now" aria-current="page">now</a>' in html
    assert html.count('aria-current="page"') == 1
    for s in SECTIONS:
        assert f'href="{s.path}"' in html
    start = client.get("/").text
    assert '<a href="/" aria-current="page">start</a>' in start


def test_no_em_dashes_in_the_pages_this_phase_wrote(client, empty_client):
    for c, path in [(client, "/now"), (client, "/kit"), (client, "/data"), (empty_client, "/"), (empty_client, "/now")]:
        assert "—" not in c.get(path).text, path


# --- the Start screen ---------------------------------------------------------------------------


def test_start_shows_the_tiles_and_each_group_links_to_a_section(client):
    html = client.get("/").text
    assert '<div class="metro"><div class="groups">' in html and "On pace to leave" in html
    assert '<h2><a href="/now">right now</a></h2>' in html and '<h2><a href="/plan">pace</a></h2>' in html
    assert re.search(r'<a href="/now" class="tile large live"', html)  # tiles are links, live flips kept
    assert "<style>" not in html  # the tiles' CSS comes from metro.css


def test_start_without_data_says_what_to_do(empty_client, empty_site):
    html = empty_client.get("/").text
    assert "No dining data yet." in html and str(empty_site.root / "raw-data") in html
    assert "/data" in hrefs(html) and 'class="tile' not in html and "no data yet" in html


# --- view parameters ----------------------------------------------------------------------------


def test_view_params_parse_and_ignore_bad_values():
    assert ViewParams.parse({"as_of": "2026-09-07", "away": "0"}) == ViewParams(date(2026, 9, 7), False)
    assert ViewParams.parse({"as_of": "soon", "away": "maybe", "other": "x"}) == ViewParams()
    assert ViewParams(date(2026, 9, 7), True).query() == {"as_of": "2026-09-07", "away": "1"}
    assert ViewParams().query() == {}


def test_view_params_are_honored_and_kept_in_every_link(client):
    html = client.get("/now?as_of=2026-09-07&away=0&unit=meals").text
    assert "viewing Sep 7, days away counted" in html
    assert "146 exchanges" in html and "with 102 days to go" in html  # a week earlier, away days counted
    links = hrefs(html)
    for s in SECTIONS:
        assert f"{s.path}?as_of=2026-09-07&away=0" in links
    assert "/?as_of=2026-09-07&away=0" in links
    assert '<a class="reset" href="/now">reset</a>' in html
    internal = [h for h in links if h.startswith("/") and not h.startswith("/static")]
    assert all("as_of=2026-09-07" in h for h in internal if h != "/now")


def test_default_view_has_no_reset_and_plain_links(client):
    html = client.get("/now?as_of=nonsense").text
    assert 'class="reset"' not in html and "as of Sep 14" in html and "/forecast" in hrefs(html)
    same = client.get(f"/now?as_of={AS_OF}&away=1").text  # asking for the defaults is not a change
    assert 'class="reset"' not in same


def test_start_tiles_keep_the_view_params(client):
    html = client.get("/?as_of=2026-09-07").text
    assert "/now?as_of=2026-09-07" in hrefs(html) and "viewing Sep 7" in html


def test_ctx_url_adds_replaces_and_removes(site):
    ctx = load_context(site.config_path, {"as_of": "2026-09-07"}, today=AS_OF)
    assert ctx.url("/now") == "/now?as_of=2026-09-07"
    assert ctx.url("/now#dd", unit="meals") == "/now?as_of=2026-09-07&unit=meals#dd"
    assert ctx.url("/now", as_of=None) == "/now"
    assert ctx.url("/now", as_of=date(2026, 9, 1)) == "/now?as_of=2026-09-01"
    assert load_context(site.config_path, today=AS_OF).url("/plan") == "/plan"


# --- the page context ---------------------------------------------------------------------------


def test_context_offers_the_data_worked_out_at_the_observed_day(site):
    ctx = load_context(site.config_path, today=AS_OF)
    assert ctx.has_data and ctx.today == AS_OF and ctx.cfg.as_of == AS_OF
    assert ctx.observed_through == COVERED_THROUGH == ctx.through == ctx.counts.a
    assert ctx.behind == 1 and not ctx.stale and ctx.variant == "away"
    assert ctx.metrics.balances.me_left == 141 and ctx.daily.index[-1] == COVERED_THROUGH
    assert ctx.forecast.observed_through == COVERED_THROUGH and ctx.forecast.me.available
    assert ctx.now.a == AS_OF and ctx.report.files and not ctx.view_changed
    away = load_context(site.config_path, ViewParams(away=False), today=AS_OF)
    assert away.variant == "plain" and away.view_changed and away.base_cfg.exclude_away


def test_context_without_exports_is_empty_but_whole(empty_site):
    ctx = load_context(empty_site.config_path, today=AS_OF)
    assert not ctx.has_data and ctx.through is None and ctx.metrics.balances.me_left == 160


def test_calendar_files_become_away_periods_and_markers(site):
    folder = site.root / "calendar"
    folder.mkdir()
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN"]
    for title, start, end in [("Fall break", "20261010", "20261014"), ("Final exams", "20261210", "20261219")]:
        lines += ["BEGIN:VEVENT", f"UID:{title}@test", f"SUMMARY:{title}", f"DTSTART;VALUE=DATE:{start}", f"DTEND;VALUE=DATE:{end}", "END:VEVENT"]
    (folder / "school.ics").write_text("\r\n".join([*lines, "END:VCALENDAR", ""]))
    cache = CalendarCache()
    ctx = load_context(site.config_path, today=AS_OF, calendar_cache=cache)
    assert [p.name for p in ctx.cfg.away_periods] == ["Thanksgiving recess", "Fall break"]
    assert [mk.name for mk in ctx.cfg.markers] == ["Final exams"] and ctx.calendar.sources == ["school.ics"]
    html = site.client().get("/now").text
    assert "Fall break" in html and "Final exams" in html  # both are drawn on the balance charts
    (folder / "school.ics").unlink()  # the cache notices the file is gone
    assert len(load_context(site.config_path, today=AS_OF, calendar_cache=cache).cfg.away_periods) == 1


# --- saved settings -----------------------------------------------------------------------------


def test_settings_save_load_reset_round_trip(site):
    base = load_context(site.config_path, today=AS_OF)
    path = base.settings_path
    assert path == site.root / "data" / "settings.json" and settings.load(path) == {} and base.saved == {}
    values, problems = settings.clean({"starting_me": "150", "semester_end": "2026-12-11", "dd_rollover": "on", "exclude_away": False, "unknown": 1})
    assert problems == [] and values == {"starting_me": 150.0, "semester_end": "2026-12-11", "dd_rollover": True, "exclude_away": False}
    settings.save(path, values)
    ctx = load_context(site.config_path, today=AS_OF)
    assert ctx.cfg.starting_me == 150 and ctx.cfg.semester_end == date(2026, 12, 11) and ctx.cfg.dd_rollover and not ctx.cfg.exclude_away
    assert ctx.saved == values and ctx.file_cfg.starting_me == 160 and ctx.metrics.balances.me_left == 131
    assert "131 exchanges" in site.client().get("/now").text  # pages use the saved values
    settings.reset(path)
    settings.reset(path)  # resetting twice is fine
    assert not path.exists() and load_context(site.config_path, today=AS_OF).cfg.starting_me == 160


def test_settings_clean_reports_bad_values_in_plain_words():
    values, problems = settings.clean({"meal_price": "0", "semester_start": "tomorrow", "rounding_mode": "up", "starting_dd": "abc", "meal_threshold": ""})
    assert values == {}
    assert problems == [
        "semester start is not a valid date.",
        "starting dd is not a valid number.",
        "meal price must be more than zero.",
        "rounding mode must be floor, round or ceil.",
    ]


def test_settings_file_that_is_broken_is_ignored(site):
    path = site.root / "data" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text("{not json")
    assert settings.load(path) == {}
    path.write_text('{"starting_me": "lots", "meal_price": 12}')
    ctx = load_context(site.config_path, today=AS_OF)
    assert ctx.cfg.starting_me == 160 and ctx.cfg.meal_price == 12


def test_saved_away_switches_apply_by_key(site):
    ctx = load_context(site.config_path, today=AS_OF)
    period = ctx.cfg.away_periods[0]
    assert settings.away_key(0, period) == "0"
    calendar_period = dataclasses.replace(period, source="calendar")
    assert settings.away_key(3, calendar_period) == "cal_2026-11-25_2026-11-29"
    settings.save(ctx.settings_path, {"away": {"0": {"enabled": False, "start": "2026-11-24", "end": "2026-11-29"}}})
    changed = load_context(site.config_path, today=AS_OF).cfg.away_periods[0]
    assert not changed.enabled and changed.start == date(2026, 11, 24) and changed.name == "Thanksgiving recess"


def test_view_param_overrides_the_saved_away_setting(site):
    ctx = load_context(site.config_path, today=AS_OF)
    settings.save(ctx.settings_path, {"exclude_away": False})
    assert not load_context(site.config_path, today=AS_OF).cfg.exclude_away
    forced = load_context(site.config_path, {"away": "1"}, today=AS_OF)
    assert forced.cfg.exclude_away and forced.view_changed


# --- POST ---------------------------------------------------------------------------------------


def test_post_to_a_section_without_a_handler_is_405(client):
    assert client.post("/forecast", data={"x": "1"}).status_code == 405


def test_post_calls_the_section_handler_and_redirects(client, monkeypatch):
    seen = {}

    def post(ctx, request, action, form):
        seen.update(action=action, form=form, me=ctx.cfg.starting_me)
        return ctx.url("/data", saved=1)

    monkeypatch.setattr(data_page, "post", post, raising=False)
    r = client.post("/data/settings?as_of=2026-09-07", data={"starting_me": "150"})
    assert r.status_code == 303 and r.headers["location"] == "/data?as_of=2026-09-07&saved=1"
    assert seen == {"action": "settings", "form": {"starting_me": "150"}, "me": 160}
    assert client.post("/data").status_code == 303 and seen["action"] == ""


# --- formatters and the launcher ----------------------------------------------------------------


def test_formatters():
    assert fmt.signed(3.24) == "+3.2" and fmt.signed(-1.0) == "-1" and fmt.signed(0.01) == "0" and fmt.signed(None) == "n/a"
    assert fmt.pct(0.426) == "43%" and fmt.weekday(date(2026, 10, 11)) == "Sunday, Oct 11"
    assert fmt.between(30, 46) == "30 to 46" and fmt.between(30.2, 30.4) == "30"
    assert fmt.money(1234.5) == "$1,234" and fmt.plural(1, "meal") == "1 meal"


def test_launcher_serves_on_localhost_and_can_skip_the_browser(monkeypatch):
    calls = {}
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
    monkeypatch.setattr(launcher.threading, "Timer", lambda *a, **k: calls.setdefault("opened", True) and None)
    launcher.main(["--port", "8199", "--no-open"])
    assert calls["host"] == "127.0.0.1" and calls["port"] == 8199 and calls["factory"] and "opened" not in calls
    assert launcher.PORT == 8160
