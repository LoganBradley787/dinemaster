"""Metro-style (Windows 8 Start screen) tile page: plain-sentence verdicts over the app's numbers.

`build_page_html(cfg, df, today)` is the only entry point `app.py` needs. It runs in three steps:

    gather        collects everything the page shows (metrics, the day-of-week forecast, the daily
                  plan and spend-down, weekly recaps, habits, semester comparison) into `PageData`.
    build_groups  turns that into `Group`s of `Tile`s — pure text and colours, easy to test.
    render_html   turns tiles into markup plus the page's CSS.

Live tiles: a `Tile` may carry a `back` face. The page flips between the two faces on a timer with
CSS only (Streamlit strips scripts): each live tile gets its own period and delay so they never
flip in unison, a tile stops flipping while the pointer is over it, and with "reduce motion" on
only the front face shows. So the front always carries the main fact; the back adds to it.

Text never leaves its tile. Every font size and padding scales with the tile unit `--u`, so a
face that fits at the smallest unit fits at every size; `value_class` picks the largest numeral
size the value fits in, `_layout` hands each caption a line allowance that always adds up to
the tile's height, and CSS line clamps enforce it. `problems` reports any face whose text would
need more room than that (the tests keep that list empty for realistic data).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from html import escape

import pandas as pd

from . import budget, compare, forecast, habits, recap
from . import metrics as m
from .config import Config
from .freshness import data_through, observed_through

# Metro palette, darkened where needed so white text stays readable
BLUE, NAVY, TEAL, GREEN, RED = "#2672EC", "#2B5797", "#008299", "#008A00", "#E51400"
ORANGE, CRIMSON, PURPLE, VIOLET, MAGENTA, SLATE = "#D24726", "#B91D47", "#7E3878", "#603CBA", "#C1004F", "#525252"

#: A pot counts as safely "on track" when at least this share of simulated semesters still has some left.
SAFE_CHANCE = 0.9


@dataclass(frozen=True)
class Tile:
    """One tile face: a small line, a big value, a small line. `back` makes it a live tile."""

    pre: str  # small line above the number
    value: str  # the big number / date
    post: str  # small line below ("\n" starts a new line)
    color: str
    size: str = "sq"  # sq (1x1) | wide (2x1) | large (2x2); a back face uses its front's size
    bar: float | None = None  # optional 0..1 progress bar
    lines: tuple[str, ...] = ()  # sentences shown instead of a big value (large tiles only)
    back: Tile | None = None  # second face of a live tile


@dataclass(frozen=True)
class Group:
    title: str
    tiles: list[Tile]


@dataclass(frozen=True, eq=False)
class PageData:
    """Everything the tiles are built from (see `gather`)."""

    cfg: Config
    df: pd.DataFrame
    today: date  # the real calendar day, for "data is N days old"
    metrics: m.Metrics
    daily: pd.DataFrame  # metrics.daily, cut off at the last observed day
    through: date | None  # last day the exports cover
    forecast: forecast.Forecast
    plan: pd.DataFrame  # budget.daily_budget from the day after the last observed day
    spend: budget.SpendDown
    recaps: list[recap.Recap]  # newest week first, at most two
    streaks: habits.Streaks
    late: habits.LateNight
    places: pd.DataFrame  # habits.place_stats
    summaries: list[compare.SemesterSummary]


# ---------------------------------------------------------------------------
# small text helpers
# ---------------------------------------------------------------------------


def num(x: float, digits: int = 1) -> str:
    """Compact number: one decimal by default, without a trailing .0."""
    return f"{x:,.{digits}f}".removesuffix("." + "0" * digits) if digits else f"{x:,.0f}"


def money(x: float) -> str:
    return f"${x:,.0f}"


def cents(x: float) -> str:
    return f"${x:,.2f}"


def day(d: date) -> str:
    return f"{d:%b} {d.day}"


def ago(days: int) -> str:
    return {0: "today", 1: "yesterday"}.get(days, f"{days} days ago")


def span(start: date, end: date) -> str:
    """A date range: "Sep 21", "Sep 21–23", "Nov 28 – Dec 2"."""
    if start == end:
        return day(start)
    if (start.year, start.month) == (end.year, end.month):
        return f"{day(start)}–{end.day}"
    return f"{day(start)} – {day(end)}"


def chance(p: float) -> str:
    """A simulated probability as text, never claiming certainty: ">99%", "83%", "<1%"."""
    if p >= 0.995:
        return ">99%"
    if p < 0.005:
        return "<1%"
    return f"{p * 100:.0f}%"


def plural(n: float, noun: str) -> str:
    return f"{num(n, 0)} {noun}" if round(n) == 1 else f"{num(n, 0)} {noun}s"


def _between(lo: float, hi: float, fmt) -> str:
    a, b = fmt(lo), fmt(hi)
    return a if a == b else f"{a}–{b}"


# ---------------------------------------------------------------------------
# fitting text into tiles
# ---------------------------------------------------------------------------

# Rough advance widths in em (Helvetica-like, a little generous so estimates err on the wide side).
_NARROW, _SLIM, _WIDE = set(" .,:;'|!iIjl·"), set("frt()-/"), set("mwMW%—")


def em(text: str) -> float:
    """Estimated width of `text` in em units."""
    total = 0.0
    for ch in text:
        total += 0.28 if ch in _NARROW else 0.34 if ch in _SLIM else 0.9 if ch in _WIDE else 0.7 if ch.isupper() else 0.56
    return total


def text_lines(text: str, width_em: float) -> int:
    """How many lines `text` takes when wrapped to `width_em` ("\\n" forces a break; 0 for no text)."""
    if not text:
        return 0
    count = 0
    for paragraph in text.split("\n"):
        count += 1
        used = 0.0
        for word in paragraph.split():
            w = em(word)
            if used and used + 0.28 + w > width_em:
                count += 1
                used = w
            else:
                used += (0.28 if used else 0.0) + w
            while used > width_em:  # one word wider than the line breaks inside the word
                count += 1
                used -= width_em
    return count


def clip(text: str, max_em: float) -> str:
    """`text`, cut with an ellipsis if it is wider than `max_em`."""
    if em(text) <= max_em:
        return text
    out = text
    while out and em(out) + 0.56 > max_em:
        out = out[:-1]
    return out.rstrip(" -–—·,") + "…"


@dataclass(frozen=True)
class _Geometry:
    """One tile size at the smallest tile unit (100px) — the tightest case, since text shrinks slower than tiles.

    All in px. Must match the CSS: `inner` is the width inside the padding, the `*_px` are font
    sizes, `tiers` the numeral sizes with their CSS class, `long_px`/`long_lines` the wrapping
    fallback for values too wide for any tier.
    """

    inner: float
    pre_px: float
    post_px: float
    tiers: tuple[tuple[float, str], ...]
    long_px: float
    long_lines: int


_SMALL_TIERS = ((36.0, ""), (28.0, "v2"), (22.0, "v3"))
GEOMETRY = {
    "sq": _Geometry(82.0, 11.5, 11.5, _SMALL_TIERS, 17.0, 2),
    "wide": _Geometry(190.0, 11.5, 11.5, _SMALL_TIERS, 20.0, 2),
    "large": _Geometry(184.0, 14.7, 12.7, ((78.0, ""), (56.0, "v2"), (42.0, "v3")), 26.0, 3),
}
SAY_PX = 13.5  # sentence text on large tiles
_SLACK = 0.96  # keep a little air on the right of big numerals


def value_class(value: str, size: str) -> str:
    """CSS class for the big value: "" (full size), "v2", "v3" (smaller steps) or "long" (wraps)."""
    g = GEOMETRY[size]
    width = em(value)
    for px, cls in g.tiers:
        if width * px <= g.inner * _SLACK:
            return cls
    return "long"


@dataclass(frozen=True)
class _Layout:
    value_cls: str
    pre: int  # line allowance for each part (0 = part absent)
    post: int
    say: int


def _layout(face: Tile, size: str) -> _Layout:
    """Line allowances for a face. Whatever the text, these always fit the tile's height."""
    g = GEOMETRY[size]
    cls = value_class(face.value, size) if face.value else ""
    pre_need = text_lines(face.pre, g.inner / g.pre_px)
    bar = 1 if face.bar is not None else 0  # a progress bar takes the room of one caption line
    if size == "large":
        pre = min(2, pre_need)
        if face.lines:
            return _Layout("", pre, 0, 9 - pre)
        return _Layout(cls, pre, (3 if cls == "long" else 4) - bar, 0)
    if size == "sq":
        return _Layout(cls, min(1, pre_need), 2 - bar, 0)
    # wide: three caption lines beside a one-line value; a bar or a wrapping value costs one
    allowance = 3 - bar - (1 if cls == "long" else 0)
    pre = min(pre_need, 2, max(1, allowance - 1))
    return _Layout(cls, pre, max(1, min(3, allowance - pre)), 0)


def problems(tile: Tile) -> list[str]:
    """Why `tile` would not show all of its text at the smallest tile size ([] when it fits).

    CSS clamps keep even a problem tile inside its square (text is cut with an ellipsis); this
    is the check that nothing gets cut.
    """
    out = []
    for name, face in (("front", tile), ("back", tile.back)):
        if face is None:
            continue
        g, lay = GEOMETRY[tile.size], _layout(face, tile.size)
        label = f"{name} of {tile.size} tile {face.pre!r} / {face.value!r}"
        pre = text_lines(face.pre, g.inner / g.pre_px)
        post = text_lines(face.post, g.inner / g.post_px)
        if face.lines and tile.size != "large":
            out.append(f"{label}: sentences only fit on large tiles")
        if pre > lay.pre:
            out.append(f"{label}: top line needs {pre} lines, has {lay.pre}")
        if post > lay.post:
            out.append(f"{label}: bottom line needs {post} lines, has {lay.post}")
        if tile.size == "sq" and pre + post > 2:
            out.append(f"{label}: a square holds two caption lines, this needs {pre + post}")
        if lay.value_cls == "long" and text_lines(face.value, g.inner / g.long_px) > g.long_lines:
            out.append(f"{label}: value needs more than {g.long_lines} lines")
        if face.lines and _say_lines(face.lines) > lay.say:
            out.append(f"{label}: sentences need {_say_lines(face.lines)} lines, have {lay.say}")
    return out


def _say_lines(lines: tuple[str, ...] | list[str]) -> int:
    return sum(text_lines(line, GEOMETRY["large"].inner / SAY_PX) for line in lines)


def _fit_sentences(lines: list[str], allowance: int = 7) -> tuple[str, ...]:
    """As many of `lines` as fit a large tile, dropping the longest first."""
    kept = list(lines)
    while len(kept) > 1 and _say_lines(kept) > allowance:
        kept.remove(max(kept, key=lambda s: text_lines(s, GEOMETRY["large"].inner / SAY_PX)))
    return tuple(kept)


# ---------------------------------------------------------------------------
# gathering
# ---------------------------------------------------------------------------


def gather(cfg: Config, df: pd.DataFrame, today: date) -> PageData:
    """Run every module the page draws on. Usage is only known through the observed day O."""
    metrics = m.compute_metrics(df, cfg)
    o = observed_through(cfg, df)
    fc = forecast.forecast(df, cfg)
    left = m.balances(df, replace(cfg, as_of=o))
    start = o + timedelta(days=1)
    daily = m.daily(df, cfg)
    weeks = recap.weeks_available(df, cfg)
    return PageData(
        cfg=cfg,
        df=df,
        today=today,
        metrics=metrics,
        daily=daily[[d <= o for d in daily.index]],
        through=data_through(cfg, df),
        forecast=fc,
        plan=budget.daily_budget(cfg, left.me_left, left.dd_left, fc.profile, start),
        spend=budget.spend_down(cfg, left.me_left, left.dd_left, fc.profile, start),
        recaps=[recap.weekly_recap(df, cfg, monday) for monday in reversed(weeks[-2:])],
        streaks=habits.streaks(df, cfg),
        late=habits.late_night(df, cfg),
        places=habits.place_stats(df, cfg),
        summaries=compare.semester_summaries(df, cfg),
    )


# ---------------------------------------------------------------------------
# tile groups
# ---------------------------------------------------------------------------


def _verdict(d: PageData) -> Tile:
    """The big tile: will the meal exchanges last? From the day-of-week forecast."""
    cfg, f = d.cfg, d.forecast.me
    end = day(cfg.semester_end)
    if d.metrics.balances.me_used <= 0:
        return Tile("No meals used yet", "—", "nothing to project from", SLATE, "large")
    if not f.available:
        return Tile("No forecast yet", "—", f.reason or "nothing to project from", SLATE, "large")
    if len(f.expected) <= 1:
        return Tile("The semester ended with", num(max(f.start_balance, 0), 0), "meal exchanges unused", SLATE, "large")
    if f.start_balance <= 0:
        return Tile("Meal exchanges", "0", "all used up", RED, "large")

    odds = chance(f.prob_lasts)
    if f.prob_lasts >= 0.5 or f.runout_p50 is None:
        color = GREEN if f.prob_lasts >= SAFE_CHANCE else ORANGE
        likely = _between(f.leftover_lo, f.leftover_hi, lambda x: num(x, 0))
        front = Tile(
            "On track to have",
            num(f.leftover_p50, 0),
            f"extra meal exchanges on {end}\nlikely {likely} · {odds} chance they last",
            color,
            "large",
        )
    else:
        color = RED
        early = (cfg.semester_end - f.runout_p50).days
        front = Tile(
            "Meal exchanges run out",
            day(f.runout_p50),
            f"likely {span(f.runout_lo, f.runout_hi)} · {plural(early, 'day')} early\n{odds} chance they last to {end}",
            color,
            "large",
        )
    seen = int(d.forecast.profile.attrs.get("observed_days", 0))
    back = Tile(
        f"Chance they last to {end}",
        odds,
        f"from {plural(seen, 'observed day')} — each day of the week forecast from its own history",
        color,
    )
    return replace(front, back=back)


def _plan_tile(d: PageData) -> Tile | None:
    """Today's share of the daily plan, with the next seven days on the back."""
    plan, ref = d.plan, d.cfg.as_of
    if plan is None or plan.empty or ref > plan.index[-1]:
        return None

    def words(on: date, noun: bool = True) -> str | None:
        """What the plan says for one day: "2 meals", "no meals", "away" (or "2", "none" without the noun)."""
        if on not in plan.index:
            return None
        row = plan.loc[on]
        if bool(row["away"]):
            return "away"
        meals = int(row["meals"])
        if not noun:
            return str(meals) if meals else "none"
        return plural(meals, "meal") if meals else "no meals"

    first = plan.index[0]
    now = words(ref)
    nxt = words(ref + timedelta(days=1), noun=now in (None, "away"))  # "3 meals" today reads on as "then 1 tomorrow"
    if now is not None:
        front = Tile("Today's plan", now, f"then {nxt} tomorrow" if nxt else "the last day of the semester", BLUE, "wide")
    elif nxt is not None:
        front = Tile("Tomorrow's plan", nxt, "today is already in the data", BLUE, "wide")
    else:
        front = Tile(f"The plan starts {day(first)}", words(first), "that day", BLUE, "wide")

    week = plan.loc[[x for x in plan.index if x >= max(ref, first)][:7]]
    if len(week) < 2:
        return front
    strip = " ".join("–" if away else str(int(n)) for away, n in zip(week["away"], week["meals"]))
    total = int(week["meals"].sum())
    pots = "exchanges + dining dollars" if plan.attrs.get("dd_meals") else "exchanges"
    surplus = int(plan.attrs.get("surplus", 0))
    note = f"{plural(surplus, 'meal')} won't fit by {day(d.cfg.semester_end)}" if surplus else f"{plural(total, 'meal')} · {pots}"
    back = Tile(f"{week.index[0]:%a} to {week.index[-1]:%a}, day by day", strip, note, NAVY)
    return replace(front, back=back)


def _calendar_tiles(d: PageData) -> list[Tile]:
    """The next away period and the next calendar marker (reading days, exams)."""
    cfg, a = d.cfg, d.metrics.day_counts.a
    tiles = []

    def when(start: date, soon: str, now: str) -> str:
        until = (start - a).days
        return f"{soon}in {plural(until, 'day')}" if until > 0 else now

    upcoming = sorted((p for p in cfg.away_periods if p.enabled and p.end >= a), key=lambda p: p.start)
    if cfg.exclude_away and upcoming:
        p = upcoming[0]
        tiles.append(Tile(clip(p.name, 30), span(p.start, p.end), when(p.start, "away · ", "away now"), NAVY, "wide"))
    markers = sorted((k for k in cfg.markers if k.end >= a), key=lambda k: k.start)
    if markers:
        k = markers[0]
        tiles.append(Tile(clip(k.name, 30), span(k.start, k.end), when(k.start, "", "happening now"), PURPLE, "wide"))
    return tiles


def _right_now(d: PageData) -> list[Tile]:
    cfg, dc, bal = d.cfg, d.metrics.day_counts, d.metrics.balances
    days_left = dc.dr_away if cfg.exclude_away else dc.dr
    usable = "usable " if cfg.exclude_away and dc.away_rem else ""
    dd_meals = cfg.round_meals(bal.dd_left / cfg.meal_price) if cfg.meal_price > 0 else 0
    tiles = [_verdict(d)]
    plan = _plan_tile(d)
    if plan is not None:
        tiles.append(plan)
    tiles += [
        Tile("", num(bal.me_left, 0), "exchanges left", BLUE),
        Tile("", money(bal.dd_left), f"dining dollars\n≈ {plural(dd_meals, 'meal')}", TEAL),
        Tile("", f"{d.metrics.progress * 100:.0f}%", f"of the semester done · {days_left} {usable}days to go", NAVY, "wide",
             bar=d.metrics.progress),
    ]
    return tiles + _calendar_tiles(d)


def _projections(d: PageData) -> list[Tile]:
    cfg, f, metrics = d.cfg, d.forecast.dd, d.metrics
    v = "away" if cfg.exclude_away else "plain"
    end = day(cfg.semester_end)
    tiles: list[Tile] = []

    if metrics.balances.dd_used <= 0:
        tiles.append(Tile("Dining dollars", "untouched", "no spending yet to project from", SLATE, "wide"))
    elif not f.available or len(f.expected) <= 1:
        pass  # nothing to forecast: no history, or the semester is over
    elif f.start_balance <= 0:
        tiles.append(Tile("Dining dollars", "$0", "all spent", RED, "wide"))
    elif f.prob_lasts >= 0.5 or f.runout_p50 is None:
        likely = _between(f.leftover_lo, f.leftover_hi, money)
        tiles.append(
            Tile("Dining dollars will hold", chance(f.prob_lasts), f"chance of lasting · likely {likely} left on {end}",
                 GREEN if f.prob_lasts >= SAFE_CHANCE else ORANGE, "wide")
        )
        if f.leftover_p50 >= cfg.dd_leftover_flag:
            color = PURPLE if cfg.dd_rollover else ORANGE
            fate = "carries into spring" if cfg.dd_rollover else "goes unspent — use it or lose it"
            back = None
            if cfg.meal_price > 0 and f.leftover_p50 >= cfg.meal_price:
                worth = int(f.leftover_p50 // cfg.meal_price)
                back = Tile("That is enough for", plural(worth, "meal"), f"at {cents(cfg.meal_price).removesuffix('.00')} each", color)
            tiles.append(Tile(f"Left over on {end}", money(f.leftover_p50), fate, color, "wide", back=back))
    else:
        tiles.append(
            Tile("Dining dollars run out", day(f.runout_p50),
                 f"likely {span(f.runout_lo, f.runout_hi)} · {chance(f.prob_lasts)} chance of lasting", RED, "wide")
        )

    for label, key in (("exchanges only", "me_only"), ("with dining dollars", "with_dd")):
        ro = metrics.run_out[v][key]
        if ro.date is None:
            continue
        short = ro.spare_days is not None and ro.spare_days < 0
        tail = f"{num(abs(ro.spare_days), 0)} days {'short' if short else 'spare'}"
        tiles.append(Tile("Two meals every day lasts until", day(ro.date), f"{label} · {tail}", CRIMSON if short else GREEN, "wide"))
    return tiles


def _spend_tile(d: PageData) -> Tile | None:
    """The spend-down advice as a sentence, with the weekly numbers on the back."""
    sd = d.spend
    if sd.reason is not None or not sd.sentence:
        return None
    sentences = tuple(s for s in re.split(r"(?<=\.)\s+", sd.sentence.strip()) if s)
    front = Tile("To finish at zero", "", "", ORANGE, "large", lines=_fit_sentences(list(sentences), 8))
    if sd.usable_days < budget.SHORT_HORIZON_DAYS:
        return front
    exchanges, dd_meals = int(sd.exchanges_per_week + 0.5), int(sd.dd_meals_per_week + 0.5)
    if exchanges and dd_meals:
        value, what = f"{exchanges} + {dd_meals}", "exchanges + dining-dollar meals"
    elif exchanges:
        value, what = str(exchanges), "exchange" if exchanges == 1 else "exchanges"
    elif dd_meals:
        value, what = str(dd_meals), "dining-dollar meal" if dd_meals == 1 else "dining-dollar meals"
    else:
        return front
    if sd.snack_reserve >= budget.SNACK_MENTION_DOLLARS:
        what += f"\n{money(sd.snack_reserve)} kept back for snacks"
    return replace(front, back=Tile(f"Every week until {day(sd.end)}", value, what, ORANGE))


def _pace(d: PageData) -> list[Tile]:
    cfg, metrics = d.cfg, d.metrics
    v = "away" if cfg.exclude_away else "plain"
    me_burn, dd_burn = metrics.burn[v]["me"], metrics.burn[v]["dd"]
    targets, pace = metrics.targets[v], metrics.pace[v]
    tiles: list[Tile] = []
    spend = _spend_tile(d)
    if spend is not None:
        tiles.append(spend)
    if pace.me_only is not None:
        tiles.append(Tile("You can eat", num(pace.me_only, 2), "meals a day on exchanges alone", VIOLET, "wide"))
        tiles.append(Tile("or", num(pace.with_dd, 2), "meals a day counting dining dollars", MAGENTA, "wide"))
    for delta, label in ((targets.strict_delta, "exchanges only"), (targets.pooled_delta, "counting dining dollars")):
        if delta is not None:
            ahead = delta >= 0
            tiles.append(
                Tile("Against an even pace", f"{delta:+.1f}", f"meals {'ahead' if ahead else 'behind'} · {label}", GREEN if ahead else RED, "wide")
            )
    mix = metrics.day_mix[v]["with_dd"]
    if mix.two_per_week is not None:
        tiles.append(
            Tile("A week from here looks like", f"{num(mix.two_per_week)} + {num(mix.one_per_week)}", "two-meal days + one-meal days", BLUE, "wide")
        )
    if me_burn.rate is not None:
        tiles.append(Tile("", num(me_burn.rate, 2), "exchanges a day so far", NAVY))
    if dd_burn.rate is not None:
        tiles.append(Tile("", cents(dd_burn.rate), "a day in dining dollars", TEAL))
    return tiles


def _weekdays(d: PageData) -> list[Tile]:
    """What each day of the week has looked like so far — the pattern the forecast and plan lean on."""
    profile = d.forecast.profile
    seen = profile[profile["days"] > 0]
    if seen.empty or float(seen["meals_avg"].max()) <= 0:
        return []
    tiles: list[Tile] = []
    big, small = seen["meals_avg"].idxmax(), seen["meals_avg"].idxmin()
    if seen.loc[big, "meals_avg"] > seen.loc[small, "meals_avg"]:
        def fact(label: str, wd: int, color: str) -> Tile:
            return Tile(label, profile.loc[wd, "weekday"][:3], f"{num(profile.loc[wd, 'meals_avg'])} a day", color)

        tiles.append(replace(fact("Biggest day", big, MAGENTA), back=fact("Lightest day", small, PURPLE)))

    spends = float(seen["dd_spend_avg"].max()) > 0
    today_wd = d.cfg.as_of.weekday()
    for wd in range(7):
        row = profile.loc[wd]
        name = f"{row['weekday'][:3]} · today" if wd == today_wd else row["weekday"]
        if int(row["days"]) == 0:
            tiles.append(Tile(name, "—", "not seen yet", SLATE))
            continue
        meals = num(row["meals_avg"])
        back = Tile(name, cents(row["dd_spend_avg"]), "dining $ a day", TEAL) if spends else None
        tiles.append(Tile(name, meals, "meal a day" if meals == "1" else "meals a day", BLUE if wd == today_wd else NAVY, back=back))
    return tiles


def _weeks(d: PageData) -> list[Tile]:
    """Weekly recap: the headline count on the front, the recap's other sentences on the back."""
    this_monday = d.cfg.as_of - timedelta(days=d.cfg.as_of.weekday())
    tiles = []
    for r, color in zip(d.recaps, (BLUE, VIOLET)):
        if r.reason is not None or not r.lines:
            continue
        if r.week_start == this_monday:
            label = "This week so far" if r.in_progress else "This week"
        elif r.week_start == this_monday - timedelta(days=7):
            label = "Last week"
        else:
            label = f"Week of {day(r.week_start)}"
        versus = r.lines[0].rstrip(".").partition(", ")[2]  # "3 fewer than the same days last week"
        if r.week_start != this_monday:  # the recap calls its newest week "this week"; here that week is already past
            versus = versus.replace("last week", "the week before")
        post = ("meal" if r.meals == 1 else "meals") + (f"\n{versus}" if versus else "")
        details = _fit_sentences(list(r.lines[1:]))
        back = Tile(label, "", "", color, lines=details) if details else None
        tiles.append(Tile(label, str(r.meals), post, color, "large", back=back))
    return tiles


def _spot_tile(d: PageData) -> Tile | None:
    swiped = d.places[d.places["swipes"] > 0] if not d.places.empty else d.places
    if swiped.empty:
        return None
    place = swiped["swipes"].astype(float).idxmax()
    row = swiped.loc[place]
    usual = habits.format_hour(round(float(row["typical_hour"]) * 4) / 4 % 24)
    weekday = forecast.WEEKDAY_NAMES[int(row["favorite_weekday_num"])]
    back = Tile("You're usually there around", usual, f"most often on a {weekday}", PURPLE)
    return Tile("Your most-swiped spot", clip(str(place), 16), plural(int(row["swipes"]), "swipe"), PURPLE, "wide", back=back)


def _late_tile(d: PageData) -> Tile | None:
    late = d.late
    if late.count <= 0:
        return None
    after = f"After {habits.format_hour(late.start_hour % 24)}"
    if late.dd_dollars > 0:
        share = f"{late.dd_share * 100:.0f}% of your dining dollars · " if late.dd_share is not None else ""
        front = Tile(f"{after} you've spent", money(late.dd_dollars), f"{share}{plural(late.nights, 'late night')}", VIOLET, "wide")
    else:
        front = Tile(after, str(late.count), f"late-night {'meal' if late.count == 1 else 'meals'} on {plural(late.nights, 'night')}", VIOLET, "wide")
    if not late.top_place:
        return front
    back = Tile("Late-night favorite", clip(late.top_place, 16), f"{late.top_place_count} of {plural(late.count, 'late visit')}", VIOLET)
    return replace(front, back=back)


def _streak_tile(d: PageData) -> Tile | None:
    st = d.streaks
    if st.reason is not None or st.meal_streak.length <= 0:
        return None
    run = st.meal_streak
    note = span(run.start, run.end)
    if st.current.length >= 2 and st.current.end != run.end:
        note += f" · {st.current.length} in a row right now"
    elif st.gap.length > 0:
        note += f" · longest gap {plural(st.gap.length, 'day')}"
    front = Tile("Longest run of days with a meal", plural(run.length, "day"), note, GREEN, "wide")
    same = st.same_place
    if same.length < 2 or not same.place:
        return front
    back = Tile("Most days in a row at one place", plural(same.length, "day"), f"{clip(same.place, 10)} · {span(same.start, same.end)}", GREEN)
    return replace(front, back=back)


def _compare_tile(d: PageData) -> Tile | None:
    """This semester's daily rate against the most recent past semester the ledger knows about."""
    if not d.summaries:
        return None
    current = d.summaries[-1]
    past = [s for s in d.summaries[:-1] if s.covered_start is not None]
    if current.covered_start is None or not past:
        return None
    prev = max(past, key=lambda s: s.end)
    for pot, label in (("ME", "Meal exchanges"), ("DD", "Dining dollars")):
        now, then = current.pots[pot], prev.pots[pot]
        if now is None or then is None or now.per_day is None or not then.per_day or then.per_day <= 0:
            continue
        change = round((now.per_day / then.per_day - 1) * 100)
        value = "the same" if change == 0 else f"{abs(change)}% {'more' if change > 0 else 'less'}"
        if pot == "ME":
            post = f"{num(now.per_day, 2)} swipes a day now · {num(then.per_day, 2)} then"
        else:
            post = f"{cents(now.per_day)} a day now · {cents(then.per_day)} then"
        if prev.covered_days < prev.total_days - compare.COVERAGE_NOTE_SLACK_DAYS:
            post += f"\nfrom {prev.covered_days} of {prev.total_days} days on record"
        back = None
        if prev.top_places:
            top = prev.top_places[0]
            back = Tile(clip(f"{prev.name} favorite", 30), clip(top.place, 16), plural(top.visits, "visit"), MAGENTA)
        return Tile(clip(f"{label} vs {prev.name}", 30), value, post, MAGENTA, "wide", back=back)
    return None


def _habits(d: PageData) -> list[Tile]:
    tiles = [t for t in (_spot_tile(d), _late_tile(d), _streak_tile(d), _compare_tile(d)) if t is not None]
    if not d.daily.empty:
        tiles.append(Tile("", num(d.daily["meals"].tail(7).sum(), 0), "meals, last 7 days", NAVY))
    bd = d.metrics.dd_breakdown
    if bd.snack_count:
        tiles.append(Tile("", money(bd.snack_amount), f"on {plural(bd.snack_count, 'snack')}", ORANGE))
    if bd.meal_count:
        tiles.append(Tile("", money(bd.meal_amount), f"on {plural(bd.meal_count, 'DD meal')}", TEAL))
    if d.through is not None:
        age = (d.today - d.through).days
        tiles.append(Tile("Data through", day(d.through), ago(age), SLATE if age <= 3 else RED))
    return tiles


def build_groups(data: PageData) -> list[Group]:
    """Tile groups for the current away setting (`cfg.exclude_away`); empty groups are left out."""
    groups = [
        Group("right now", _right_now(data)),
        Group("projections", _projections(data)),
        Group("pace", _pace(data)),
        Group("day by day", _weekdays(data)),
        Group("this week", _weeks(data)),
        Group("habits", _habits(data)),
    ]
    return [g for g in groups if g.tiles]


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

CSS = """
<style>
.stApp { background: #1B0E45; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { max-width: none; padding-top: 2.5rem; }
.metro { container-type: inline-size; color: #fff;
  font-family: "Segoe UI", "Segoe WP", "Selawik", "Open Sans", "Helvetica Neue", system-ui, sans-serif; }
.metro .top { display: flex; align-items: baseline; justify-content: space-between; margin: 0 0 28px; }
.metro h1 { font-weight: 200; font-size: 58px; line-height: 1; letter-spacing: -1px; margin: 0; padding: 0; color: #fff; }
.metro .asof { font-weight: 300; font-size: 18px; opacity: .85; text-align: right; }
/* tile unit sized so two 4-column groups sit side by side when the page is wide enough */
.metro .groups { --u: clamp(104px, calc((100cqw - 56px - 48px) / 8), 150px);
  display: flex; flex-wrap: wrap; gap: 36px 56px; }
.metro h2 { font-weight: 300; font-size: 20px; margin: 0 0 10px; padding: 0; color: #fff; opacity: .9; }
.metro .grid { display: grid; grid-template-columns: repeat(4, var(--u)); grid-auto-rows: var(--u);
  grid-auto-flow: dense; gap: 8px; }
/* every size inside a tile follows --u, so a tile looks the same (and fits the same text) at any size */
.metro .tile { position: relative; overflow: hidden; border-radius: 0; perspective: calc(var(--u) * 9);
  --cap: clamp(11.5px, calc(var(--u) * .092), 15px);
  transition: transform .12s; animation: metro-in .5s cubic-bezier(.1,.9,.2,1) both;
  animation-delay: calc(var(--i) * 28ms); }
.metro .tile::after { content: ""; position: absolute; inset: 0; border: 3px solid transparent;
  pointer-events: none; transition: border-color .12s; }
.metro .tile:hover::after { border-color: rgba(255,255,255,.45); }
.metro .tile:active { transform: scale(.975); }
.metro .wide { grid-column: span 2; }
.metro .large { grid-column: span 2; grid-row: span 2; }
.metro .face { position: absolute; inset: 0; box-sizing: border-box; display: flex; flex-direction: column;
  justify-content: flex-end; overflow: hidden;
  padding: calc(var(--u) * .08) calc(var(--u) * .09) calc(var(--u) * .09); }
.metro .large .face { padding: calc(var(--u) * .11) calc(var(--u) * .12) calc(var(--u) * .12); }
.metro .pre, .metro .post, .metro .say, .metro .val.long { display: -webkit-box; -webkit-box-orient: vertical;
  overflow: hidden; overflow-wrap: anywhere; }
.metro .pre, .metro .val, .metro .post, .metro .say, .metro .bar { flex: none; }
.metro .c1 { -webkit-line-clamp: 1; } .metro .c2 { -webkit-line-clamp: 2; } .metro .c3 { -webkit-line-clamp: 3; }
.metro .c4 { -webkit-line-clamp: 4; } .metro .c5 { -webkit-line-clamp: 5; } .metro .c6 { -webkit-line-clamp: 6; }
.metro .c7 { -webkit-line-clamp: 7; } .metro .c8 { -webkit-line-clamp: 8; } .metro .c9 { -webkit-line-clamp: 9; }
.metro .pre { margin-bottom: auto; font-size: var(--cap); font-weight: 400; line-height: 1.22; }
.metro .post { margin-top: calc(var(--u) * .03); font-size: var(--cap); font-weight: 400; line-height: 1.22; }
.metro .val { font-weight: 200; font-size: calc(var(--u) * .36); line-height: 1.02; letter-spacing: -.02em;
  white-space: nowrap; font-variant-numeric: tabular-nums; }
.metro .val.v2 { font-size: calc(var(--u) * .28); }
.metro .val.v3 { font-size: calc(var(--u) * .22); font-weight: 300; letter-spacing: 0; }
.metro .val.long { font-size: calc(var(--u) * .17); font-weight: 300; letter-spacing: 0; line-height: 1.1;
  white-space: normal; -webkit-line-clamp: 2; }
.metro .wide .val.long { font-size: calc(var(--u) * .2); }
.metro .large .pre { font-size: clamp(14px, calc(var(--u) * .147), 22px); font-weight: 300; line-height: 1.2; }
.metro .large .val { font-size: calc(var(--u) * .78); line-height: 1; letter-spacing: -.035em; }
.metro .large .val.v2 { font-size: calc(var(--u) * .56); }
.metro .large .val.v3 { font-size: calc(var(--u) * .42); font-weight: 200; letter-spacing: -.02em; }
.metro .large .val.long { font-size: calc(var(--u) * .26); line-height: 1.1; letter-spacing: 0; -webkit-line-clamp: 3; }
.metro .large .post { margin-top: calc(var(--u) * .05); font-size: clamp(12.5px, calc(var(--u) * .127), 19px);
  font-weight: 300; line-height: 1.25; }
.metro .say { font-size: clamp(13px, calc(var(--u) * .135), 20px); font-weight: 300; line-height: 1.3; }
.metro .say span { display: block; }
.metro .say span + span { margin-top: .3em; }
.metro .bar { height: calc(var(--u) * .04); background: rgba(255,255,255,.28); margin-top: calc(var(--u) * .06); }
.metro .bar i { display: block; height: 100%; background: #fff; }
/* live tiles: two faces back to back on a card that turns over, then turns back */
.metro .flip { position: absolute; inset: 0; transform-style: preserve-3d;
  animation: metro-flip var(--t, 20s) cubic-bezier(.45,0,.2,1) var(--d, 3s) infinite; }
.metro .live .face { -webkit-backface-visibility: hidden; backface-visibility: hidden; }
.metro .live .front { transform: rotateX(0deg); }
.metro .live .back { transform: rotateX(180deg); }
.metro .tile:hover .flip { animation-play-state: paused; }
@keyframes metro-in { from { opacity: 0; transform: translateX(46px); } to { opacity: 1; transform: none; } }
@keyframes metro-flip { 0%, 46% { transform: rotateX(0deg); } 50%, 96% { transform: rotateX(180deg); }
  100% { transform: rotateX(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .metro .tile { animation: none; transition: none; }
  .metro .flip { animation: none; }
}
@container (max-width: 935px) { .metro .groups { --u: clamp(96px, calc((100cqw - 24px) / 4), 150px); } }
@container (max-width: 430px) {
  .metro .groups { --u: calc((100cqw - 8px) / 2); }
  .metro .grid { grid-template-columns: repeat(2, var(--u)); }
  .metro h1 { font-size: 44px; }
}
</style>
"""

#: Seconds for a full front-back-front cycle; consecutive live tiles take different ones.
FLIP_PERIODS = (17, 23, 15, 25, 20)


def flip_timing(k: int) -> tuple[int, float]:
    """(period, first-flip delay) in seconds for the k-th live tile, so no two neighbours flip together."""
    return FLIP_PERIODS[k % len(FLIP_PERIODS)], round(2.5 + (k * 3.7) % 10, 1)


def _lines_html(text: str) -> str:
    return "<br>".join(escape(part) for part in text.split("\n"))


def _face_html(face: Tile, size: str, cls: str = "", style: str = "") -> str:
    lay = _layout(face, size)
    parts = []
    if face.pre and lay.pre:
        parts.append(f'<div class="pre c{lay.pre}">{escape(face.pre)}</div>')
    if face.lines:
        spans = "".join(f"<span>{escape(line)}</span>" for line in face.lines)
        parts.append(f'<div class="say c{lay.say}">{spans}</div>')
    else:
        value_cls = f" {lay.value_cls}" if lay.value_cls else ""
        parts.append(f'<div class="val{value_cls}">{escape(face.value)}</div>')
        if face.post:
            parts.append(f'<div class="post c{lay.post}">{_lines_html(face.post)}</div>')
    if face.bar is not None:
        parts.append(f'<div class="bar"><i style="width:{max(0.0, min(1.0, face.bar)) * 100:.1f}%"></i></div>')
    style_attr = f' style="{style}"' if style else ""
    return f'<div class="face{cls}"{style_attr}>{"".join(parts)}</div>'


def render_html(groups: list[Group], title: str, subtitle: str) -> str:
    out = [CSS, f'<div class="metro"><div class="top"><h1>{escape(title)}</h1><div class="asof">{escape(subtitle)}</div></div>']
    out.append('<div class="groups">')
    i = live = 0
    for g in groups:
        out.append(f'<section class="group"><h2>{escape(g.title)}</h2><div class="grid">')
        for t in g.tiles:
            if t.back is None:
                out.append(f'<div class="tile {t.size}" style="background:{t.color};--i:{i}">{_face_html(t, t.size)}</div>')
            else:
                period, delay = flip_timing(live)
                live += 1
                front = _face_html(t, t.size, " front", f"background:{t.color}")
                back = _face_html(t.back, t.size, " back", f"background:{t.back.color}")
                out.append(
                    f'<div class="tile {t.size} live" style="--i:{i};--t:{period}s;--d:{delay}s">'
                    f'<div class="flip">{front}{back}</div></div>'
                )
            i += 1
        out.append("</div></section>")
    out.append("</div></div>")
    return "".join(out)


def build_page_html(cfg: Config, df: pd.DataFrame, today: date) -> str:
    """Everything the Tiles page shows, as one HTML string. app.py calls only this."""
    data = gather(cfg, df, today)
    a = data.metrics.day_counts.a
    return render_html(build_groups(data), "Dining", f"as of {a:%A, %b} {a.day}")
