"""The fetcher's login steps, without a browser: a fake page that walks through URLs, a made-up
Preferences file and a made-up Chrome cookie database."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from dinemaster import fetch
from dinemaster.config import ExportAccount, ExportConfig, load_config

HOME = "https://dining.example.edu/login.php?cid=9"
IN = "https://dining.example.edu/index.php?cid=9&skey=abc"
NETBADGE = "https://idp.example.edu/idp/profile/SAML2/Redirect/SSO?execution=e1s2"
DUO = "https://api-1234.duosecurity.com/frame/v4/auth/prompt"


@pytest.fixture
def cfg():
    export = ExportConfig(login_url=HOME, accounts=(ExportAccount("Meals", "22"),), cert_button="certificate")
    return replace(load_config(), export=export)


def test_where_names_the_step_of_the_login(cfg):
    assert fetch.where(IN, cfg) == "in"
    assert fetch.where(DUO, cfg) == "duo"
    assert fetch.where("https://duosecurity.com/x", cfg) == "duo"
    assert fetch.where("https://notduosecurity.com/x", cfg) == "login"
    assert fetch.where(NETBADGE, cfg) == "login" and fetch.where(HOME, cfg) == "login"


class Button:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def or_(self, other):
        return self

    def is_visible(self):
        return self.page.url == NETBADGE and self.page.button_shown

    def click(self, timeout=None):
        self.page.pressed += 1
        self.page.url = self.page.after_press


class Page:
    """Starts on `url`; pressing the certificate button goes to `after_press`; every wait moves along `then`."""

    def __init__(self, url, after_press=IN, then=(), button_shown=True):
        self.url, self.after_press, self.then, self.button_shown = url, after_press, list(then), button_shown
        self.pressed, self.waited, self.roles = 0, 0, []

    def get_by_role(self, role, name):
        self.roles.append((role, name.pattern))
        return Button(self)

    def wait_for_timeout(self, ms):
        self.waited += ms
        if self.then:
            self.url = self.then.pop(0)


def test_log_in_presses_the_certificate_button(cfg):
    page = Page(NETBADGE)
    fetch.log_in(page, cfg, quiet=True)
    assert page.pressed == 1 and page.url == IN and ("button", "certificate") in page.roles


def test_log_in_with_no_button_configured_just_waits(cfg):
    cfg = replace(cfg, export=replace(cfg.export, cert_button=""))
    page = Page(NETBADGE, then=[NETBADGE, IN])
    fetch.log_in(page, cfg, quiet=False)
    assert page.pressed == 0 and page.roles == [] and page.url == IN


def fake_clock(monkeypatch, step):
    clock = iter(range(0, 100_000, step))
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(clock))


def test_a_quiet_run_stops_when_duo_keeps_the_page(cfg, monkeypatch):
    fake_clock(monkeypatch, 1)
    page = Page(NETBADGE, after_press=DUO)
    with pytest.raises(fetch.NeedsYou, match="Duo"):
        fetch.log_in(page, cfg, quiet=True)


def test_a_trusted_browser_passing_through_duo_goes_on(cfg, monkeypatch):
    fake_clock(monkeypatch, 1)
    page = Page(NETBADGE, after_press=DUO, then=[DUO] * 8 + [IN])  # Duo's page for a few seconds, then sent on
    fetch.log_in(page, cfg, quiet=True)
    assert page.url == IN


def test_a_window_waits_at_duo_for_the_person(cfg):
    page = Page(NETBADGE, after_press=DUO, then=[DUO, DUO, IN])
    fetch.log_in(page, cfg, quiet=False)
    assert page.url == IN


def test_the_button_is_pressed_at_most_twice_and_a_quiet_run_gives_up(cfg, monkeypatch):
    fake_clock(monkeypatch, 5)
    page = Page(NETBADGE, after_press=NETBADGE)  # the certificate was not taken: back on the same page
    with pytest.raises(fetch.NeedsYou, match="did not finish by itself .it stopped on idp.example.edu."):
        fetch.log_in(page, cfg, quiet=True)
    assert page.pressed == 2


def test_a_window_that_is_never_finished_times_out(cfg, monkeypatch):
    fake_clock(monkeypatch, 60)
    with pytest.raises(TimeoutError):
        fetch.log_in(Page(NETBADGE, button_shown=False), cfg, quiet=False)


def test_allow_certificate_writes_only_the_auto_select_setting(tmp_path):
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    prefs.write_text(json.dumps({"browser": {"x": 1}, "profile": {"name": "Person 1"}}))
    assert fetch.allow_certificate(tmp_path, ("[*.]example.edu",))
    saved = json.loads(prefs.read_text())
    assert saved["browser"] == {"x": 1} and saved["profile"]["name"] == "Person 1"
    assert saved["profile"]["content_settings"]["exceptions"]["auto_select_certificate"] == {"[*.]example.edu,*": {"setting": {"filters": [{}]}}}
    assert not fetch.allow_certificate(tmp_path, ("[*.]example.edu",))  # already there: the file is left alone


def test_allow_certificate_starts_a_new_profile_and_leaves_a_broken_file(tmp_path):
    assert not fetch.allow_certificate(tmp_path, ())
    assert fetch.allow_certificate(tmp_path, ("[*.]example.edu",))
    prefs = tmp_path / "Default" / "Preferences"
    prefs.write_text("{not json")
    assert not fetch.allow_certificate(tmp_path, ("[*.]example.edu",)) and prefs.read_text() == "{not json"


def cookie_db(profile, rows):
    db = profile / "Default" / "Cookies"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, expires_utc INTEGER)")
        con.executemany("INSERT INTO cookies VALUES (?, ?, ?)", rows)


def chrome_time(when: datetime) -> int:
    return (when - datetime(1601, 1, 1, tzinfo=timezone.utc)) // timedelta(microseconds=1)


def test_duo_trust_is_read_from_the_cookie_database(tmp_path):
    assert fetch.duo_trusted_until(tmp_path) is None
    later = datetime(2026, 11, 17, 23, 48, tzinfo=timezone.utc)
    cookie_db(tmp_path, [
        ("api-1234.duosecurity.com", "browsertrust|AAA|BBB", chrome_time(later)),
        ("api-1234.duosecurity.com", "browsertrust|AAA|CCC", chrome_time(later - timedelta(days=9))),
        ("api-1234.duosecurity.com", "hac|AAA", chrome_time(later + timedelta(days=300))),
        ("evil.example.com", "browsertrust", chrome_time(later + timedelta(days=300))),
    ])  # fmt: skip
    assert fetch.duo_trusted_until(tmp_path) == later.astimezone().replace(tzinfo=None)


def test_no_duo_trust_cookie_means_none(tmp_path):
    cookie_db(tmp_path, [("idp.example.edu", "session", 0)])
    assert fetch.duo_trusted_until(tmp_path) is None


def test_quiet_flag_and_needs_you_exit_code(monkeypatch, tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text((fetch.ROOT / "config.toml").read_text())
    monkeypatch.setenv("DINEMASTER_CONFIG", str(config))
    calls = []

    def fake(cfg, today, quiet=False):
        calls.append(quiet)
        raise fetch.NeedsYou("Duo wants you to approve this browser again")

    monkeypatch.setattr(fetch, "fetch", fake)
    assert fetch.main(["--quiet"]) == fetch.NEEDS_YOU and calls == [True]
    assert capsys.readouterr().err == "Needs you: Duo wants you to approve this browser again.\n"
    assert fetch.main([]) == fetch.NEEDS_YOU and calls == [True, False]


def test_config_reads_the_new_export_settings():
    cfg = load_config()
    assert cfg.export.cert_button and cfg.export.cert_sites and cfg.export.auto_hours > 0
    plain = ExportConfig()
    assert (plain.cert_button, plain.cert_sites, plain.auto_hours) == ("", (), 0.0)  # off unless the config asks
