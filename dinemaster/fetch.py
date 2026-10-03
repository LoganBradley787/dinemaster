"""Download each account's statement CSV from the dining site through a real browser window.

The site sits behind single sign-on, so a person has to log in; everything after that is
automatic. The browser keeps its profile next to the ledger, so later runs often skip the login.
Streamlit runs this as a subprocess (`python -m dinemaster.fetch`); it prints one line per file.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import ROOT, Config, load_config
from .export_links import ExportLink, build_links, latest_by_account
from .ingest import load_transactions

LOGIN_TIMEOUT_MS = 5 * 60 * 1000
_FILENAME = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)


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


def fetch(cfg: Config, today: date) -> list[Path]:
    """Open the login page, wait for the session, then save one CSV per configured account into cfg.raw_dir."""
    from playwright.sync_api import sync_playwright

    df, _ = load_transactions(cfg)
    latest = latest_by_account(df)
    profile = cfg.ledger_path.parent / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(str(profile), headless=False)
        except Exception:  # Playwright's own Chromium isn't installed; use the system Chrome
            ctx = p.chromium.launch_persistent_context(str(profile), headless=False, channel="chrome")
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(cfg.export.login_url)
            page.wait_for_url(lambda url: logged_in(url, cfg), timeout=LOGIN_TIMEOUT_MS)
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


def main() -> int:
    path = Path(os.environ.get("DINEMASTER_CONFIG", ROOT / "config.toml"))
    cfg = load_config(path, root=path.resolve().parent)
    if not cfg.export.login_url or not cfg.export.accounts:
        print("Set login_url and at least one account in the [export] section of the config.", file=sys.stderr)
        return 2
    try:
        fetch(cfg, date.today())
    except Exception as err:
        reason = "the login was not finished in time or the window was closed" if "Timeout" in type(err).__name__ or "closed" in str(err) else str(err)
        print(f"Fetch failed: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
