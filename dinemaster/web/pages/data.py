"""The `data` section: get new data, check that it is healthy, change the settings, see the calendar.

GET  /data             the page (`build`)
POST /data/fetch       start the one-click fetch in the background (a browser window opens for the login)
POST /data/reread      read the export files and calendars again
POST /data/links       build statement links from a pasted dining-site URL (kept in memory for a while, never saved)
POST /data/settings    save the settings form to data/settings.json
POST /data/reset       delete data/settings.json, back to config.toml
GET  /data/fetch-status   (in web/app.py) `FETCH.status()` as JSON, for the page to poll

The fetch takes minutes and waits for a person, so it runs on its own thread: `FETCH.start` returns at
once and the page shows `FETCH.status()`. Tests replace `run_fetch`; nothing here opens a browser in a test.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from ...config import ROOT
from ...export_links import build_links, latest_by_account
from ...freshness import STALE_AFTER_DAYS
from .. import fmt, settings
from ..charts import data as charts
from ..charts import kit
from ..context import PageContext

FETCH_TIMEOUT = 420  # seconds; the login alone may take five minutes
LINKS_KEPT_FOR = 15 * 60  # seconds the pasted-URL links stay in memory
POT_NAMES = {"ME": "Meal exchanges", "DD": "Dining dollars"}


# --- the fetch job ------------------------------------------------------------------------------


def run_fetch(config_path: Path, on_line: Callable[[str], None]) -> tuple[int, str]:
    """Run `python -m dinemaster.fetch` to the end. Calls `on_line` for each line it prints.

    Returns (exit code, what it wrote to stderr). Raises TimeoutError after `FETCH_TIMEOUT` seconds.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "dinemaster.fetch"],
        cwd=ROOT,
        env={**os.environ, "DINEMASTER_CONFIG": str(config_path)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    late = threading.Event()

    def stop() -> None:
        late.set()
        proc.kill()

    errors: list[str] = []
    drain = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    drain.start()
    timer = threading.Timer(FETCH_TIMEOUT, stop)
    timer.start()
    try:
        for line in proc.stdout:
            if line.strip():
                on_line(line.strip())
        code = proc.wait()
        drain.join(timeout=5)
    finally:
        timer.cancel()
    if late.is_set():
        raise TimeoutError
    return code, "".join(errors)


class Fetch:
    """The one fetch this process may run at a time, and how the last one went."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = "idle"  # idle | running | done | failed
        self.lines: list[str] = []
        self.error: str | None = None
        self.finished: datetime | None = None
        self.thread: threading.Thread | None = None

    def start(self, config_path: Path) -> bool:
        """Start a fetch on a background thread. False when one is already running."""
        with self._lock:
            if self.state == "running":
                return False
            self.state, self.lines, self.error, self.finished = "running", [], None, None
            self.thread = threading.Thread(target=self._work, args=(config_path,), daemon=True)
        self.thread.start()
        return True

    def _add(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)

    def _work(self, config_path: Path) -> None:
        error = None
        try:
            code, stderr = run_fetch(config_path, self._add)
        except TimeoutError:
            error = "Fetch failed: it took too long."
        except Exception as err:  # the launcher itself broke; say so on the page rather than die quietly
            error = f"Fetch failed: {err}"
        else:
            if code:
                error = stderr.strip().splitlines()[-1] if stderr.strip() else "Fetch failed."
        with self._lock:
            self.error = error
            self.state = "failed" if error else "done"
            self.finished = datetime.now()

    def status(self) -> dict:
        """What the page shows: {"state", "lines", "error", "finished"}. Lines are ready to read."""
        with self._lock:
            lines = [line if "nothing in this range" in line else f"Downloaded {line}" for line in self.lines]
            finished = f"{self.finished:%-I:%M %p}" if self.finished else None
            return {"state": self.state, "lines": lines, "error": self.error, "finished": finished}


FETCH = Fetch()
#: One-shot messages from a POST to the page that follows it: {"get": [note], "by_hand": [note], "settings": [note], "form": {...}}.
FLASH: dict = {}
#: Statement links built from a pasted URL: {"at": monotonic seconds, "links": [ExportLink]}. Memory only.
LINKS: dict = {}


def _flash(where: str, kind, text: str, **more) -> None:
    """Leave a note for the next page view. `where` is the part of the page it belongs to: get | by_hand | settings."""
    FLASH.setdefault(where, []).append({"kind": kind, "text": text})
    FLASH.update(more)


def _plain(text: str) -> str:
    """A message from the math modules, without the long dashes this app does not use."""
    return str(text).replace(" — ", ", ").replace("—", ", ").replace("≈", "about ")


def _can_fetch(ctx: PageContext) -> bool:
    return bool(ctx.cfg.export.login_url and ctx.cfg.export.accounts)


# --- settings -----------------------------------------------------------------------------------


def away_rows(ctx: PageContext) -> list[dict]:
    """One row per away period: its saved key, what it holds now, and what it was before any saved edit."""
    saved = settings.clean({"away": ctx.saved.get("away")})[0].get("away", {})
    from_file = ctx.file_cfg.away_periods
    rows = []
    for i, period in enumerate(ctx.base_cfg.away_periods):
        key = settings.away_key(i, period)
        original = (period.enabled, period.start, period.end)
        if period.source == "calendar":
            # an edited calendar period is still saved under the dates the calendar gave it
            for k, entry in saved.items():
                if k.startswith("cal_") and entry["start"] == period.start.isoformat() and entry["end"] == period.end.isoformat():
                    key = k
                    break
            try:
                _, start, end = key.split("_")
                original = (True, date.fromisoformat(start), date.fromisoformat(end))
            except ValueError:
                pass
        elif i < len(from_file) and from_file[i].source != "calendar":
            original = (from_file[i].enabled, from_file[i].start, from_file[i].end)
        rows.append({"key": key, "period": period, "original": original})
    return rows


def _shown(value) -> str:
    """A config value as a form field shows it."""
    if isinstance(value, bool):
        return "1" if value else ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return f"{float(value):g}"
    return str(value)


def _said(value) -> str:
    """A config value in a sentence."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, date):
        return f"{fmt.day(value)}, {value.year}"
    if isinstance(value, (int, float)):
        return f"{float(value):g}"
    return str(value)


def save_settings(ctx: PageContext, form: dict) -> list[str]:
    """Check the posted settings form and save what differs from config.toml. Returns the problems;
    when there are any, nothing is saved."""
    raw = dict(form)
    for name, kind in settings.FIELDS.items():
        if kind is bool:
            raw[name] = name in form  # a switch that is off is simply not posted
    values, problems = settings.clean(raw)
    problems = [p[0].upper() + p[1:] for p in problems]

    def in_force(name: str) -> date:
        return date.fromisoformat(values[name]) if name in values else getattr(ctx.file_cfg, name)

    if in_force("semester_end") < in_force("semester_start"):
        problems.append("The semester must end on or after the day it starts.")

    away = {}
    for row in away_rows(ctx):
        key, period = row["key"], row["period"]
        try:
            start = date.fromisoformat(form.get(f"away_start_{key}", ""))
            end = date.fromisoformat(form.get(f"away_end_{key}", ""))
        except ValueError:
            problems.append(f"{period.name} has a date that is not valid.")
            continue
        if end < start:
            problems.append(f"{period.name} ends before it starts.")
            continue
        enabled = f"away_enabled_{key}" in form
        if (enabled, start, end) != row["original"]:
            away[key] = {"enabled": enabled, "start": start.isoformat(), "end": end.isoformat()}
    if problems:
        return problems

    keep = {name: v for name, v in values.items() if _shown(v) != _shown(getattr(ctx.file_cfg, name))}
    if away:
        keep["away"] = away
    if keep:
        settings.save(ctx.settings_path, keep)
    else:
        settings.reset(ctx.settings_path)
    return []


def post(ctx: PageContext, request, action: str, form: dict) -> str:
    if action == "fetch":
        if not _can_fetch(ctx):
            _flash("get", kit.PROBLEM, "Set login_url and at least one account in the [export] section of config.toml first.")
        elif not FETCH.start(ctx.config_path):
            _flash("get", kit.LOOK, "A fetch is already running. Finish the login in the browser window that is open.")
        return ctx.url("/data#get")
    if action == "reread":
        if ctx.calendar_cache is not None:
            ctx.calendar_cache.clear()
        _flash("get", kit.GOOD, f"Read the files in {ctx.cfg.raw_dir.name}/ and your calendars again. Nothing was downloaded.")
        return ctx.url("/data#get")
    if action == "links":
        LINKS.clear()
        pasted = str(form.get("pasted", "")).strip()
        if pasted:
            try:
                links = build_links(pasted, ctx.cfg, latest_by_account(ctx.df), ctx.today)
            except ValueError as err:
                first, _, rest = str(err).partition(" — ")
                _flash("by_hand", kit.PROBLEM, first + (". " + rest[0].upper() + rest[1:] if rest else ""))
            else:
                LINKS.update(at=time.monotonic(), links=links)
        return ctx.url("/data#by-hand")
    if action == "settings":
        problems = save_settings(ctx, form)
        if problems:
            for p in problems:
                _flash("settings", kit.PROBLEM, p)
            _flash("settings", kit.PROBLEM, "Nothing was saved. Fix the values below and save again.", form=dict(form))
        else:
            _flash("settings", kit.GOOD, "Settings saved.")
        return ctx.url("/data#settings")
    if action == "reset":
        settings.reset(ctx.settings_path)
        _flash("settings", kit.GOOD, "Settings are back to config.toml.")
        return ctx.url("/data#settings")
    return ctx.url("/data")


def _settings_view(ctx: PageContext, posted: dict | None) -> dict:
    """The settings form: every field's value, and for a changed one, what config.toml says."""
    base, file = ctx.base_cfg, ctx.file_cfg
    values, hints = {}, {}
    for name, kind in settings.FIELDS.items():
        now = getattr(base, name)
        if posted is not None:
            values[name] = (name in posted) if kind is bool else posted.get(name, "")
        else:
            values[name] = now if kind is bool else _shown(now)
        if _shown(now) != _shown(getattr(file, name)):
            hints[name] = f"config.toml says {_said(getattr(file, name))}."
    away = []
    for row in away_rows(ctx):
        key, p = row["key"], row["period"]
        was_on, was_start, was_end = row["original"]
        changed = (p.enabled, p.start, p.end) != row["original"]
        away.append({
            "key": key,
            "name": p.name,
            "from_calendar": p.source == "calendar",
            "enabled": (f"away_enabled_{key}" in posted) if posted is not None else p.enabled,
            "start": posted.get(f"away_start_{key}", "") if posted is not None else p.start.isoformat(),
            "end": posted.get(f"away_end_{key}", "") if posted is not None else p.end.isoformat(),
            "hint": f"Was {'on' if was_on else 'off'}, {fmt.day(was_start)} to {fmt.day(was_end)}." if changed else None,
        })  # fmt: skip
    changed = len(hints) + sum(1 for a in away if a["hint"])
    return {"v": values, "hints": hints, "away": away, "changed": changed, "modes": settings.ROUNDING_MODES, "file": ctx.settings_path}


# --- the page -----------------------------------------------------------------------------------


def _headline(ctx: PageContext) -> tuple[str, str | None]:
    report = ctx.report
    if not ctx.has_data or ctx.through is None:
        how = "Press Get new data and log in to the dining site. Or put" if _can_fetch(ctx) else "Put"
        return "No dining data yet.", f"{how} your statement exports (one CSV file per account) into {ctx.cfg.raw_dir} and press Re-read files."
    age = (ctx.today - ctx.through).days
    sub = f"{fmt.plural(report.total_rows, 'transaction')} from {fmt.plural(len(report.files), 'file')} are in the ledger."
    if age > STALE_AFTER_DAYS:
        return f"Your data is {age} days old. It ends {fmt.day(ctx.through)}.", sub
    return f"Your data runs through {fmt.day(ctx.through)}, {fmt.ago(age)}.", sub


def _band(ctx: PageContext, jumps: list[dict]) -> list[dict]:
    report, bal = ctx.report, ctx.metrics.balances
    age = (ctx.today - ctx.through).days
    recent = [b for b in jumps if b["timestamp"].date() >= ctx.cfg.data_cutoff]
    off = [w for w in (bal.me_warning, bal.dd_warning) if w]
    return [
        {
            "value": str(age),
            "label": "day behind" if age == 1 else "days behind",
            "detail": f"The exports cover every pot through {fmt.day(ctx.through)}.",
            "kind": kit.PROBLEM if age > STALE_AFTER_DAYS else kit.DAYS,
        },
        {
            "value": str(len(report.warnings)),
            "label": "problem reading the files" if len(report.warnings) == 1 else "problems reading the files",
            "detail": "Every file was read cleanly." if not report.warnings else "They are listed below.",
            "kind": kit.LOOK if report.warnings else kit.GOOD,
        },
        {
            "value": str(len(recent)),
            "label": "unexplained balance change" if len(recent) == 1 else "unexplained balance changes",
            "detail": "This semester." + (f" {len(jumps)} in all the files." if len(jumps) != len(recent) else ""),
            "kind": kit.LOOK if recent else kit.GOOD,
        },
        {
            "value": f"{2 - len(off)} of 2",
            "label": "pots add up",
            "detail": "The ledger agrees with your plan settings." if not off else "See the check below.",
            "kind": kit.LOOK if off else kit.GOOD,
        },
    ]


def _notes(ctx: PageContext) -> list[dict]:
    out: list[dict] = []
    if ctx.has_data and ctx.params.as_of is not None and ctx.params.as_of != ctx.today:
        out.append({
            "kind": kit.DAYS,
            "text": f"You are viewing {fmt.day(ctx.cfg.as_of)}. The checks below describe that day. How fresh the data is always counts from today.",
            "href": ctx.url("/data", as_of=None), "link": "Back to today",
        })  # fmt: skip
    return out


def _accounts(ctx: PageContext) -> dict | None:
    latest = latest_by_account(ctx.df)
    if not latest:
        return None
    rows = [[name, f"{fmt.day(d)}, {d.year}", fmt.ago((ctx.today - d).days)] for name, d in sorted(latest.items())]
    return {"columns": [("Account", False), ("Last transaction", False), ("How long ago", False)], "rows": rows}


def _by_hand(ctx: PageContext) -> dict:
    links = []
    if LINKS and time.monotonic() - LINKS["at"] <= LINKS_KEPT_FOR:
        links = [{"name": k.name, "url": k.url, "range": f"{fmt.day(k.start)} to {fmt.day(k.end)}"} for k in LINKS["links"]]
    elif LINKS:
        LINKS.clear()
    return {"offered": bool(ctx.cfg.export.accounts), "links": links}


def _health(ctx: PageContext) -> dict:
    report = ctx.report
    pots = {"ME": "meal exchanges", "DD": "dining dollars"}
    files = {
        "columns": [("File", False), ("Account", False), ("Pot", False), ("Rows", True), ("New", True), ("Duplicate", True)],
        "rows": [
            [f["file"], f["account"] or "not recognised", pots.get(f["pot"], "none"), str(f["rows"]), str(f["new"]), str(f["duplicate"])]
            for f in report.files
        ],
    }
    out: dict = {
        "files": files if report.files else None,
        "file_count": len(report.files),
        "new_rows": sum(f["new"] for f in report.files),
        "warnings": [{"kind": kit.LOOK, "text": _plain(w)} for w in report.warnings],
        "ledger": report.ledger_path or ctx.cfg.ledger_path,
        "jumps": None,
        "late": None,
        "pots": None,
        "split": None,
    }
    if report.chain_breaks:
        out["jumps"] = {
            "columns": [("When", False), ("Account", False), ("At this transaction", False), ("Balance should be", True), ("Site says", True),
                        ("Unexplained", True)],
            "rows": [
                [f"{fmt.day(b['timestamp'])}, {b['timestamp'].year}", b["account"], b["description"], f"{b['expected']:,.2f}", f"{b['actual']:,.2f}",
                 f"{b['actual'] - b['expected']:+,.2f}"]
                for b in report.chain_breaks
            ],
        }  # fmt: skip
    if report.late_posted:
        out["late"] = {
            "columns": [("Dated", False), ("Account", False), ("Transaction", False), ("Amount", True), ("Charged after", False), ("And before", False)],
            "rows": [
                [f"{fmt.day(p['timestamp'])}, {p['timestamp'].year}", p["account"], p["description"], f"{p['amount']:,.2f}",
                 f"{fmt.day(p['after'])}, {p['after'].year}", f"{fmt.day(p['before'])}, {p['before'].year}" if p["before"] is not None else "not known"]
                for p in report.late_posted
            ],
        }  # fmt: skip
    if ctx.has_data:
        bal, bd = ctx.metrics.balances, ctx.metrics.dd_breakdown
        whole, money = (lambda x: fmt.num(x, 0)), fmt.cents

        def row(name, show, left, derived, deposits, warning):
            return [name, show(left), show(derived) if derived is not None else "no rows", show(deposits), _plain(warning) if warning else "Adds up."]

        out["pots"] = {
            "columns": [("Pot", False), ("Left, worked out", True), ("Left, by the site's balances", True), ("Deposits in the window", True),
                        ("Check", False)],
            "rows": [
                row("Meal exchanges", whole, bal.me_left, bal.me_derived_balance, bal.me_deposits, bal.me_warning),
                row("Dining dollars", money, bal.dd_left, bal.dd_derived_balance, bal.dd_deposits, bal.dd_warning),
            ],
        }  # fmt: skip
        out["split"] = {
            "sentence": (
                f"{fmt.cents(bd.meal_amount)} went on {fmt.plural(bd.meal_count, 'meal')} and "
                f"{fmt.cents(bd.snack_amount)} on {fmt.plural(bd.snack_count, 'snack')}."
                if bd.meal_count or bd.snack_count else "No dining-dollar purchases yet."
            ),
            "chart": charts.dd_split_chart(bd.by_location),
            "classes": {
                "columns": [("Counted as", False), ("Purchases", True), ("Amount", True)],
                "rows": [["A meal", str(bd.meal_count), fmt.cents(bd.meal_amount)], ["A snack", str(bd.snack_count), fmt.cents(bd.snack_amount)]],
            },
            "places": {
                "columns": [("Place", False), ("Meals", True), ("Meal amount", True), ("Snacks", True), ("Snack amount", True)],
                "rows": [
                    [str(place), str(v.get("meal_count", 0)), fmt.cents(v.get("meal_amount", 0.0) + 0.0), str(v.get("snack_count", 0)),
                     fmt.cents(v.get("snack_amount", 0.0) + 0.0)]  # + 0.0 turns a negative zero into a plain one
                    for place, v in sorted(bd.by_location.items(), key=lambda kv: -(kv[1].get("meal_amount", 0.0) + kv[1].get("snack_amount", 0.0)))
                ],
            } if bd.by_location else None,
        }  # fmt: skip
    return out


def _calendar(ctx: PageContext) -> dict:
    cfg, cal = ctx.cfg, ctx.calendar
    merged = {(p.start, p.end) for c in (ctx.file_cfg, ctx.base_cfg) for p in c.away_periods if p.source == "calendar"}
    merged |= {row["original"][1:] for row in away_rows(ctx) if row["period"].source == "calendar"}
    rows = [
        ["Away period", p.name, fmt.day(p.start), fmt.day(p.end),
         "Added. Switch it off under settings." if (p.start, p.end) in merged else "Already covered by a configured period."]
        for p in cal.away
    ] + [["Chart marker", mk.name, fmt.day(mk.start), fmt.day(mk.end), "Shown on balance charts."] for mk in cal.markers]  # fmt: skip
    return {
        "warnings": [{"kind": kit.LOOK, "text": f"Calendar: {_plain(w)}"} for w in cal.warnings],
        "sources": list(cal.sources),
        "folder": cfg.calendar.ics_dir,
        "found": {"columns": [("What", False), ("Name", False), ("Start", False), ("End", False), ("Status", False)], "rows": rows} if rows else None,
    }


def _view(ctx: PageContext) -> dict:
    """The URL-only view controls: the as-of day and whether days away are left out."""
    base = ctx.base_cfg.exclude_away

    def away_link(want: bool) -> str:
        return ctx.url("/data#view", away=None if want == base else ("1" if want else "0"))

    return {
        "as_of": ctx.cfg.as_of.isoformat(),
        "today": ctx.today.isoformat(),
        "away_param": ctx.params.query().get("away"),
        "away": [
            {"label": "Left out", "href": away_link(True), "on": ctx.cfg.exclude_away},
            {"label": "Counted", "href": away_link(False), "on": not ctx.cfg.exclude_away},
        ],
        "setting": "left out" if base else "counted",
        "changed": ctx.view_changed,
        "reset": ctx.url("/data#view", as_of=None, away=None),
    }


def _fetch_notes(fetch: dict) -> list[dict]:
    """How the last fetch ended, as notes."""
    when = f" at {fetch['finished']}" if fetch["finished"] else ""
    if fetch["state"] == "failed":
        return [{"kind": kit.PROBLEM, "text": f"{_plain(fetch['error'])} Nothing else was downloaded. You can try again."}]
    if fetch["state"] == "done":
        if not fetch["lines"]:
            return [{"kind": kit.GOOD, "text": f"The fetch finished{when}. There was nothing new to download."}]
        return [{"kind": kit.GOOD, "text": f"The fetch finished{when}."}] + [{"kind": kit.GOOD, "text": f"{line}."} for line in fetch["lines"]]
    return []


def build(ctx: PageContext, request) -> dict:
    flash = dict(FLASH)
    FLASH.clear()
    headline, sub = _headline(ctx)
    fetch = FETCH.status()
    return {
        "empty": not ctx.has_data,
        "headline": headline,
        "sub": sub,
        "notes": _notes(ctx),
        "said": {where: flash.get(where, []) for where in ("get", "by_hand", "settings")},
        "band": _band(ctx, ctx.report.chain_breaks) if ctx.has_data and ctx.through is not None else None,
        "can_fetch": _can_fetch(ctx),
        "fetch": fetch,
        "running": fetch["state"] == "running",
        "fetch_notes": _fetch_notes(fetch),
        "accounts": _accounts(ctx),
        "by_hand": _by_hand(ctx),
        "health": _health(ctx),
        "settings": _settings_view(ctx, flash.get("form")),
        "view": _view(ctx),
        "calendar": _calendar(ctx),
    }
