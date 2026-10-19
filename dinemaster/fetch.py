"""Download each account's statement CSV from the dining site through Chrome.

The site sits behind single sign-on. The browser keeps its own profile next to the ledger, so a login
it has seen before (a trusted Duo browser, a certificate it may send) carries over to the next run.
On the login page it presses the certificate button by itself when [export] cert_button names one.

`python -m dinemaster.fetch` opens a window and waits for a person if the login needs one.
`python -m dinemaster.fetch --quiet` runs with no window and gives up with exit code 3 (NEEDS_YOU)
as soon as the login wants a person, for instance when Duo asks to approve the browser again. It then
leaves a screenshot of where it stopped in data/last-fetch.png.
The data page runs this as a subprocess; it prints one line per file.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import ROOT, Config, load_config
from .export_links import ExportLink, build_links, latest_by_account
from .ingest import load_transactions

LOGIN_TIMEOUT_MS = 5 * 60 * 1000
QUIET_LOGIN_MS = 45 * 1000  # a login that needs nobody is done in a few seconds
DUO_PASS_MS = 15 * 1000  # a browser Duo trusts still passes through Duo's page; it is sent on within this
NEEDS_YOU = 3  # exit code: a quiet run stopped because the login wants a person
DUO_HOST = "duosecurity.com"
_FILENAME = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)


class NeedsYou(Exception):
    """A quiet run reached a step of the login that a person has to do."""


def profile_dir(cfg: Config) -> Path:
    """The fetcher's own browser profile (not your everyday Chrome's)."""
    return cfg.ledger_path.parent / "browser-profile"


def csv_url(link: ExportLink, cfg: Config) -> str:
    """The statement link with the site's CSV switch added."""
    return f"{link.url}&{cfg.export.csv_param}"


def csv_filename(content_disposition: str | None, link: ExportLink) -> str:
    """The site's own file name (ingest reads the account from it), or the same pattern if it sends none."""
    match = _FILENAME.search(content_disposition or "")
    name = Path(match.group(1)).name if match else f"{link.name}_statement_{link.start}_to_{link.end}.csv"
    return name if name.lower().endswith(".csv") else f"{name}.csv"


def logged_in(url: str, cfg: Config) -> bool:
    """True once the browser is back on the dining site with a session in the URL."""
    parts, home = urlsplit(url), urlsplit(cfg.export.login_url)
    query = parse_qs(parts.query)
    return (
        parts.netloc == home.netloc
        and "login" not in parts.path
        and all(query.get(p) for p in cfg.export.session_params)
    )


def where(url: str, cfg: Config) -> str:
    """Which step of the login a URL is: "in" (the dining site has a session), "duo", or "login" (anything else)."""
    if logged_in(url, cfg):
        return "in"
    host = urlsplit(url).hostname or ""
    return "duo" if host == DUO_HOST or host.endswith("." + DUO_HOST) else "login"


def allow_certificate(profile: Path, sites: tuple[str, ...]) -> bool:
    """Let the profile send a certificate to these sites without asking (Chrome's auto-select setting).

    Writes the setting into the profile's Preferences, so only the fetcher's browser is changed. Needs the
    browser closed. Returns False when there was nothing to do or the file could not be read.
    """
    if not sites:
        return False
    path = profile / "Default" / "Preferences"
    try:
        prefs = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return False  # a file Chrome cannot read either; leave it to Chrome
    exceptions = prefs.setdefault("profile", {}).setdefault("content_settings", {}).setdefault("exceptions", {})
    auto = exceptions.setdefault("auto_select_certificate", {})
    wanted = {f"{site},*": {"setting": {"filters": [{}]}} for site in sites}  # an empty filter matches any certificate
    if all(auto.get(key, {}).get("setting") == value["setting"] for key, value in wanted.items()):
        return False
    auto.update(wanted)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs))
    return True


def duo_trusted_until(profile: Path) -> datetime | None:
    """When Duo stops trusting the fetcher's browser (local time), or None: not trusted, or the browser is open."""
    db = profile / "Default" / "Cookies"
    if not db.exists():
        return None
    try:
        with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=0.2)) as con:
            (expires,) = con.execute(
                "SELECT MAX(expires_utc) FROM cookies WHERE (host_key = ? OR host_key LIKE ?) AND name LIKE 'browsertrust%'",
                (DUO_HOST, f"%.{DUO_HOST}"),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not expires:
        return None
    utc = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=expires)  # Chrome counts from 1601
    return utc.astimezone().replace(tzinfo=None)


def _open(p, profile: Path, quiet: bool):
    """The fetcher's browser: Google Chrome (the profile is Chrome's), else Playwright's own Chromium.

    A quiet run would tell sites it is "HeadlessChrome"; it is opened again saying plain Chrome, as the window does.
    """

    def launch(**kw):
        try:
            return p.chromium.launch_persistent_context(str(profile), headless=quiet, channel="chrome", **kw)
        except Exception:  # Chrome isn't installed
            return p.chromium.launch_persistent_context(str(profile), headless=quiet, **kw)

    ctx = launch()
    if quiet:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        agent = page.evaluate("navigator.userAgent")
        if "HeadlessChrome" in agent:
            ctx.close()
            ctx = launch(user_agent=agent.replace("HeadlessChrome", "Chrome"))
    return ctx


def _press_certificate(page, label: str) -> bool:
    """Press the login page's certificate button (a button or link whose name matches `label`). True if pressed."""
    pattern = re.compile(label, re.IGNORECASE)
    button = page.get_by_role("button", name=pattern).or_(page.get_by_role("link", name=pattern)).first
    try:
        if not button.is_visible():
            return False
        button.click(timeout=2000)
    except Exception:  # the page moved on under us; look again next time round
        return False
    return True


def log_in(page, cfg: Config, quiet: bool) -> None:
    """Wait until the dining site has a session, pressing the certificate button on the way.

    A window waits up to five minutes for a person. A quiet run raises NeedsYou when Duo keeps the page
    for DUO_PASS_MS (it is asking for a person), or when the login has not finished by itself in QUIET_LOGIN_MS.
    """
    give_up = time.monotonic() + (QUIET_LOGIN_MS if quiet else LOGIN_TIMEOUT_MS) / 1000
    pressed, at_duo = 0, None
    while (step := where(page.url, cfg)) != "in":
        at_duo = (at_duo or time.monotonic()) if step == "duo" else None
        if quiet and at_duo is not None and time.monotonic() - at_duo > DUO_PASS_MS / 1000:
            raise NeedsYou("Duo wants you to approve this browser again")
        if time.monotonic() > give_up:
            if quiet:
                raise NeedsYou(f"the login did not finish by itself (it stopped on {urlsplit(page.url).hostname})")
            raise TimeoutError("Timeout")
        if step == "login" and pressed < 2 and cfg.export.cert_button and _press_certificate(page, cfg.export.cert_button):
            pressed += 1
            page.wait_for_timeout(2500)  # give the certificate login time to move the page on
        page.wait_for_timeout(400)


def fetch(cfg: Config, today: date, quiet: bool = False) -> list[Path]:
    """Log in, then save one CSV per configured account into cfg.raw_dir."""
    from playwright.sync_api import sync_playwright

    df, _ = load_transactions(cfg)
    latest = latest_by_account(df)
    profile = profile_dir(cfg)
    profile.mkdir(parents=True, exist_ok=True)
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    allow_certificate(profile, cfg.export.cert_sites)

    saved = []
    with sync_playwright() as p:
        ctx = _open(p, profile, quiet)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(cfg.export.login_url)
            try:
                log_in(page, cfg, quiet)
            except NeedsYou:
                try:  # what the quiet run was looking at; a page mid-login can make this hang, so keep it short
                    page.screenshot(path=profile.parent / "last-fetch.png", timeout=5000)
                except Exception:
                    pass
                raise
            for link in build_links(page.url, cfg, latest, today):
                resp = ctx.request.get(csv_url(link, cfg))
                body = resp.body()
                if not resp.ok or body.lstrip()[:1] == b"<":
                    raise RuntimeError(f"{link.name}: the site did not return a CSV (HTTP {resp.status}).")
                rows = body.decode(errors="replace").strip().splitlines()[1:]
                if not rows or not rows[0][:4].isdigit():  # the site sends a one-line note when a range is empty
                    print(f"{link.name}: nothing in this range", flush=True)
                    continue
                path = cfg.raw_dir / csv_filename(resp.headers.get("content-disposition"), link)
                path.write_bytes(body)
                saved.append(path)
                print(f"{link.name}: {link.start:%b} {link.start.day} to {link.end:%b} {link.end.day}", flush=True)
        finally:
            ctx.close()
    return saved


def main(argv: list[str] | None = None) -> int:
    quiet = "--quiet" in (sys.argv[1:] if argv is None else argv)
    path = Path(os.environ.get("DINEMASTER_CONFIG", ROOT / "config.toml"))
    cfg = load_config(path, root=path.resolve().parent)
    if not cfg.export.login_url or not cfg.export.accounts:
        print("Set login_url and at least one account in the [export] section of the config.", file=sys.stderr)
        return 2
    try:
        fetch(cfg, date.today(), quiet)
    except NeedsYou as err:
        print(f"Needs you: {err}.", file=sys.stderr)
        return NEEDS_YOU
    except Exception as err:
        reason = "the login was not finished in time or the window was closed" if "Timeout" in type(err).__name__ or "closed" in str(err) else str(err)
        print(f"Fetch failed: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
