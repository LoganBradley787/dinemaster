"""The data section: freshness, the background fetch, pasted-URL links, health tables, saved settings,
the view controls and the calendar. The fetch launcher is always replaced: no test opens a browser."""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta

import pytest

from dinemaster.fetch import NEEDS_YOU
from dinemaster.web import settings
from dinemaster.web.context import load_context
from dinemaster.web.pages import data as page

from conftest import AS_OF, write_csv

EXPORT = """
[export]
login_url = "https://dining.example.test/login.php?cid=1"

[[export.accounts]]
name = "Block 160 Meals"
acct = "11"

[[export.accounts]]
name = "Dining Dollars"
acct = "22"
"""


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Each test starts with no fetch, no messages and no links, and a launcher that must not be reached."""

    def never(config_path, on_line, quiet=False):
        raise AssertionError("a test reached the real fetch launcher")

    monkeypatch.setattr(page, "run_fetch", never)
    monkeypatch.setattr(page, "FETCH", page.Fetch())
    page.FLASH.clear()
    page.LINKS.clear()
    yield
    page.FLASH.clear()
    page.LINKS.clear()


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").replace("\xa0", " ").replace("&#39;", "'"))


def hrefs(html: str) -> list[str]:
    return [h.replace("&amp;", "&") for h in re.findall(r'href="([^"]+)"', html)]


def with_export(site) -> None:
    site.config_path.write_text(site.config_path.read_text() + EXPORT)


def settings_form(site, **changes) -> dict:
    """The settings form as the page would post it unchanged, then `changes` (None removes a field)."""
    ctx = load_context(site.config_path, today=AS_OF)
    view = page._settings_view(ctx, None)
    form = {name: value for name, value in view["v"].items() if not isinstance(value, bool)}
    form.update({name: "1" for name, value in view["v"].items() if value is True})
    for a in view["away"]:
        form[f"away_start_{a['key']}"], form[f"away_end_{a['key']}"] = a["start"], a["end"]
        if a["enabled"]:
            form[f"away_enabled_{a['key']}"] = "1"
    for name, value in changes.items():
        if value is None:
            form.pop(name, None)
        else:
            form[name] = value
    return form


# --- the page -----------------------------------------------------------------------------------


def test_page_leads_with_freshness_and_keeps_the_order(client):
    r = client.get("/data")
    html, text = r.text, plain(r.text)
    assert r.status_code == 200 and "is not built yet" not in html
    assert "Your data runs through Sep 13, yesterday." in text
    assert "29 transactions from 2 files are in the ledger." in text
    order = [html.index(f'id="{part}"') for part in ("get", "health", "settings", "view", "calendar")]
    assert order == sorted(order)
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html) == ["days", "good", "good", "good"]
    assert "1 day behind" in text and "0 problems reading the files" in text and "2 of 2 pots add up" in text


def test_last_transaction_per_account_and_files_read(client):
    text = plain(client.get("/data").text)
    assert "Block 160 Meals Sep 11, 2026 3 days ago" in text and "Dining Dollars Sep 12, 2026 2 days ago" in text
    assert "2 files read" in text and "Block 160 Meals_statement_2026-08-01_to_2026-09-13.csv Block 160 Meals meal exchanges" in text
    assert "Rows New Duplicate" in text and "No files ingested yet." not in text


def test_health_tables(site):
    html = site.client().get("/data").text
    text = plain(html)
    assert "Meal exchanges 141 141 160 Adds up." in text
    assert "Dining dollars $293.00 $293.00 $360.00 Adds up." in text
    assert f"Ledger path: {site.root / 'data' / 'ledger.csv'}" in text
    assert "Balance jumps" in text and "None found." in text and "Posted late" not in text
    assert "$50.00 went on 4 meals and $17.00 on 4 snacks." in text
    assert "A meal 4 $50.00" in text and "A snack 4 $17.00" in text
    assert "Crossroads Grill 4 $50.00 0 $0.00" in text and "Gastons 0 $0.00 4 $17.00" in text


def test_split_chart_is_teal_with_tooltips_and_a_legend(client):
    html = client.get("/data").text
    assert html.count('<figure class="chart"') == 1
    figure = html[html.index('<figure class="chart"') : html.index("</figure>")]
    assert "var(--dollars-mark)" in figure and "var(--meals-mark)" not in figure
    assert "counted as a meal" in figure and "counted as a snack" in figure
    assert "Crossroads Grill\n$50.00 on 4 meals" in figure.replace("&#10;", "\n") and "Gastons" in figure


def test_empty_site_still_offers_the_ways_in(empty_site):
    html = empty_site.client().get("/data").text
    text = plain(html)
    assert "No dining data yet." in text and "Re-read files" in text and "No files ingested yet." in text
    assert 'class="band' not in html and "Nothing to check yet." in text and "Save settings" in html
    assert "Get new data" not in re.sub(r"<h2>.*?</h2>", "", html)  # no [export] section: no button
    assert f"put new CSV exports into {empty_site.root / 'raw-data'}" in text
    with_export(empty_site)
    text = plain(empty_site.client().get("/data").text)
    assert "Press Get new data and log in to the dining site." in text


def test_stale_data_is_a_problem(site):
    html = site.client(today=date(2026, 10, 1)).get("/data").text
    assert "Your data is 18 days old. It ends Sep 13." in plain(html)
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html)[0] == "problem" and "18 days behind" in plain(html)


def test_semester_over_still_renders(site):
    r = site.client(today=date(2027, 1, 20)).get("/data")
    assert r.status_code == 200 and "Your data is 129 days old." in plain(r.text)


def test_reading_problems_and_balance_jumps_are_listed(site):
    write_csv(site.root / "raw-data", "Mystery Account_statement_2026-08-01_to_2026-09-13.csv", [("2026-09-01 09:00:00", "Thing", -1, 4)])
    write_csv(
        site.root / "raw-data",
        "Dining Dollars_statement_2026-09-13_to_2026-09-13.csv",
        [("2026-09-13 09:00:00", "Crossroads Grill", -10.0, 300.0)],  # the site says 300.00; the chain says 283.00
    )
    html = site.client().get("/data").text
    text = plain(html)
    assert "unknown account 'Mystery Account'" in text and "1 problem reading the files" in text
    assert "Sep 13, 2026 Dining Dollars Crossroads Grill 283.00 300.00 +17.00" in text
    kinds = re.findall(r'<p class="stat" data-kind="(\w+)"', html)
    assert kinds[1] == "look" and kinds[2] == "look" and "1 unexplained balance change" in text
    assert "—" not in html


def test_no_em_dash_and_links_keep_view_params(client):
    html = client.get("/data?as_of=2026-09-07&away=0").text
    assert "—" not in html
    internal = [h for h in hrefs(html) if h.startswith("/") and not h.startswith("/static")]
    resets = {"/data", "/data?away=0", "/data#view"}  # the top bar's reset and the two "back to today" links drop the view on purpose
    assert all("as_of=2026-09-07" in h for h in internal if h not in resets)
    assert "/now?as_of=2026-09-07&away=0" in internal and "/data/settings?as_of=2026-09-07&away=0" in html.replace("&amp;", "&")
    assert "You are viewing Sep 7." in plain(html)


# --- the view controls --------------------------------------------------------------------------


def test_view_form_is_a_plain_get_and_changes_the_page(client):
    html = client.get("/data").text
    assert '<form class="view-date" method="get" action="/data">' in html and f'name="as_of" value="{AS_OF}"' in html
    assert "/data?away=0#view" in hrefs(html)  # the setting is "left out", so "Counted" is the override
    assert "/data#view" in hrefs(html) and "Back to today and the saved setting" not in html
    past = client.get("/data?as_of=2026-09-07&away=0").text
    assert 'name="as_of" value="2026-09-07"' in past and '<input type="hidden" name="away" value="0">' in past
    assert "Meal exchanges 146 " in plain(past)  # the health check is worked out at the day viewed
    assert "/data#view" in hrefs(past) and "Back to today and the saved setting" in past
    assert re.search(r'<a href="[^"]+" aria-current="true">Counted</a>', past)
    assert re.search(r'<a href="[^"]+" aria-current="true">Left out</a>', html)


# --- settings -----------------------------------------------------------------------------------


def test_settings_form_offers_every_setting(client):
    html = client.get("/data").text
    for name in settings.FIELDS:
        assert f'name="{name}"' in html, name
    assert 'name="away_enabled_0" value="1" checked' in html and 'name="away_start_0" value="2026-11-25"' in html
    assert "Thanksgiving recess" in html and "Everything matches config.toml." in html
    assert '<option value="floor" selected>' in html and 'name="exclude_away" value="1" checked' in html
    assert 'name="dd_rollover" value="1" checked' not in html


def test_saving_settings_writes_only_what_changed(site):
    client = site.client()
    r = client.post("/data/settings?as_of=2026-09-07", data=settings_form(site, starting_me="150", dd_rollover="1", exclude_away=None))
    assert r.status_code == 303 and r.headers["location"] == "/data?as_of=2026-09-07#settings"
    saved = json.loads((site.root / "data" / "settings.json").read_text())
    assert saved == {"starting_me": 150.0, "dd_rollover": True, "exclude_away": False}
    html = client.get("/data").text
    text = plain(html)
    assert "Settings saved." in text and "3 settings differ from config.toml." in text and "config.toml says 160." in text
    assert 'name="starting_me" value="150"' in html and 'name="dd_rollover" value="1" checked' in html
    assert "Meal exchanges 131 141" in text and "1 of 2 pots add up" in text  # the new start no longer matches the deposits
    assert "131 exchanges" in client.get("/now").text
    assert "Settings saved." not in client.get("/data").text  # said once


def test_saving_unchanged_settings_leaves_no_file(site):
    client = site.client()
    assert client.post("/data/settings", data=settings_form(site)).status_code == 303
    assert not (site.root / "data" / "settings.json").exists()


def test_away_switches_are_saved_and_shown(site):
    client = site.client()
    client.post("/data/settings", data=settings_form(site, away_enabled_0=None, away_start_0="2026-11-24"))
    saved = json.loads((site.root / "data" / "settings.json").read_text())
    assert saved == {"away": {"0": {"enabled": False, "start": "2026-11-24", "end": "2026-11-29"}}}
    html = client.get("/data").text
    assert 'name="away_enabled_0" value="1" checked' not in html and 'name="away_start_0" value="2026-11-24"' in html
    assert "Was on, Nov 25 to Nov 29." in plain(html) and "1 setting differs from config.toml." in plain(html)
    client.post("/data/settings", data=settings_form(site, away_enabled_0="1", away_start_0="2026-11-25"))
    assert not (site.root / "data" / "settings.json").exists()  # put back: nothing differs any more


def test_bad_settings_save_nothing_and_keep_what_was_typed(site):
    client = site.client()
    form = settings_form(site, meal_price="0", starting_me="150", semester_end="2026-08-01", away_end_0="2026-11-01")
    assert client.post("/data/settings", data=form).headers["location"] == "/data#settings"
    assert not (site.root / "data" / "settings.json").exists()
    html = client.get("/data").text
    text = plain(html)
    assert "Meal price must be more than zero." in text and "The semester must end on or after the day it starts." in text
    assert "Thanksgiving recess ends before it starts." in text and "Nothing was saved." in text
    assert 'name="starting_me" value="150"' in html and 'name="meal_price" value="0"' in html
    assert 'name="starting_me" value="160"' in client.get("/data").text  # the next view is back to what is in force


def test_reset_removes_the_saved_file(site):
    client = site.client()
    client.post("/data/settings", data=settings_form(site, starting_me="150"))
    assert (site.root / "data" / "settings.json").exists()
    r = client.post("/data/reset")
    assert r.status_code == 303 and r.headers["location"] == "/data#settings"
    assert not (site.root / "data" / "settings.json").exists()
    assert "Settings are back to config.toml." in client.get("/data").text
    assert client.post("/data/reset").status_code == 303  # twice is fine


# --- get new data -------------------------------------------------------------------------------


def test_fetch_button_needs_the_export_section(site):
    client = site.client()
    assert 'action="/data/fetch"' not in client.get("/data").text
    assert client.post("/data/fetch").status_code == 303
    assert page.FETCH.state == "idle" and "Set login_url" in client.get("/data").text
    with_export(site)
    html = site.client().get("/data").text
    assert 'action="/data/fetch"' in html and ">Get new data</button>" in html


def test_fetch_runs_in_the_background_one_at_a_time(site, monkeypatch):
    with_export(site)
    client = site.client()
    go, seen = threading.Event(), []

    def launcher(config_path, on_line, quiet=False):
        seen.append(config_path)
        on_line("Block 160 Meals: Sep 11 to Sep 14")
        assert go.wait(10)
        on_line("Dining Dollars: nothing in this range")
        return 0, ""

    monkeypatch.setattr(page, "run_fetch", launcher)
    r = client.post("/data/fetch?as_of=2026-09-07")
    assert r.status_code == 303 and r.headers["location"] == "/data?as_of=2026-09-07#get"  # answered at once, while it runs
    for _ in range(200):
        if page.FETCH.status()["lines"]:
            break
        threading.Event().wait(0.01)
    status = client.get("/data/fetch-status").json()
    assert status["state"] == "running" and status["lines"] == ["Downloaded Block 160 Meals: Sep 11 to Sep 14"]
    html = client.get("/data").text
    assert "Getting new data with no window. If the login needs you, a browser window opens." in html and 'role="progressbar"' in html
    assert "Getting new data</button>" in html and "disabled" in html and "Downloaded Block 160 Meals: Sep 11 to Sep 14" in html
    assert '<meta http-equiv="refresh" content="5">' in html and "data-fetch" in html and ">updating</a>" in html

    client.post("/data/fetch")  # a second press while it runs starts nothing
    assert seen == [site.config_path] and "A fetch is already running." in client.get("/data").text

    go.set()
    page.FETCH.thread.join(10)
    assert client.get("/data/fetch-status").json()["state"] == "done"
    html = client.get("/data").text
    text = plain(html)
    assert "The fetch finished at" in text and "Downloaded Block 160 Meals: Sep 11 to Sep 14." in text
    assert "Dining Dollars: nothing in this range." in text and "Downloaded Dining Dollars" not in text
    assert 'http-equiv="refresh"' not in html and "data-fetch" not in html and ">Get new data</button>" in html
    client.post("/data/fetch")  # and it can run again afterwards
    page.FETCH.thread.join(10)
    assert len(seen) == 2


@pytest.mark.parametrize(
    ("outcome", "said"),
    [
        ((1, "Traceback\nFetch failed: the login was not finished in time or the window was closed\n"), "Fetch failed: the login was not finished in time or the window was closed"),
        ((2, ""), "Fetch failed."),
        (TimeoutError(), "Fetch failed: it took too long."),
        (OSError("no python"), "Fetch failed: no python"),
    ],
)
def test_fetch_failures_are_said_on_the_page(site, monkeypatch, outcome, said):
    with_export(site)
    client = site.client()

    def launcher(config_path, on_line, quiet=False):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(page, "run_fetch", launcher)
    client.post("/data/fetch")
    page.FETCH.thread.join(10)
    assert client.get("/data/fetch-status").json() == {
        "state": "failed", "quiet": True, "lines": [], "error": said, "finished": page.FETCH.status()["finished"],
    }  # fmt: skip
    html = client.get("/data").text
    assert said in plain(html) and 'class="problem"' in html and "You can try again." in html


def test_run_fetch_starts_the_fetch_module_with_the_config(monkeypatch, tmp_path):
    """The real launcher, with Popen replaced: the command, the config it names, and the lines passed on."""
    import io
    import subprocess

    started = {}

    class Proc:
        stdout = io.StringIO("Block 160 Meals: Sep 11 to Sep 14\n\n")
        stderr = io.StringIO("Fetch failed: nope\n")

        def wait(self):
            return 1

        def kill(self):
            started["killed"] = True

    def popen(cmd, **kw):
        started.update(cmd=cmd, env=kw["env"], cwd=kw["cwd"])
        return Proc()

    monkeypatch.setattr(subprocess, "Popen", popen)
    lines = []
    code, err = REAL_RUN_FETCH(tmp_path / "config.toml", lines.append)
    assert (code, err) == (1, "Fetch failed: nope\n") and lines == ["Block 160 Meals: Sep 11 to Sep 14"]
    assert started["cmd"][1:] == ["-m", "dinemaster.fetch"] and started["env"]["DINEMASTER_CONFIG"] == str(tmp_path / "config.toml")
    assert "killed" not in started
    REAL_RUN_FETCH(tmp_path / "config.toml", lines.append, True)
    assert started["cmd"][1:] == ["-m", "dinemaster.fetch", "--quiet"]


REAL_RUN_FETCH = page.run_fetch  # taken at import, before any fixture replaces it


def test_button_opens_a_window_only_when_the_quiet_run_needs_you(site, monkeypatch):
    with_export(site)
    client = site.client()
    runs, go = [], threading.Event()

    def launcher(config_path, on_line, quiet=False):
        runs.append(quiet)
        if quiet:
            return NEEDS_YOU, "Needs you: Duo wants you to approve this browser again.\n"
        assert go.wait(10)
        on_line("Dining Dollars: Sep 12 to Sep 14")
        return 0, ""

    monkeypatch.setattr(page, "run_fetch", launcher)
    client.post("/data/fetch")
    for _ in range(200):
        if runs == [True, False]:
            break
        threading.Event().wait(0.01)
    html = client.get("/data").text
    assert "A browser window is open. Log in there if it asks, then wait." in html and client.get("/data/fetch-status").json()["quiet"] is False
    go.set()
    page.FETCH.thread.join(10)
    assert runs == [True, False] and page.FETCH.state == "done"
    assert page.read_record(site.root / "data" / "last-fetch.json")["state"] == "done"


def test_a_quiet_run_that_works_opens_no_window(site, monkeypatch):
    with_export(site)
    runs = []
    monkeypatch.setattr(page, "run_fetch", lambda config_path, on_line, quiet=False: runs.append(quiet) or (0, ""))
    site.client().post("/data/fetch")
    page.FETCH.thread.join(10)
    assert runs == [True] and page.FETCH.state == "done"


def auto_site(site, hours="4"):
    with_export(site)
    site.edit_config('login_url = "https://dining.example.test/login.php?cid=1"\n', f'login_url = "https://dining.example.test/login.php?cid=1"\nauto_hours = {hours}\n')


def test_opening_the_app_fetches_by_itself_and_never_opens_a_window(site, monkeypatch):
    auto_site(site)
    runs = []

    def launcher(config_path, on_line, quiet=False):
        runs.append(quiet)
        return NEEDS_YOU, "Needs you: Duo wants you to approve this browser again.\n"

    monkeypatch.setattr(page, "run_fetch", launcher)
    client = site.client()
    client.get("/")
    page.FETCH.thread.join(10)
    assert runs == [True] and page.FETCH.state == "needs_you"  # stopped: no window for a fetch nobody asked for
    record = page.read_record(site.root / "data" / "last-fetch.json")
    assert record["state"] == "needs_you" and "Duo" in record["error"]

    html = client.get("/now").text
    assert ">update needs you</a>" in html and 'href="/data#get"' in html and runs == [True]  # a recent try is not repeated
    text = plain(client.get("/data").text)
    assert "The data could not be updated by itself at" in text and "Duo wants you to approve this browser again. Press Get new data" in text

    monkeypatch.setattr(page, "FETCH", page.Fetch())  # the app was stopped and opened again: the record still says so
    assert ">update needs you</a>" in site.client().get("/").text and runs == [True]


def test_an_old_try_is_tried_again_and_a_fresh_one_is_not(site, monkeypatch):
    auto_site(site, hours="4")
    runs = []
    monkeypatch.setattr(page, "run_fetch", lambda config_path, on_line, quiet=False: runs.append(quiet) or (0, ""))
    record = site.root / "data" / "last-fetch.json"
    page.write_record(record, {"tried": (datetime.now() - timedelta(hours=3)).isoformat(), "state": "done", "error": None})
    site.client().get("/")
    assert page.FETCH.state == "idle" and runs == []
    page.write_record(record, {"tried": (datetime.now() - timedelta(hours=5)).isoformat(), "state": "done", "error": None})
    site.client().get("/")
    page.FETCH.thread.join(10)
    assert runs == [True] and page.read_record(record)["tried"] > datetime.now() - timedelta(minutes=1)


def test_a_running_fetch_shows_in_the_top_bar_of_every_page(site, monkeypatch):
    auto_site(site)
    go = threading.Event()
    monkeypatch.setattr(page, "run_fetch", lambda config_path, on_line, quiet=False: (go.wait(10), (0, ""))[1])
    client = site.client()
    html = client.get("/forecast").text
    assert ">updating</a>" in html and "data-fetch" in html
    go.set()
    page.FETCH.thread.join(10)
    assert ">updating</a>" not in client.get("/forecast").text


def test_pressing_the_button_during_an_automatic_fetch_lets_it_open_a_window(site, monkeypatch):
    auto_site(site)
    runs, go = [], threading.Event()

    def launcher(config_path, on_line, quiet=False):
        runs.append(quiet)
        if quiet:
            assert go.wait(10)
            return NEEDS_YOU, "Needs you: Duo wants you to approve this browser again.\n"
        return 0, ""

    monkeypatch.setattr(page, "run_fetch", launcher)
    client = site.client()
    client.get("/")  # starts the automatic one
    client.post("/data/fetch")  # pressed while it runs: not refused
    assert "already running" not in client.get("/data").text
    go.set()
    page.FETCH.thread.join(10)
    assert runs == [True, False] and page.FETCH.state == "done"


def test_a_record_that_cannot_be_saved_does_not_fetch_on_every_page(site, monkeypatch):
    auto_site(site)
    runs = []
    monkeypatch.setattr(page, "run_fetch", lambda config_path, on_line, quiet=False: runs.append(quiet) or (1, "Fetch failed: nope\n"))
    monkeypatch.setattr(page, "write_record", lambda path, record: None)  # the disk said no
    client = site.client()
    client.get("/")
    page.FETCH.thread.join(10)
    client.get("/")
    client.get("/now")
    assert runs == [True]


def test_after_a_restart_the_record_says_what_came_down_and_when(site):
    with_export(site)
    yesterday = datetime.now() - timedelta(days=1)
    page.write_record(site.root / "data" / "last-fetch.json", {
        "tried": yesterday.isoformat(timespec="seconds"), "state": "done", "error": None,
        "lines": ["Block 160 Meals: Sep 11 to Sep 14", "Dining Dollars: nothing in this range"],
    })  # fmt: skip
    text = plain(site.client().get("/data").text)
    assert f"The fetch finished at {yesterday:%-I:%M %p} on {yesterday:%b} {yesterday.day}." in text
    assert "Downloaded Block 160 Meals: Sep 11 to Sep 14." in text and "nothing new to download" not in text
    assert not list((site.root / "data").glob("*.part"))


def test_without_auto_hours_nothing_starts_by_itself(site):
    with_export(site)
    site.client().get("/")  # the fixture's launcher fails the test if it is reached
    assert page.FETCH.state == "idle"


@pytest.mark.parametrize(
    ("days", "said"),
    [
        (10, None),
        (3, "Duo stops trusting the fetcher's browser on Sep 17."),
        (1, "Duo stops trusting the fetcher's browser tomorrow."),
        (0, "Duo stops trusting the fetcher's browser today."),
        (-2, "Duo no longer trusts the fetcher's browser."),
    ],
)
def test_duo_running_out_is_said_ahead_of_time(site, monkeypatch, days, said):
    with_export(site)
    until = datetime.combine(AS_OF + timedelta(days=days), datetime.min.time()) + timedelta(hours=23)
    monkeypatch.setattr(page, "duo_trusted_until", lambda profile: until)
    text = plain(site.client().get("/data").text)
    if said is None:
        assert "trust" not in text
    else:
        assert said in text


def test_reread_clears_the_calendar_cache(site):
    client = site.client()
    cache = client.app.state.calendar_cache
    client.get("/data")
    assert cache._kept
    r = client.post("/data/reread?away=0")
    assert r.status_code == 303 and r.headers["location"] == "/data?away=0#get" and not cache._kept
    assert "Read the files in raw-data/ and your calendars again. Nothing was downloaded." in client.get("/data").text


def test_pasted_url_builds_statement_links_that_are_not_saved(site):
    with_export(site)
    client = site.client()
    html = client.get("/data").text
    assert 'action="/data/links"' in html and 'name="pasted"' in html and 'method="post"' in html
    r = client.post("/data/links", data={"pasted": "https://dining.example.test/home.php?cid=1&skey=abc123"})
    assert r.status_code == 303 and r.headers["location"] == "/data#by-hand"
    html = client.get("/data").text
    links = [h for h in hrefs(html) if h.startswith("https://dining.example.test/statementdetail.php")]
    assert len(links) == 2 and "skey=abc123" in links[0] and "startdate=2026-09-11" in links[0] and f"enddate={AS_OF}" in links[0] and "acct=11" in links[0]
    assert "startdate=2026-09-12" in links[1] and "acct=22" in links[1]
    assert "Sep 11 to Sep 14" in html and "Export each as CSV" in html and re.search(r'<details class="more" id="by-hand" open>', html)
    assert "abc123" in client.get("/data").text  # still there on a reload, from memory
    assert not any("abc123" in p.read_text(errors="ignore") for p in site.root.rglob("*") if p.is_file())
    client.post("/data/links", data={"pasted": ""})  # an empty box forgets them
    assert "abc123" not in client.get("/data").text


def test_pasted_url_that_will_not_do_says_why(site):
    with_export(site)
    client = site.client()
    client.post("/data/links", data={"pasted": "https://dining.example.test/home.php?cid=1"})
    text = plain(client.get("/data").text)
    assert "That URL is missing skey. Copy one from a page you reach after logging in." in text
    client.post("/data/links", data={"pasted": "not a url"})
    assert "That doesn't look like a URL from the dining site." in plain(client.get("/data").text)


def test_links_are_forgotten_after_a_while(site, monkeypatch):
    with_export(site)
    client = site.client()
    client.post("/data/links", data={"pasted": "https://dining.example.test/home.php?cid=1&skey=abc123"})
    page.LINKS["at"] -= page.LINKS_KEPT_FOR + 1
    assert "abc123" not in client.get("/data").text and not page.LINKS


def test_without_export_accounts_download_by_hand_says_where_files_go(client):
    html = client.get("/data").text
    assert "Download by hand" in html and 'action="/data/links"' not in html and "list your accounts in the [export] section" in html


# --- calendar -----------------------------------------------------------------------------------


def test_calendar_with_nothing_read(site):
    text = plain(site.client().get("/data").text)
    assert f"No calendars read. Drop .ics files into {site.root / 'calendar'}" in text
    assert "Events named like a break or trip become away periods" in text


ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:1@test
DTSTAMP:20260801T000000Z
DTSTART;VALUE=DATE:20261010
DTEND;VALUE=DATE:20261014
SUMMARY:Fall break
END:VEVENT
BEGIN:VEVENT
UID:2@test
DTSTAMP:20260801T000000Z
DTSTART;VALUE=DATE:20261126
DTEND;VALUE=DATE:20261128
SUMMARY:Thanksgiving recess
END:VEVENT
BEGIN:VEVENT
UID:3@test
DTSTAMP:20260801T000000Z
DTSTART;VALUE=DATE:20261210
DTEND;VALUE=DATE:20261211
SUMMARY:Reading day
END:VEVENT
END:VCALENDAR
"""


def test_calendar_events_are_listed_and_get_a_switch(site):
    (site.root / "calendar").mkdir()
    (site.root / "calendar" / "school.ics").write_text(ICS)
    client = site.client()
    html = client.get("/data").text
    text = plain(html)
    assert "Read: school.ics." in text
    assert "Away period Fall break Oct 10 Oct 13 Added. Switch it off under settings." in text
    assert "Away period Thanksgiving recess Nov 26 Nov 27 Already covered by a configured period." in text
    assert "Chart marker Reading day Dec 10 Dec 10 Shown on balance charts." in text
    key = "cal_2026-10-10_2026-10-13"
    assert f'name="away_enabled_{key}" value="1" checked' in html and "From your calendar." in text
    # switch it off and move it: saved under the calendar's own dates, and still found on the next view
    client.post("/data/settings", data=settings_form(site, **{f"away_enabled_{key}": None, f"away_end_{key}": "2026-10-12"}))
    saved = json.loads((site.root / "data" / "settings.json").read_text())
    assert saved == {"away": {key: {"enabled": False, "start": "2026-10-10", "end": "2026-10-12"}}}
    html = client.get("/data").text
    assert f'name="away_end_{key}" value="2026-10-12"' in html and f'name="away_enabled_{key}" value="1" checked' not in html
    assert "Was on, Oct 10 to Oct 13." in plain(html) and "Added. Switch it off under settings." in plain(html)
    client.post("/data/settings", data=settings_form(site))  # saving the form as shown keeps the edit
    assert json.loads((site.root / "data" / "settings.json").read_text()) == saved


def test_broken_calendar_file_is_a_warning(site):
    (site.root / "calendar").mkdir()
    (site.root / "calendar" / "bad.ics").write_text("this is not a calendar")
    html = site.client().get("/data").text
    assert "Calendar: " in plain(html) and "bad.ics" in html
