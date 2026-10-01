"""Calendar awareness: turn .ics events into away periods and chart markers.

Sources are every `*.ics` file in `cfg.calendar.ics_dir` plus each feed URL in `cfg.calendar.ics_urls`
(downloaded at most once per `cache_hours`, kept in `cache_dir`). Only event titles and dates are read.

Rules, for events that overlap the semester `[S, E]`:

- A title matching any `away_patterns` regex becomes an `AwayPeriod(source="calendar")` when the event
  covers at least one full day: any all-day event, or a timed event lasting `MIN_AWAY_HOURS` or more.
  A timed event is away on the days it covers at least half of (leave Friday 5pm, back Sunday 8pm ->
  Saturday and Sunday); if it covers no day that much, on the single day it covers most.
- A title matching any `marker_patterns` regex becomes a `Marker` on every day the event touches.
  An event can be both.
- Dates are inclusive (an all-day event's exclusive ICS end is converted) and clipped to `[S, E]`.
  Times with a timezone are read in this computer's local time.
- Repeating events are expanded with their RRULE and EXDATE; a rule that can't be expanded is skipped
  with a warning. Cancelled events are skipped. Not handled: RDATE, and a single repeat that was moved
  in the calendar app (RECURRENCE-ID) shows up on both its old and new dates.

Nothing here raises for bad input: unreadable files, failed downloads, broken events and bad patterns
all end up as plain-English strings in `CalendarResult.warnings`.
"""

from __future__ import annotations

import hashlib
import os
import re
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from dateutil.rrule import rruleset, rrulestr
from icalendar import Calendar

from .config import AwayPeriod, Config, Marker

MIN_AWAY_HOURS = 20  # a timed event must last at least this long to count as being away
HALF_DAY = timedelta(hours=12)  # ...and is away on each day it covers at least this much of
FETCH_TIMEOUT_SECONDS = 10
MAX_FEED_BYTES = 5_000_000

ONE_DAY = timedelta(days=1)

Fetcher = Callable[[str], bytes]


@dataclass
class CalendarResult:
    """What the calendars offered.

    away:     away periods found (enabled, `source="calendar"`), oldest first, clipped to the semester,
              one per distinct date range. Not filtered against `cfg.away_periods` — `apply_calendar`
              does that when it merges.
    markers:  chart markers found, oldest first, clipped to the semester.
    warnings: plain-English problems to show the user (never contain a feed URL, only its host).
    sources:  what was read successfully: file names, and host names for feeds.
    """

    away: list[AwayPeriod] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


def load_calendar(cfg: Config, now: datetime | None = None, fetch: Fetcher | None = None) -> CalendarResult:
    """Read every configured calendar and return its away periods and markers. Never raises.

    `now` (default: the current time) only decides whether a cached feed is still fresh.
    `fetch(url) -> bytes` replaces the real download; tests pass a fake. With no `ics_dir` folder and
    no `ics_urls`, the result is empty with no warnings.
    """
    result = CalendarResult()
    try:
        _load(cfg, now or datetime.now(), fetch or _fetch_url, result)
    except Exception as exc:  # last resort: the calendar is optional, the dashboard must still open
        result.warnings.append(f"The calendar couldn't be fully read ({type(exc).__name__}).")
    result.away = _unique(result.away, key=lambda p: (p.start, p.end))
    result.markers = _unique(result.markers, key=lambda m: (m.start, m.end, m.name.lower()))
    return result


def _load(cfg: Config, now: datetime, fetch: Fetcher, result: CalendarResult) -> None:
    """Fill `result` from every source; `load_calendar` sorts and de-duplicates afterwards."""
    away_res = _compile(cfg.calendar.away_patterns, result.warnings)
    marker_res = _compile(cfg.calendar.marker_patterns, result.warnings)

    for label, calendar in _calendars(cfg, now, fetch, result.warnings):
        unreadable = 0
        for event in calendar.walk("VEVENT"):
            title = str(event.get("SUMMARY") or "").strip()
            is_away = any(r.search(title) for r in away_res)
            is_marker = any(r.search(title) for r in marker_res)
            if not (is_away or is_marker) or str(event.get("STATUS") or "").upper() == "CANCELLED":
                continue
            try:
                occurrences = _occurrences(event, cfg.semester_start, cfg.semester_end)
            except (ValueError, TypeError, OverflowError):  # broken dates, or a repeat rule dateutil refuses
                unreadable += 1
                continue
            for start, end in occurrences:
                if is_away and (days := _clip(_away_days(start, end), cfg)):
                    result.away.append(AwayPeriod(title, days[0], days[1], enabled=True, source="calendar"))
                if is_marker and (days := _clip(_touched_days(start, end), cfg)):
                    result.markers.append(Marker(title, days[0], days[1]))
        if unreadable:
            result.warnings.append(
                f"{label}: skipped {unreadable} event(s) whose dates or repeat rule couldn't be read."
            )
        result.sources.append(label)


def apply_calendar(
    cfg: Config, now: datetime | None = None, fetch: Fetcher | None = None
) -> tuple[Config, CalendarResult]:
    """Return a copy of `cfg` with the calendar merged in, plus the raw `CalendarResult`. Never raises.

    `away_periods` keeps every existing period, in order, followed by the calendar's periods. A calendar
    period is left out when an existing period (enabled or not) covers the same days or more, so a period
    the user switched off in config stays off and calling this twice adds nothing new. A calendar period
    that overlaps an existing one but sticks out either side is kept as its own entry.
    `markers` is replaced with the calendar's markers. `now` and `fetch` are passed to `load_calendar`.
    """
    result = load_calendar(cfg, now, fetch)
    existing = cfg.away_periods
    added = tuple(p for p in result.away if not any(q.start <= p.start and p.end <= q.end for q in existing))
    return replace(cfg, away_periods=existing + added, markers=tuple(result.markers)), result


# --- events -> days ---------------------------------------------------------------------------


def _occurrences(event, window_start: date, window_end: date) -> list[tuple[date, date]]:
    """Each (start, end) of the event, repeats included; values are dates (all-day) or naive local datetimes.

    Repeats are only generated near `[window_start, window_end]`. Raises ValueError for unusable dates
    or a repeat rule dateutil can't expand.
    """
    start, end = event.start, event.end
    if not event.rrules:
        return [(_local(start), _local(end))]

    all_day = not isinstance(start, datetime)
    first = datetime.combine(start, time.min) if all_day else start
    rules = rruleset()
    for rule in event.rrules:
        rules.rrule(rrulestr(rule.to_ical().decode(), dtstart=first))
    # A day of slack either side covers timezone shifts; occurrences outside the semester are dropped later.
    lo = datetime.combine(window_start - ONE_DAY, time.min, first.tzinfo) - (end - start)
    hi = datetime.combine(window_end + 2 * ONE_DAY, time.min, first.tzinfo)
    excluded, found = event.exdates, []
    for moment in rules.between(lo, hi, inc=True):
        occurrence = moment.date() if all_day else moment
        if occurrence not in excluded:
            found.append((_local(occurrence), _local(occurrence + (end - start))))
    return found


def _local(value: date) -> date:
    """Timezone-aware datetimes become naive local time; dates and naive datetimes pass through."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _touched_days(start: date, end: date) -> tuple[date, date]:
    """First and last calendar day the event touches (inclusive). `end` is exclusive, as in ICS."""
    if not isinstance(start, datetime):
        return start, max(start, end - ONE_DAY)
    last_moment = max(start, end - timedelta(microseconds=1))
    return start.date(), last_moment.date()


def _away_days(start: date, end: date) -> tuple[date, date] | None:
    """First and last full day away (inclusive), or None when a timed event is too short to count."""
    first, last = _touched_days(start, end)
    if not isinstance(start, datetime):
        return first, last
    if end - start < timedelta(hours=MIN_AWAY_HOURS):
        return None

    def covered(day: date) -> timedelta:
        midnight = datetime.combine(day, time.min)
        return min(end, midnight + ONE_DAY) - max(start, midnight)

    days = [first + i * ONE_DAY for i in range((last - first).days + 1)]
    mostly_away = [d for d in days if covered(d) >= HALF_DAY] or [max(days, key=covered)]
    return mostly_away[0], mostly_away[-1]


def _clip(days: tuple[date, date] | None, cfg: Config) -> tuple[date, date] | None:
    """Trim an inclusive day range to the semester; None when nothing is left."""
    if days is None:
        return None
    first, last = max(days[0], cfg.semester_start), min(days[1], cfg.semester_end)
    return (first, last) if first <= last else None


def _unique(items: list, key) -> list:
    """Sort by `key` and keep the first item for each key (the sort is stable, so first read wins)."""
    kept = {}
    for item in items:
        kept.setdefault(key(item), item)
    return [kept[k] for k in sorted(kept)]


def _compile(patterns: tuple[str, ...], warnings: list[str]) -> list[re.Pattern]:
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            warnings.append(f"Calendar pattern {pattern!r} isn't a valid regular expression ({exc}); it was ignored.")
    return compiled


# --- sources ----------------------------------------------------------------------------------


def _calendars(cfg: Config, now: datetime, fetch: Fetcher, warnings: list[str]) -> Iterator[tuple[str, Calendar]]:
    """Yield (label, parsed calendar) for each readable source; problems go to `warnings`."""
    folder = cfg.calendar.ics_dir
    if folder is not None and folder.is_dir():
        for path in sorted(folder.glob("*.ics")):
            try:
                yield path.name, _parse(path.read_bytes())
            except (OSError, ValueError):
                warnings.append(f"{path.name}: couldn't be read as a calendar file; it was skipped.")
    for url in cfg.calendar.ics_urls:
        calendar = _feed(cfg, url, now, fetch, warnings)
        if calendar is not None:
            yield _host(url), calendar


def _feed(cfg: Config, url: str, now: datetime, fetch: Fetcher, warnings: list[str]) -> Calendar | None:
    """A feed's calendar: the cached copy while fresh, else a download, else a stale cached copy."""
    cache = _cache_path(cfg, url)
    cached_at = datetime.fromtimestamp(cache.stat().st_mtime) if cache is not None and cache.exists() else None
    if cached_at is not None and timedelta(0) <= now - cached_at < timedelta(hours=cfg.calendar.cache_hours):
        try:
            return _parse(cache.read_bytes())
        except (OSError, ValueError):
            cached_at = None  # unusable cache: fall through to a download

    try:
        data = fetch(url)
        calendar = _parse(data)  # parse before caching so a login page never replaces a good copy
    except Exception as exc:  # network, HTTP, or not-a-calendar; the feed is optional, the app is not
        if cached_at is not None:
            try:
                stale = _parse(cache.read_bytes())
                warnings.append(
                    f"Couldn't refresh the calendar from {_host(url)} ({_brief(exc, url)}); "
                    f"using the copy saved {cached_at:%b} {cached_at.day}."
                )
                return stale
            except (OSError, ValueError):
                pass
        warnings.append(f"Couldn't load the calendar from {_host(url)} ({_brief(exc, url)}); it was skipped.")
        return None

    if cache is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
            os.utime(cache, (now.timestamp(), now.timestamp()))  # freshness is judged against `now`
        except OSError:
            pass  # caching is a convenience; the download itself worked
    return calendar


def _parse(data: bytes) -> Calendar:
    """Parse .ics bytes; raises ValueError with a short, content-free message when they aren't a calendar."""
    try:
        return Calendar.from_ical(data)
    except Exception:
        raise ValueError("not a calendar file") from None


def _cache_path(cfg: Config, url: str) -> Path | None:
    """Where a feed is cached; the name is a hash so the URL (often a private token) isn't on disk."""
    if cfg.calendar.cache_dir is None:
        return None
    return cfg.calendar.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.ics"


def _fetch_url(url: str) -> bytes:
    """Download a calendar feed over http(s) (`webcal://` is treated as https). Raises on any failure."""
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("only http(s) and webcal links are supported")
    request = urllib.request.Request(url, headers={"User-Agent": "dinemaster"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # scheme checked above
        return response.read(MAX_FEED_BYTES)


def _host(url: str) -> str:
    """A feed's display name: its host only, since the rest of the URL may be a secret."""
    try:
        return urlsplit(url).hostname or "calendar feed"
    except ValueError:
        return "calendar feed"


def _brief(exc: Exception, url: str) -> str:
    """A short, single-line reason for a download warning that never repeats any part of the URL."""
    text = " ".join(str(exc).split())
    try:
        parts = urlsplit(url)
        secrets = [piece for piece in (url, parts.path, parts.query) if len(piece) > 1]
    except ValueError:
        secrets = [url]
    if not text or any(piece in text for piece in secrets):
        return type(exc).__name__
    return text if len(text) <= 80 else text[:77] + "..."
