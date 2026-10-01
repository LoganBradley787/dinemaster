"""Tiles page: what the tiles say, that live tiles flip with CSS only, and that text fits its tile.

All data is synthetic. `raw_dir` points at an empty folder so the observed window comes from the
last transaction of each pot, never from real export files.
"""

import re
from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from dinemaster import budget
from dinemaster import tiles as T
from dinemaster.config import AwayPeriod, Marker, SemesterDef, load_config

COLS = ["account", "pot", "timestamp", "date", "description", "amount", "balance", "kind", "dd_class"]
S = date(2026, 8, 21)  # a Friday
LAST = date(2026, 9, 22)  # a Tuesday: the last day the synthetic data covers
AS_OF = date(2026, 9, 23)  # a Wednesday
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def frame(rows):
    out = []
    for pot, ts, desc, amount, kind, dd_class in rows:
        t = datetime.fromisoformat(ts)
        out.append((pot, pot, t, t.date(), desc, amount, 0.0, kind, dd_class))
    df = pd.DataFrame(out, columns=COLS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def days(start=S, end=LAST):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def swipe(d, hour=12, minute=0, place="Hall A"):
    return ("ME", f"{d} {hour:02d}:{minute:02d}", place, -1.0, "usage", None)


def dd(d, dollars, dd_class, place="Cafe", hour=13):
    return ("DD", f"{d} {hour:02d}:00", place, -dollars, "usage", dd_class)


def steady_rows(place="Hall A"):
    """Three swipes every Thursday, none on Saturdays, one otherwise; a little dining-dollar spending."""
    rows = []
    for d in days():
        for i in range({THU: 3, SAT: 0}.get(d.weekday(), 1)):
            rows.append(swipe(d, 12, i, place))
    rows += [dd(date(2026, 9, 1), 12.0, "meal"), dd(date(2026, 9, 10), 4.5, "snack", "Market", hour=22), dd(LAST, 3.0, "snack", "Market", hour=23)]
    return rows


def heavy_rows():
    """Four swipes and a $10 dining-dollar meal every day: both pots run out well before the end."""
    rows = []
    for d in days():
        rows += [swipe(d, 12, i) for i in range(4)] + [dd(d, 10.0, "meal")]
    return rows


@pytest.fixture
def cfg(tmp_path):
    return replace(
        load_config(), as_of=AS_OF, starting_me=160, starting_dd=360.0, raw_dir=tmp_path / "no-exports",
        exclude_away=True, away_periods=(), markers=(), history=(), dd_rollover=False,
    )  # fmt: skip


def page(cfg, rows, today=None):
    data = T.gather(cfg, frame(rows), today or cfg.as_of)
    return T.build_groups(data), data


def faces(groups):
    """Every face on the page, fronts and backs, as "pre|value|post|sentences"."""
    out = []
    for g in groups:
        for t in g.tiles:
            for f in (t, t.back):
                if f is not None:
                    out.append(f"{f.pre}|{f.value}|{f.post}|{' '.join(f.lines)}")
    return "\n".join(out)


def tile(groups, pre, group=None):
    found = [t for g in groups if group in (None, g.title) for t in g.tiles if t.pre.startswith(pre)]
    assert found, f"no tile starting with {pre!r}"
    return found[0]


# --- what the tiles say -----------------------------------------------------


def test_groups_in_order(cfg):
    groups, _ = page(cfg, steady_rows())
    assert [g.title for g in groups] == ["right now", "projections", "pace", "day by day", "this week", "habits"]


def test_verdict_uses_forecast_range_and_chance(cfg):
    groups, data = page(cfg, steady_rows())
    verdict = groups[0].tiles[0]
    f = data.forecast.me
    assert verdict.size == "large" and verdict.pre == "On track to have"
    assert verdict.value == T.num(f.leftover_p50, 0)
    assert "extra meal exchanges on Dec 18" in verdict.post
    assert f"likely {T.num(f.leftover_lo, 0)}" in verdict.post and "chance they last" in verdict.post
    assert verdict.back.pre == "Chance they last to Dec 18" and verdict.back.value == T.chance(f.prob_lasts)
    assert "each day of the week" in verdict.back.post


def test_dining_dollars_hold_with_range_and_leftover_fate(cfg):
    groups, data = page(cfg, steady_rows())
    hold = tile(groups, "Dining dollars will hold")
    assert hold.value == T.chance(data.forecast.dd.prob_lasts)
    assert "chance of lasting" in hold.post and "likely $" in hold.post
    left = tile(groups, "Left over on Dec 18")
    assert left.value == T.money(data.forecast.dd.leftover_p50) and "goes unspent" in left.post
    assert left.back.pre == "That is enough for" and "meal" in left.back.value


def test_rollover_changes_leftover_wording(cfg):
    groups, _ = page(replace(cfg, dd_rollover=True), steady_rows())
    assert "carries into spring" in tile(groups, "Left over on Dec 18").post


def test_running_out_of_both(cfg):
    groups, data = page(cfg, heavy_rows())
    verdict = groups[0].tiles[0]
    assert verdict.pre == "Meal exchanges run out" and verdict.color == T.RED
    assert verdict.value == T.day(data.forecast.me.runout_p50)
    assert "likely " in verdict.post and "early" in verdict.post and "chance they last to Dec 18" in verdict.post
    out = tile(groups, "Dining dollars run out")
    assert out.value == T.day(data.forecast.dd.runout_p50)
    assert "likely " in out.post and "chance of lasting" in out.post


def test_no_usage_yet_does_not_crash(cfg):
    groups, _ = page(cfg, [("ME", "2026-08-20 09:00", "Deposit", 160.0, "load", None)])
    text = faces(groups)
    assert "No meals used yet|—|nothing to project from" in text
    assert "Dining dollars|untouched|" in text


def test_empty_ledger_still_builds_a_page(cfg):
    html = T.build_page_html(cfg, pd.DataFrame(columns=COLS), AS_OF)
    assert "nothing to project from" in html


def test_plan_tile_matches_the_daily_plan(cfg):
    groups, data = page(cfg, steady_rows())
    plan = tile(groups, "Today's plan")
    line = budget.today_line(data.plan, cfg.as_of)  # e.g. "1 meal today, 3 tomorrow"
    today_part, _, tomorrow_part = line.partition(", ")
    assert today_part.lower() == f"{plan.value} today"
    assert plan.post == f"then {tomorrow_part}"
    week = data.plan.head(7)
    assert plan.back.value == " ".join(str(n) for n in week["meals"])
    assert f"{int(week['meals'].sum())} meals" in plan.back.post


def test_plan_tile_when_today_is_already_in_the_data(cfg):
    groups, _ = page(replace(cfg, as_of=LAST), steady_rows())
    plan = tile(groups, "Tomorrow's plan")
    assert "meal" in plan.value and plan.post == "today is already in the data"


def test_plan_tile_shows_away_days(cfg):
    away = AwayPeriod("Trip", AS_OF + timedelta(days=1), AS_OF + timedelta(days=2))
    groups, _ = page(replace(cfg, away_periods=(away,)), steady_rows())
    plan = tile(groups, "Today's plan")
    assert plan.post == "then away tomorrow"
    assert plan.back.value.split()[1:3] == ["–", "–"]


def test_spend_down_sentence_and_weekly_numbers(cfg):
    groups, data = page(cfg, steady_rows())
    spend = tile(groups, "To finish at zero")
    assert spend.size == "large" and " ".join(spend.lines) == data.spend.sentence
    assert spend.back.pre == "Every week until Dec 18"
    assert spend.back.value.startswith(str(int(data.spend.exchanges_per_week + 0.5)))
    assert "exchanges" in spend.back.post


def test_weekday_tiles_show_each_day_and_the_extremes(cfg):
    groups, data = page(cfg, steady_rows())
    by_day = next(g for g in groups if g.title == "day by day").tiles
    assert len(by_day) == 8
    fact = by_day[0]
    assert (fact.pre, fact.value, fact.post) == ("Biggest day", "Thu", "3 a day")
    assert (fact.back.pre, fact.back.value, fact.back.post) == ("Lightest day", "Sat", "0 a day")
    names = [t.pre for t in by_day[1:]]
    assert names == ["Monday", "Tuesday", "Wed · today", "Thursday", "Friday", "Saturday", "Sunday"]
    thursday = by_day[4]
    assert thursday.value == "3" and thursday.post == "meals a day"
    assert by_day[1].post == "meal a day"  # exactly one
    assert thursday.back.post == "dining $ a day" and thursday.back.value.startswith("$")
    profile = data.forecast.profile
    assert by_day[2].back.value == T.cents(profile.loc[TUE, "dd_spend_avg"])


def test_unseen_weekday_is_not_shown_as_zero(cfg):
    rows = [swipe(d) for d in days(S, S + timedelta(days=2))]  # Fri, Sat, Sun only
    groups, _ = page(replace(cfg, as_of=S + timedelta(days=3)), rows)
    monday = tile(groups, "Mon · today", "day by day")
    assert (monday.value, monday.post) == ("—", "not seen yet")


def test_weekly_recap_tiles(cfg):
    groups, data = page(cfg, steady_rows())
    week = next(g for g in groups if g.title == "this week").tiles
    assert [t.pre for t in week] == ["This week so far", "Last week"]
    latest = data.recaps[0]
    assert week[0].value == str(latest.meals) and week[0].post.startswith("meal")
    assert "than the same days last week" in week[0].post or "level with" in week[0].post
    assert week[0].back.lines and set(week[0].back.lines) <= set(latest.lines[1:])
    assert week[1].value == "8"  # Mon-Sun: 1+1+1+3+1+0+1


def test_stale_data_names_the_week_instead_of_calling_it_this_week(cfg):
    groups, _ = page(replace(cfg, as_of=date(2026, 10, 14)), steady_rows(), today=date(2026, 10, 14))
    week = next(g for g in groups if g.title == "this week").tiles
    assert week[0].pre == "Week of Sep 21"
    stale = tile(groups, "Data through")
    assert (stale.value, stale.post, stale.color) == ("Sep 22", "22 days ago", T.RED)


def test_a_week_that_is_already_past_is_compared_with_the_week_before_it(cfg):
    # On Monday Sep 28 the newest data week (Sep 21-22) is labelled "Last week"; the recap's own
    # "... than the same days last week" would then point at the wrong week.
    groups, _ = page(replace(cfg, as_of=date(2026, 9, 28)), steady_rows())
    latest = next(g for g in groups if g.title == "this week").tiles[0]
    assert latest.pre == "Last week"
    assert "last week" not in latest.post
    assert latest.post.endswith("the same days the week before")


def test_habit_tiles(cfg):
    groups, data = page(cfg, steady_rows())
    spot = tile(groups, "Your most-swiped spot")
    assert spot.value == "Hall A" and spot.post.endswith("swipes")
    assert spot.back.value == "12 PM" and spot.back.post == "most often on a Thursday"
    late = tile(groups, "After 9 PM you've spent")
    assert late.value == "$8" and "2 late nights" in late.post
    assert (late.back.pre, late.back.value, late.back.post) == ("Late-night favorite", "Market", "2 of 2 late visits")
    streak = tile(groups, "Longest run of days with a meal")
    assert data.streaks.meal_streak.length == 6  # Sunday to Friday, every week
    assert (streak.value, streak.post) == ("6 days", "Sep 13–18 · 3 in a row right now")
    assert streak.back.pre == "Most days in a row at one place" and "Hall A" in streak.back.post


def test_semester_comparison_tile(cfg):
    spring = SemesterDef("Spring 2026", date(2026, 1, 14), date(2026, 5, 9))
    old = [swipe(d, place="Old Hall") for d in days(date(2026, 3, 1), date(2026, 3, 10)) for _ in range(2)]
    groups, _ = page(replace(cfg, history=(spring,)), old + steady_rows())
    vs = tile(groups, "Meal exchanges vs Spring 2026")
    assert vs.value.endswith("% less") and "swipes a day now" in vs.post and "2 then" in vs.post
    assert "from 10 of 116 days on record" in vs.post
    assert (vs.back.pre, vs.back.value, vs.back.post) == ("Spring 2026 favorite", "Old Hall", "20 visits")


def test_no_comparison_tile_without_history(cfg):
    assert " vs " not in faces(page(cfg, steady_rows())[0])


def test_calendar_tiles(cfg):
    away = AwayPeriod("Thanksgiving recess", date(2026, 11, 25), date(2026, 11, 29), source="calendar")
    exams = Marker("Final Exams", date(2026, 12, 10), date(2026, 12, 18))
    groups, _ = page(replace(cfg, away_periods=(away,), markers=(exams,)), steady_rows())
    text = faces(groups)
    assert "Thanksgiving recess|Nov 25–29|away · in 63 days" in text
    assert "Final Exams|Dec 10–18|in 78 days" in text


# --- live tiles and markup ---------------------------------------------------


def test_render_escapes_text(cfg):
    groups, _ = page(cfg, [swipe(d, place="<b>Hall</b>") for d in days()])
    html = T.render_html(groups, "Dining", "as of today")
    assert "<b>Hall</b>" not in html and "&lt;b&gt;Hall&lt;/b&gt;" in html


def test_live_tiles_flip_with_css_only(cfg):
    html = T.build_page_html(cfg, frame(steady_rows()), AS_OF)
    assert "<script" not in html.lower()
    live = re.findall(r'<div class="tile \w+ live" style="([^"]*)"><div class="flip"><div class="face front"', html)
    assert len(live) >= 8
    assert html.count('class="face back"') == len(live)
    # staggered: no two live tiles share a period and a delay, and neighbours differ in both
    timings = [(re.search(r"--t:(\d+)s", s).group(1), re.search(r"--d:([\d.]+)s", s).group(1)) for s in live]
    assert len(set(timings)) == len(timings)
    assert all(a[0] != b[0] and a[1] != b[1] for a, b in zip(timings, timings[1:]))
    assert "@keyframes metro-flip" in T.CSS
    assert ".metro .tile:hover .flip { animation-play-state: paused; }" in T.CSS
    reduced = T.CSS.split("@media (prefers-reduced-motion: reduce)")[1].split("@container")[0]
    assert ".metro .flip { animation: none; }" in reduced


def test_static_tiles_have_one_face(cfg):
    html = T.render_html([T.Group("g", [T.Tile("a", "1", "b", T.BLUE)])], "t", "s")
    assert html.count('class="face"') == 1 and 'class="flip"' not in html
    assert '<div class="tile sq" style="background:#2672EC;--i:0">' in html


def test_post_line_breaks_and_sentences_render(cfg):
    front = T.Tile("p", "", "", T.BLUE, "large", lines=("One.", "Two & three."), back=T.Tile("p", "5", "a\nb <c>", T.RED))
    html = T.render_html([T.Group("g", [front])], "t", "s")
    assert "<span>One.</span><span>Two &amp; three.</span>" in html
    assert "a<br>b &lt;c&gt;" in html
    assert "background:#E51400" in html  # the back face keeps its own colour


# --- text fits its tile ------------------------------------------------------


def test_value_class_steps_down_as_values_get_wider():
    assert T.value_class("112", "sq") == ""
    assert T.value_class("$1.84", "sq") == "v2"
    assert T.value_class("Sep 30", "sq") == "v3"
    assert T.value_class("GH Gastons Market Just Walk Out", "wide") == "long"
    assert T.value_class("28", "large") == ""
    assert T.value_class(">99%", "large") == "v2"
    assert T.value_class("Nov 30", "large") == "v3"
    assert T.value_class("Wednesday", "wide") in ("", "v2")


def test_clip_and_text_lines():
    assert T.clip("Hall A", 16) == "Hall A"
    long_name = "562251009723 - Watson-Webb House-Vending Rm (Snack)"
    cut = T.clip(long_name, 16)
    assert cut.endswith("…") and T.em(cut) <= 16 and long_name.startswith(cut[:-1])
    assert T.text_lines("", 10) == 0
    assert T.text_lines("meals a day", 7) == 1
    assert T.text_lines("dining dollars\n≈ 18 meals", 7.1) == 2
    assert T.text_lines("a day in dining dollars", 7.1) == 2


def test_line_allowances_always_fit_the_tile_height():
    """Whatever the text, the allowances `_layout` hands out add up to no more than the tile holds."""
    long_text = "words " * 60
    for size, height, pre_line, post_line, value_px in (("sq", 83.0, 14.1, 14.1, 37.5), ("wide", 83.0, 14.1, 14.1, 37.5), ("large", 185.0, 17.7, 15.9, 78.0)):
        for face in (
            T.Tile(long_text, "88", long_text, T.BLUE),
            T.Tile(long_text, long_text, long_text, T.BLUE),
            T.Tile("", "88", long_text, T.BLUE),
            T.Tile(long_text, "88", long_text, T.BLUE, bar=0.5),
        ):
            lay = T._layout(face, size)
            g = T.GEOMETRY[size]
            value = g.long_px * 1.1 * g.long_lines if lay.value_cls == "long" else value_px
            bar = 10.0 if face.bar is not None else 0.0
            gap = 5.0 if size == "large" else 3.0
            assert lay.pre * pre_line + value + lay.post * post_line + gap + bar <= height, (size, face.bar, lay)
    lay = T._layout(T.Tile(long_text, "", "", T.BLUE, lines=(long_text,)), "large")
    assert lay.pre * 17.7 + lay.say * 17.6 + 3 * 4.1 <= 185.0


def test_overlong_text_is_reported():
    assert T.problems(T.Tile("Data covers through the last day", "Sep 30", "yesterday", T.SLATE))
    assert T.problems(T.Tile("Monday", "1.7", "meals a day on a typical Monday", T.NAVY))  # three caption lines on a square
    assert T.problems(T.Tile("x", "", "", T.BLUE, "wide", lines=("Sentence.",)))
    assert not T.problems(T.Tile("Monday", "1.7", "meals a day", T.NAVY))


@pytest.mark.parametrize("rows", [steady_rows, heavy_rows, lambda: steady_rows("GH Gastons Market Just Walk Out")])
def test_every_tile_fits(cfg, rows):
    spring = SemesterDef("Spring 2026", date(2026, 1, 14), date(2026, 5, 9))
    old = [dd(d, 9.0, "snack", "562251009723 - Watson-Webb House-Vending Rm (Snack)") for d in days(date(2026, 3, 1), date(2026, 3, 10))]
    away = AwayPeriod("Thanksgiving recess", date(2026, 11, 25), date(2026, 11, 29))
    exams = Marker("Final Examinations", date(2026, 11, 28), date(2026, 12, 2))
    full = replace(cfg, history=(spring,), away_periods=(away,), markers=(exams,))
    for variant in (full, replace(full, exclude_away=False), replace(full, as_of=LAST)):
        groups, _ = page(variant, old + rows())
        found = [p for g in groups for t in g.tiles for p in T.problems(t)]
        assert found == []
