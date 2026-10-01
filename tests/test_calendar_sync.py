"""Calendar sync tests: synthetic .ics files in tmp_path, and an injected fetcher (never the network)."""

import os
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from dinemaster import calendar_sync
from dinemaster.calendar_sync import CalendarResult, apply_calendar, load_calendar
from dinemaster.config import AwayPeriod, CalendarConfig, Marker, load_config

NOW = datetime(2026, 10, 1, 12, 0)
FEED_URL = "https://feeds.example.edu/private/calendar.ics?token=SECRET123"
THANKSGIVING = AwayPeriod("Thanksgiving recess", date(2026, 11, 25), date(2026, 11, 29))


def event(summary, start, end=None, extra=()):
    """One VEVENT. `start`/`end` are raw ICS values, e.g. "20261010" (all-day) or "20261010T170000" (timed)."""

    def prop(name, value):
        return f"{name};VALUE=DATE:{value}" if len(value) == 8 else f"{name}:{value}"

    lines = ["BEGIN:VEVENT", f"UID:{summary}-{start}@test", f"SUMMARY:{summary}", prop("DTSTART", start)]
    if end:
        lines.append(prop("DTEND", end))
    return "\r\n".join([*lines, *extra, "END:VEVENT"])


def ics(*events):
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN", *events, "END:VCALENDAR", ""])


def make_cfg(tmp_path, urls=(), away_periods=(THANKSGIVING,), **calendar_overrides):
    settings = dict(
        ics_dir=tmp_path / "calendar",
        ics_urls=tuple(urls),
        away_patterns=("(?i)break", "(?i)recess", r"(?i)\btrip\b"),
        marker_patterns=("(?i)reading day", "(?i)exam"),
        cache_hours=24.0,
        cache_dir=tmp_path / "cache",
    )
    settings.update(calendar_overrides)
    return replace(
        load_config(),
        semester_start=date(2026, 8, 21),
        semester_end=date(2026, 12, 18),
        away_periods=tuple(away_periods),
        calendar=CalendarConfig(**settings),
    )


def write_ics(tmp_path, name, *events):
    folder = tmp_path / "calendar"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(ics(*events))


def spans(items):
    return [(i.name, i.start, i.end) for i in items]


class FakeFetcher:
    """Stands in for the network: returns `payload` (or raises it, when it is an exception)."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


# --- files in ics_dir -------------------------------------------------------------------------


def test_missing_ics_dir_gives_empty_result(tmp_path):
    for cfg in (make_cfg(tmp_path), make_cfg(tmp_path, ics_dir=None)):
        assert load_calendar(cfg, NOW) == CalendarResult(away=[], markers=[], warnings=[], sources=[])


def test_all_day_multi_day_event_becomes_away_period_with_inclusive_end(tmp_path):
    write_ics(tmp_path, "uva.ics", event("Fall break", "20261010", "20261014"))  # DTEND is exclusive
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert result.away == [AwayPeriod("Fall break", date(2026, 10, 10), date(2026, 10, 13), True, "calendar")]
    assert result.sources == ["uva.ics"]
    assert result.markers == [] and result.warnings == []


def test_all_day_event_without_dtend_is_one_day(tmp_path):
    write_ics(tmp_path, "uva.ics", event("Day trip", "20261010"))
    assert spans(load_calendar(make_cfg(tmp_path), NOW).away) == [("Day trip", date(2026, 10, 10), date(2026, 10, 10))]


def test_timed_event_shorter_than_20h_is_not_away(tmp_path):
    write_ics(tmp_path, "me.ics", event("Lunch break", "20261010T120000", "20261010T130000"),
              event("Road trip", "20261010T080000", "20261011T030000"))  # 19h
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert result.away == [] and result.warnings == []


def test_long_timed_event_is_away_on_the_days_it_mostly_covers(tmp_path):
    # Fri 17:00 -> Sun 20:00: Friday is mostly on campus (7h away), Saturday and Sunday are away.
    write_ics(tmp_path, "me.ics", event("Trip home", "20261009T170000", "20261011T200000"))
    assert spans(load_calendar(make_cfg(tmp_path), NOW).away) == [("Trip home", date(2026, 10, 10), date(2026, 10, 11))]


def test_20h_timed_event_split_evenly_over_midnight_counts_one_day(tmp_path):
    write_ics(tmp_path, "me.ics", event("Trip", "20261009T140000", "20261010T100000"))  # 10h + 10h
    assert spans(load_calendar(make_cfg(tmp_path), NOW).away) == [("Trip", date(2026, 10, 9), date(2026, 10, 9))]


def test_timed_event_ending_at_midnight_does_not_spill_into_next_day(tmp_path):
    write_ics(tmp_path, "me.ics", event("Trip", "20261010T000000", "20261012T000000"))
    assert spans(load_calendar(make_cfg(tmp_path), NOW).away) == [("Trip", date(2026, 10, 10), date(2026, 10, 11))]


def test_timezone_aware_event_is_read_in_local_time(tmp_path):
    # Whatever this machine's UTC offset, Oct 11-13 are fully inside the event.
    write_ics(tmp_path, "me.ics", event("Trip", "20261010T120000Z", "20261014T120000Z"))
    (period,) = load_calendar(make_cfg(tmp_path), NOW).away
    assert period.start in (date(2026, 10, 10), date(2026, 10, 11))
    assert period.end in (date(2026, 10, 13), date(2026, 10, 14))


def test_events_outside_the_semester_are_ignored_and_straddlers_are_clipped(tmp_path):
    write_ics(
        tmp_path,
        "uva.ics",
        event("Summer break", "20260601", "20260815"),
        event("Spring break", "20270306", "20270315"),
        event("Final exams", "20270501", "20270508"),
        event("Winter break", "20261215", "20270112"),  # overlaps the last four days
        event("Move-in recess", "20260815", "20260823"),  # overlaps the first two days
    )
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert spans(result.away) == [
        ("Move-in recess", date(2026, 8, 21), date(2026, 8, 22)),
        ("Winter break", date(2026, 12, 15), date(2026, 12, 18)),
    ]
    assert result.markers == []


def test_marker_patterns_make_markers_and_unmatched_events_are_dropped(tmp_path):
    write_ics(
        tmp_path,
        "uva.ics",
        event("Reading Day", "20261209", "20261210"),
        event("Final Exams", "20261210", "20261219"),  # runs one day past the semester end -> clipped
        event("CS 2100 exam", "20261020T140000", "20261020T170000"),  # timed markers land on their date
        event("Dentist", "20261021T090000", "20261021T100000"),
    )
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert result.markers == [
        Marker("CS 2100 exam", date(2026, 10, 20), date(2026, 10, 20)),
        Marker("Reading Day", date(2026, 12, 9), date(2026, 12, 9)),
        Marker("Final Exams", date(2026, 12, 10), date(2026, 12, 18)),
    ]
    assert result.away == []


def test_event_matching_both_pattern_lists_is_both(tmp_path):
    write_ics(tmp_path, "uva.ics", event("Reading days (fall break)", "20261003", "20261007"))
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert spans(result.away) == [("Reading days (fall break)", date(2026, 10, 3), date(2026, 10, 6))]
    assert spans(result.markers) == spans(result.away)


def test_cancelled_events_are_skipped(tmp_path):
    write_ics(tmp_path, "me.ics", event("Trip", "20261010", "20261012", extra=["STATUS:CANCELLED"]))
    assert load_calendar(make_cfg(tmp_path), NOW).away == []


def test_same_event_in_two_files_is_listed_once(tmp_path):
    write_ics(tmp_path, "a.ics", event("Fall break", "20261010", "20261014"), event("Reading Day", "20261209"))
    write_ics(tmp_path, "b.ics", event("Fall Break", "20261010", "20261014"), event("Reading Day", "20261209"))
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert spans(result.away) == [("Fall break", date(2026, 10, 10), date(2026, 10, 13))]
    assert len(result.markers) == 1
    assert result.sources == ["a.ics", "b.ics"]


def test_malformed_file_is_a_warning_and_other_files_still_load(tmp_path):
    write_ics(tmp_path, "good.ics", event("Fall break", "20261010", "20261014"))
    (tmp_path / "calendar" / "broken.ics").write_text("this is not a calendar\n")
    (tmp_path / "calendar" / "binary.ics").write_bytes(b"\xff\xfe\x00\x01")
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert len(result.away) == 1
    assert result.sources == ["good.ics"]
    assert len(result.warnings) == 2
    assert any("broken.ics" in w for w in result.warnings) and any("binary.ics" in w for w in result.warnings)


def test_event_with_unreadable_dates_is_a_warning_not_a_crash(tmp_path):
    broken = "\r\n".join(["BEGIN:VEVENT", "SUMMARY:Mystery break", "DTSTART:garbage", "END:VEVENT"])
    no_start = "\r\n".join(["BEGIN:VEVENT", "SUMMARY:Another break", "END:VEVENT"])
    write_ics(tmp_path, "uva.ics", broken, no_start, event("Fall break", "20261010", "20261014"))
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert spans(result.away) == [("Fall break", date(2026, 10, 10), date(2026, 10, 13))]
    assert len(result.warnings) == 1
    assert "uva.ics" in result.warnings[0] and "2 event" in result.warnings[0]


def test_bad_pattern_is_a_warning_not_a_crash(tmp_path):
    write_ics(tmp_path, "uva.ics", event("Fall break", "20261010", "20261014"))
    cfg = make_cfg(tmp_path, away_patterns=("(unclosed", "(?i)break"))
    result = load_calendar(cfg, NOW)
    assert len(result.away) == 1
    assert len(result.warnings) == 1 and "(unclosed" in result.warnings[0]


# --- recurring events -------------------------------------------------------------------------


def test_simple_weekly_recurrence_is_expanded_and_honors_exdate(tmp_path):
    weekly = event(
        "Trip home", "20261003", "20261004", extra=["RRULE:FREQ=WEEKLY;COUNT=4", "EXDATE;VALUE=DATE:20261010"]
    )
    write_ics(tmp_path, "me.ics", weekly)
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert [p.start for p in result.away] == [date(2026, 10, 3), date(2026, 10, 17), date(2026, 10, 24)]
    assert all(p.start == p.end for p in result.away)
    assert result.warnings == []


def test_recurrence_only_yields_occurrences_inside_the_semester(tmp_path):
    yearly = event("Thanksgiving break", "20241127", "20241130", extra=["RRULE:FREQ=WEEKLY;INTERVAL=52"])
    write_ics(tmp_path, "me.ics", yearly)
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert spans(result.away) == [("Thanksgiving break", date(2026, 11, 25), date(2026, 11, 27))]


def test_unsupported_recurrence_is_skipped_with_a_warning(tmp_path):
    # A floating start with a UTC UNTIL is something the rule expander refuses.
    odd = event("Trip", "20261003T080000", "20261004T200000", extra=["RRULE:FREQ=WEEKLY;UNTIL=20261101T000000Z"])
    write_ics(tmp_path, "me.ics", odd, event("Fall break", "20261010", "20261014"))
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert spans(result.away) == [("Fall break", date(2026, 10, 10), date(2026, 10, 13))]
    assert len(result.warnings) == 1 and "me.ics" in result.warnings[0]


# --- feeds (ics_urls) -------------------------------------------------------------------------

FEED = ics(event("Fall break", "20261010", "20261014")).encode()
NEWER_FEED = ics(event("Fall break", "20261010", "20261015")).encode()


def test_feed_is_downloaded_then_served_from_cache_until_it_expires(tmp_path):
    cfg = make_cfg(tmp_path, urls=[FEED_URL])
    fetch = FakeFetcher(FEED)

    first = load_calendar(cfg, NOW, fetch=fetch)
    assert spans(first.away) == [("Fall break", date(2026, 10, 10), date(2026, 10, 13))]
    assert first.sources == ["feeds.example.edu"] and first.warnings == []
    assert fetch.calls == [FEED_URL]

    fetch.payload = NEWER_FEED
    cached = load_calendar(cfg, NOW + timedelta(hours=23), fetch=fetch)
    assert fetch.calls == [FEED_URL]  # cache hit: no second download
    assert cached.away == first.away and cached.warnings == []

    refreshed = load_calendar(cfg, NOW + timedelta(hours=25), fetch=fetch)
    assert len(fetch.calls) == 2
    assert refreshed.away[0].end == date(2026, 10, 14)


def test_failed_refresh_falls_back_to_stale_cache_with_a_warning(tmp_path):
    cfg = make_cfg(tmp_path, urls=[FEED_URL])
    load_calendar(cfg, NOW, fetch=FakeFetcher(FEED))

    down = FakeFetcher(OSError("network unreachable"))
    result = load_calendar(cfg, NOW + timedelta(days=3), fetch=down)
    assert len(down.calls) == 1
    assert spans(result.away) == [("Fall break", date(2026, 10, 10), date(2026, 10, 13))]
    assert result.sources == ["feeds.example.edu"]
    assert len(result.warnings) == 1
    assert "feeds.example.edu" in result.warnings[0] and "network unreachable" in result.warnings[0]


def test_failed_download_without_cache_is_a_warning_and_no_events(tmp_path):
    cfg = make_cfg(tmp_path, urls=[FEED_URL])
    result = load_calendar(cfg, NOW, fetch=FakeFetcher(TimeoutError("timed out")))
    assert result.away == [] and result.markers == [] and result.sources == []
    assert len(result.warnings) == 1 and "feeds.example.edu" in result.warnings[0]


def test_download_that_is_not_a_calendar_keeps_the_old_cache(tmp_path):
    # e.g. a login page served in place of the feed
    cfg = make_cfg(tmp_path, urls=[FEED_URL])
    load_calendar(cfg, NOW, fetch=FakeFetcher(FEED))
    later = NOW + timedelta(days=2)
    result = load_calendar(cfg, later, fetch=FakeFetcher(b"<html><body>Sign in</body></html>"))
    assert len(result.away) == 1 and len(result.warnings) == 1
    # the good copy survived, so the next successful-looking run still has it
    again = load_calendar(cfg, later, fetch=FakeFetcher(OSError("down")))
    assert len(again.away) == 1


def test_warnings_and_sources_never_show_the_feed_url_secret(tmp_path):
    cfg = make_cfg(tmp_path, urls=[FEED_URL])
    result = load_calendar(cfg, NOW, fetch=FakeFetcher(OSError("boom")))
    ok = load_calendar(cfg, NOW, fetch=FakeFetcher(FEED))
    for text in [*result.warnings, *ok.sources, *(p.name for p in (tmp_path / "cache").iterdir())]:
        assert "SECRET123" not in text


def test_error_text_that_repeats_the_url_is_replaced_by_the_error_type(tmp_path):
    cfg = make_cfg(tmp_path, urls=[FEED_URL])
    leaky = OSError("can't open /private/calendar.ics?token=SECRET123")
    (warning,) = load_calendar(cfg, NOW, fetch=FakeFetcher(leaky)).warnings
    assert "SECRET123" not in warning and "OSError" in warning


def test_not_a_calendar_warning_does_not_quote_the_page(tmp_path):
    cfg = make_cfg(tmp_path, urls=[FEED_URL])
    (warning,) = load_calendar(cfg, NOW, fetch=FakeFetcher(b"<html>Sign in</html>")).warnings
    assert "html" not in warning and "not a calendar file" in warning


def test_unexpected_failure_is_a_warning_not_a_crash(tmp_path, monkeypatch):
    write_ics(tmp_path, "uva.ics", event("Fall break", "20261010", "20261014"))

    def boom(*args, **kwargs):
        raise RuntimeError("surprise")

    monkeypatch.setattr(calendar_sync, "_calendars", boom)
    result = load_calendar(make_cfg(tmp_path), NOW)
    assert result.away == [] and len(result.warnings) == 1 and "RuntimeError" in result.warnings[0]


def test_module_level_fetcher_is_used_by_default_and_can_be_monkeypatched(tmp_path, monkeypatch):
    fetch = FakeFetcher(FEED)
    monkeypatch.setattr(calendar_sync, "_fetch_url", fetch)
    result = load_calendar(make_cfg(tmp_path, urls=[FEED_URL]), NOW)
    assert fetch.calls == [FEED_URL] and len(result.away) == 1


def test_no_cache_dir_means_download_every_time(tmp_path):
    cfg = make_cfg(tmp_path, urls=[FEED_URL], cache_dir=None)
    fetch = FakeFetcher(FEED)
    load_calendar(cfg, NOW, fetch=fetch)
    load_calendar(cfg, NOW, fetch=fetch)
    assert len(fetch.calls) == 2


def test_cache_freshness_is_measured_from_the_file_time(tmp_path):
    cfg = make_cfg(tmp_path, urls=[FEED_URL])
    load_calendar(cfg, NOW, fetch=FakeFetcher(FEED))
    (cache_file,) = (tmp_path / "cache").iterdir()
    old = (NOW - timedelta(hours=30)).timestamp()
    os.utime(cache_file, (old, old))
    fetch = FakeFetcher(NEWER_FEED)
    load_calendar(cfg, NOW, fetch=fetch)
    assert len(fetch.calls) == 1


def test_default_fetcher_refuses_non_web_links():
    with pytest.raises(ValueError):
        calendar_sync._fetch_url("file:///etc/passwd")


# --- apply_calendar ---------------------------------------------------------------------------


def test_apply_calendar_appends_new_periods_and_sets_markers(tmp_path):
    write_ics(
        tmp_path,
        "uva.ics",
        event("Fall break", "20261010", "20261014"),
        event("Thanksgiving Recess", "20261125", "20261130"),  # same days as the config period
        event("Turkey trip", "20261126", "20261128"),  # inside the config period
        event("Long Thanksgiving break", "20261121", "20261127"),  # overlaps but sticks out
        event("Reading Day", "20261209"),
    )
    cfg = make_cfg(tmp_path)
    new_cfg, result = apply_calendar(cfg, NOW)

    assert new_cfg.away_periods == (
        THANKSGIVING,
        AwayPeriod("Fall break", date(2026, 10, 10), date(2026, 10, 13), True, "calendar"),
        AwayPeriod("Long Thanksgiving break", date(2026, 11, 21), date(2026, 11, 26), True, "calendar"),
    )
    assert new_cfg.markers == (Marker("Reading Day", date(2026, 12, 9), date(2026, 12, 9)),)
    assert len(result.away) == 4  # the result still reports everything the calendar offered
    assert cfg.away_periods == (THANKSGIVING,) and cfg.markers == ()  # input untouched
    assert new_cfg.is_away(date(2026, 10, 12)) and not cfg.is_away(date(2026, 10, 12))


def test_apply_calendar_respects_a_disabled_config_period(tmp_path):
    write_ics(tmp_path, "uva.ics", event("Thanksgiving Recess", "20261125", "20261130"))
    off = replace(THANKSGIVING, enabled=False)
    new_cfg, _ = apply_calendar(make_cfg(tmp_path, away_periods=(off,)), NOW)
    assert new_cfg.away_periods == (off,)
    assert not new_cfg.is_away(date(2026, 11, 26))


def test_apply_calendar_twice_does_not_duplicate(tmp_path):
    write_ics(tmp_path, "uva.ics", event("Fall break", "20261010", "20261014"))
    once, _ = apply_calendar(make_cfg(tmp_path), NOW)
    twice, _ = apply_calendar(once, NOW)
    assert twice.away_periods == once.away_periods and len(once.away_periods) == 2


def test_apply_calendar_with_nothing_configured_returns_cfg_unchanged(tmp_path):
    cfg = make_cfg(tmp_path, ics_dir=None)
    new_cfg, result = apply_calendar(cfg, NOW)
    assert new_cfg.away_periods == cfg.away_periods and new_cfg.markers == ()
    assert result == CalendarResult()
