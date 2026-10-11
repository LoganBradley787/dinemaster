"""Metro-style (Windows 8 Start screen) tiles: plain-sentence verdicts over the app's numbers.

The web app's Start screen (`dinemaster/web/app.py`) builds its tiles in three steps:

    gather        collects everything the tiles show (metrics, the day-of-week forecast, the daily
                  plan and spend-down, weekly recaps, habits, semester comparison) into `PageData`.
    build_groups  turns that into `Group`s of `Tile`s — pure text and meanings (`palette.py` holds the colours), easy to test.
    groups_html   turns tiles into markup. The styles are in `dinemaster/web/static/metro.css`.

Live tiles: a `Tile` may carry a `back` face. The page flips between the two faces on a timer with
CSS only, no script: each live tile gets its own period and delay so they never
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
from .palette import Meaning, need_meaning
from .freshness import at_observed, data_through, days_behind, is_stale, observed_through

# A tile names what it means; palette.py turns that into a colour. The rule:
#   it judges something      -> GOOD, LOOK (worth a look), PROBLEM, UNKNOWN (not enough data yet)
#   otherwise, by the number -> MEALS (meals and exchanges), DOLLARS, DAYS (days and dates), PLACES
# A front and a back face are named separately when they measure different things. A face with
# no big number (sentences) takes the subject of the sentences.
MEALS, DOLLARS, DAYS, PLACES = Meaning.MEALS, Meaning.DOLLARS, Meaning.DAYS, Meaning.PLACES
GOOD, LOOK, PROBLEM, UNKNOWN = Meaning.GOOD, Meaning.LOOK, Meaning.PROBLEM, Meaning.UNKNOWN

SAFE_CHANCE = forecast.SAFE_CHANCE


@dataclass(frozen=True)
class Tile:
    """One tile face: a small line, a big value, a small line. `back` makes it a live tile."""

    pre: str  # small line above the number
    value: str  # the big number / date
    post: str  # small line below ("\n" starts a new line)
    kind: Meaning  # what the face means (see palette.py); the colour follows from it
    size: str = "sq"  # sq (1x1) | wide (2x1) | large (2x2); a back face uses its front's size
    bar: float | None = None  # optional 0..1 progress bar
    lines: tuple[str, ...] = ()  # sentences shown instead of a big value (large tiles only)
    back: Tile | None = None  # second face of a live tile
    today: bool = False  # marks the tile for today with a white top edge (not with a colour)

    def __post_init__(self):
        need_meaning(self.kind)  # a hex string or any other colour is a TypeError

    @property
    def color(self) -> str:
        return self.kind.fill


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
    metrics: m.Metrics  # worked out at the last observed day, so uncovered days are not zero-usage days
    now: m.DayCounts  # the calendar at the real as-of date: progress, days to go, what is coming up
    behind: int  # days between the last observed day and the as-of date
    stale: bool  # the data is too old for "today" advice
    daily: pd.DataFrame  # metrics.daily through the last observed day
    through: date | None  # last day the exports cover
    forecast: forecast.Forecast
    plan: pd.DataFrame | None  # budget.daily_budget from today (None when the data is stale)
    spend: budget.SpendDown
    ladder: budget.Ladder
    gaps: forecast.SwipeGaps | None
    recaps: list[recap.Recap]  # newest week first, at most two
    streaks: habits.Streaks
    late: habits.LateNight
    places: pd.DataFrame  # habits.place_stats
    summaries: list[compare.SemesterSummary]


# ---------------------------------------------------------------------------
# small text helpers
# ---------------------------------------------------------------------------


def _no_negative_zero(x: float, digits: int) -> float:
    """Round for display. A swipe and its reversal net to -0.0, which would print as "-0"."""
    return round(x, digits) + 0.0


def num(x: float, digits: int = 1) -> str:
    """Compact number: one decimal by default, without a trailing .0."""
    x = _no_negative_zero(x, digits)
    return f"{x:,.{digits}f}".removesuffix("." + "0" * digits) if digits else f"{x:,.0f}"


def money(x: float) -> str:
    return f"${_no_negative_zero(x, 0):,.0f}"


def cents(x: float) -> str:
    return f"${_no_negative_zero(x, 2):,.2f}"


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


_DATE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2})\b")
NBSP = "\u00a0"


def keep_dates(text: str) -> str:
    """`text` with a non-breaking space inside each date, so "Dec 18" never wraps after "Dec"."""
    return _DATE.sub(rf"\1{NBSP}\2", text)


def plural(n: float, noun: str) -> str:
    return f"{num(n, 0)} {noun}" if round(n) == 1 else f"{num(n, 0)} {noun}s"


def _between(lo: float, hi: float, fmt) -> str:
    a, b = fmt(lo), fmt(hi)
    return a if a == b else f"{a}–{b}"


# ---------------------------------------------------------------------------
# fitting text into tiles
# ---------------------------------------------------------------------------

# Rough advance widths in em (Helvetica-like, a little generous so estimates err on the wide side).
_NARROW, _SLIM, _WIDE = set(" .,:;'|!iIjl·" + NBSP), set("frt()-/"), set("mwMW%—")


def em(text: str) -> float:
    """Estimated width of `text` in em units."""
    total = 0.0
    for ch in text:
        total += 0.28 if ch in _NARROW else 0.34 if ch in _SLIM else 0.9 if ch in _WIDE else 0.7 if ch.isupper() else 0.56
    return total


def text_lines(text: str, width_em: float) -> int:
    """How many lines `text` takes when wrapped to `width_em` ("\\n" forces a break; 0 for no text).

    A date counts as one word, as `keep_dates` renders it.
    """
    if not text:
        return 0
    count = 0
    for paragraph in keep_dates(text).split("\n"):
        count += 1
        used = 0.0
        for word in paragraph.split(" "):
            if not word:
                continue
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
    pre_need = text_lines(face.pre, _pre_em(face, size))
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


def _pre_em(face: Tile, size: str) -> float:
    """Width of the top line in em. A face that judges gives up its top right corner to its sign."""
    g = GEOMETRY[size]
    return g.inner / g.pre_px - (1.5 if face.kind.judges else 0.0)


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
        pre = text_lines(face.pre, _pre_em(face, tile.size))
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
    """Run every module the page draws on. Usage is only known through the observed day O.

    When the data is a few days behind, the plan still starts today: the days in between are
    assumed to have gone at the usual rate. Past `freshness.STALE_AFTER_DAYS` there is no plan.
    """
    o = observed_through(cfg, df)
    at_o = at_observed(cfg, df)
    fc = forecast.forecast(df, cfg)
    stale = is_stale(cfg, df)
    known = o + timedelta(days=1)
    start = max(known, m.clamp_as_of(cfg))
    me_left, dd_left = forecast.balances_on(fc, start)
    expected_unused = float(fc.me.expected.iloc[-1]) if fc.me.available else fc.me.start_balance
    weeks = recap.weeks_available(df, cfg)
    return PageData(
        cfg=cfg,
        df=df,
        today=today,
        metrics=m.compute_metrics(df, at_o),
        now=m.day_counts(cfg),
        behind=days_behind(cfg, df),
        stale=stale,
        daily=m.daily(df, at_o),
        through=data_through(cfg, df),
        forecast=fc,
        plan=None if stale else budget.daily_budget(cfg, me_left, dd_left, fc.profile, start, include_dd=not cfg.dd_rollover),
        spend=budget.spend_down(cfg, fc.me.start_balance, fc.dd.start_balance, fc.profile, known),
        ladder=budget.rate_ladder(cfg, fc.me.start_balance, expected_unused, known),
        gaps=forecast.swipe_gaps(df, cfg),
        recaps=[recap.weekly_recap(df, cfg, monday) for monday in reversed(weeks[-2:])],
        streaks=habits.streaks(df, cfg),
        late=habits.late_night(df, cfg),
        places=habits.place_stats(df, cfg),
        summaries=compare.semester_summaries(df, cfg),
    )


def run_out_matters(d: PageData) -> bool:
    """Whether to show the "when will it run out" tiles (`[tiles] run_out` in config.toml).

    "auto" follows the forecast: the tiles come back by themselves once either pot has less
    than a `SAFE_CHANCE` chance of lasting.
    """
    mode = d.cfg.run_out_tiles
    if mode in ("always", "never"):
        return mode == "always"
    return forecast.at_risk(d.forecast.me) or forecast.at_risk(d.forecast.dd)


def going_unused(d: PageData) -> bool:
    return forecast.going_unused(d.cfg, d.forecast.me)


def meal_run_out_matters(d: PageData) -> bool:
    """Whether to show the tiles about making meals last ("you can eat", "two meals every day").

    As `run_out_matters`, except that in "auto" they stay hidden while exchanges are going
    unused: dining dollars running short is no reason to ration meals then.
    """
    if d.cfg.run_out_tiles != "auto":
        return run_out_matters(d)
    return run_out_matters(d) and not going_unused(d)


# ---------------------------------------------------------------------------
# tile groups
# ---------------------------------------------------------------------------


def _verdict(d: PageData) -> Tile:
    """The big tile: will the meal exchanges last? From the day-of-week forecast."""
    cfg, f = d.cfg, d.forecast.me
    end = day(cfg.semester_end)
    if d.metrics.balances.me_used <= 0:
        return Tile("No meals used yet", "—", "nothing to project from", UNKNOWN, "large")
    if not f.available:
        return Tile("No forecast yet", "—", f.reason or "nothing to project from", UNKNOWN, "large")
    if len(f.expected) <= 1:
        return Tile("The semester ended with", num(max(f.start_balance, 0), 0), "meal exchanges unused", MEALS, "large")
    if f.start_balance <= 0:
        return Tile("Meal exchanges", "0", "all used up", PROBLEM, "large")

    odds = chance(f.prob_lasts)
    if f.prob_lasts >= 0.5 or f.runout_p50 is None:
        likely = _between(f.leftover_lo, f.leftover_hi, lambda x: num(x, 0))
        if going_unused(d):  # safe, but that many expire: the loss is the news, not the safety
            color = LOOK
            front = Tile("On pace to leave", num(f.leftover_p50, 0), f"meal exchanges unused on {end}\nlikely {likely}", color, "large")
        else:
            color = GOOD if f.prob_lasts >= SAFE_CHANCE else LOOK
            front = Tile(
                "On track to have",
                num(f.leftover_p50, 0),
                f"extra meal exchanges on {end}\nlikely {likely} · {odds} chance they last",
                color,
                "large",
            )
    else:
        color = PROBLEM
        early = (cfg.semester_end - f.runout_p50).days
        front = Tile(
            "Meal exchanges run out",
            day(f.runout_p50),
            f"likely {span(f.runout_lo, f.runout_hi)} · {plural(early, 'day')} early\n{odds} chance they last to {end}",
            color,
            "large",
        )
    seen = int(d.forecast.profile.attrs.get("observed_days", 0))
    unused = going_unused(d)  # then lasting is not in doubt; using them up is
    back = Tile(
        f"Chance you use them all by {end}" if unused else f"Chance they last to {end}",
        chance(1 - f.prob_lasts) if unused else odds,
        f"from {plural(seen, 'observed day')} — each day of the week forecast from its own history",
        color,
    )
    return replace(front, back=back)


def _plan_tile(d: PageData) -> Tile | None:
    """Today's share of the daily plan, with the next seven days on the back."""
    plan, ref = d.plan, d.cfg.as_of
    usual = f" · assuming your usual since {day(observed_through(d.cfg, d.df))}" if d.behind > 1 else ""
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
        then = f"then {nxt} tomorrow" if nxt else "the last day of the semester"
        front = Tile("Today's plan", now, then + usual, MEALS, "wide")
    elif nxt is not None:
        front = Tile("Tomorrow's plan", nxt, "today is already in the data", MEALS, "wide")
    else:
        front = Tile(f"The plan starts {day(first)}", words(first), "that day", MEALS, "wide")

    week = plan.loc[[x for x in plan.index if x >= max(ref, first)][:7]]
    if len(week) < 2:
        return front
    strip = " ".join("–" if away else str(int(n)) for away, n in zip(week["away"], week["meals"]))
    total = int(week["meals"].sum())
    pots = "exchanges + dining dollars" if plan.attrs.get("dd_meals") else "exchanges"
    surplus = int(plan.attrs.get("surplus", 0))
    note = f"{plural(surplus, 'meal')} won't fit by {day(d.cfg.semester_end)}" if surplus else f"{plural(total, 'meal')} · {pots}"
    back = Tile(f"{week.index[0]:%a} to {week.index[-1]:%a}, day by day", strip, note, MEALS)
    return replace(front, back=back)


def _calendar_tiles(d: PageData) -> list[Tile]:
    """The next away period and the next calendar marker (reading days, exams)."""
    cfg, a = d.cfg, d.now.a
    tiles = []

    def when(start: date, soon: str, now: str) -> str:
        until = (start - a).days
        return f"{soon}in {plural(until, 'day')}" if until > 0 else now

    upcoming = sorted((p for p in cfg.away_periods if p.enabled and p.end >= a), key=lambda p: p.start)
    if cfg.exclude_away and upcoming:
        p = upcoming[0]
        tiles.append(Tile(clip(p.name, 30), span(p.start, p.end), when(p.start, "away · ", "away now"), DAYS, "wide"))
    markers = sorted((k for k in cfg.markers if k.end >= a), key=lambda k: k.start)
    if markers:
        k = markers[0]
        tiles.append(Tile(clip(k.name, 30), span(k.start, k.end), when(k.start, "", "happening now"), DAYS, "wide"))
    return tiles


def _right_now(d: PageData) -> list[Tile]:
    cfg, dc, bal = d.cfg, d.now, d.metrics.balances
    days_left = dc.dr_away if cfg.exclude_away else dc.dr
    usable = "usable " if cfg.exclude_away and dc.away_rem else ""
    if cfg.dd_rollover:  # dollars that carry over are not meals waiting to be eaten
        dd_note = "dining dollars"
    else:
        dd_meals = cfg.round_meals(bal.dd_left / cfg.meal_price) if cfg.meal_price > 0 else 0
        dd_note = f"dining dollars\n≈ {plural(dd_meals, 'meal')}"
    tiles = []
    if d.stale and d.through is not None:
        tiles.append(Tile("Your data is", f"{d.behind} days old", "get new data before trusting today's numbers", PROBLEM, "wide"))
    tiles.append(_verdict(d))
    plan = _plan_tile(d)
    if plan is not None:
        tiles.append(plan)
    tiles += [
        Tile("", num(bal.me_left, 0), "exchanges left", MEALS),
        Tile("", money(bal.dd_left), dd_note, DOLLARS),
        Tile("", f"{dc.progress * 100:.0f}%", f"of the semester done · {days_left} {usable}days to go", DAYS, "wide",
             bar=dc.progress),
    ]
    return tiles + _calendar_tiles(d)


def _projections(d: PageData) -> list[Tile]:
    cfg, f, metrics = d.cfg, d.forecast.dd, d.metrics
    v = "away" if cfg.exclude_away else "plain"
    end = day(cfg.semester_end)
    tiles: list[Tile] = []
    run_out = run_out_matters(d)

    if metrics.balances.dd_used <= 0:
        tiles.append(Tile("Dining dollars", "untouched", "no spending yet to project from", UNKNOWN, "wide"))
    elif not f.available or len(f.expected) <= 1:
        pass  # nothing to forecast: no history, or the semester is over
    elif f.start_balance <= 0:
        tiles.append(Tile("Dining dollars", "$0", "all spent", PROBLEM, "wide"))
    elif f.prob_lasts >= 0.5 or f.runout_p50 is None:
        likely = _between(f.leftover_lo, f.leftover_hi, money)
        leftover = f.leftover_p50 >= cfg.dd_leftover_flag
        if run_out or not leftover:  # with nothing at risk, the leftover tile below says it all
            tiles.append(
                Tile("Dining dollars will hold", chance(f.prob_lasts), f"chance of lasting · likely {likely} left on {end}",
                     GOOD if f.prob_lasts >= SAFE_CHANCE else LOOK, "wide")
            )
        if leftover:
            color = DOLLARS if cfg.dd_rollover else LOOK  # money that carries over is just an amount
            fate = f"likely {likely} · carries into spring" if cfg.dd_rollover else "goes unspent — use it or lose it"
            back = None
            if not cfg.dd_rollover and cfg.meal_price > 0 and f.leftover_p50 >= cfg.meal_price:
                worth = int(f.leftover_p50 // cfg.meal_price)
                back = Tile("That is enough for", plural(worth, "meal"), f"at {cents(cfg.meal_price).removesuffix('.00')} each", color)
            tiles.append(Tile(f"Left over on {end}", money(f.leftover_p50), fate, color, "wide", back=back))
    else:
        tiles.append(
            Tile("Dining dollars run out", day(f.runout_p50),
                 f"likely {span(f.runout_lo, f.runout_hi)} · {chance(f.prob_lasts)} chance of lasting", PROBLEM, "wide")
        )

    for label, key in (("exchanges only", "me_only"), ("with dining dollars", "with_dd")):
        ro = metrics.run_out[v][key]
        if ro.date is None or not meal_run_out_matters(d) or (key == "with_dd" and cfg.dd_rollover):
            continue
        short = ro.spare_days is not None and ro.spare_days < 0
        tail = f"{num(abs(ro.spare_days), 0)} days {'short' if short else 'spare'}"
        tiles.append(Tile("Two meals every day lasts until", day(ro.date), f"{label} · {tail}", PROBLEM if short else GOOD, "wide"))
    return tiles


def _spend_tile(d: PageData) -> Tile | None:
    """The spend-down advice as a sentence, with the weekly numbers on the back."""
    sd = d.spend
    if sd.reason is not None or not sd.sentence:
        return None
    sentences = tuple(s for s in re.split(r"(?<=\.)\s+", sd.sentence.strip()) if s)
    front = Tile("To finish at zero", "", "", MEALS, "large", lines=_fit_sentences(list(sentences), 8))
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
    return replace(front, back=Tile(f"Every week until {day(sd.end)}", value, what, MEALS))


def _ladder_tile(d: PageData) -> Tile | None:
    """What each weekly rate leaves unused, with the rate that uses them all on the back."""
    ladder = d.ladder
    lines = budget.ladder_lines(ladder)
    if not lines:
        return None
    front = Tile("To use them all", "", "", MEALS, "large", lines=_fit_sentences(lines, 8))
    end = d.spend.end if d.spend.reason is None else d.cfg.semester_end
    back = Tile(f"Every week until {day(end)}", num(ladder.needed_per_week), f"exchanges uses them all\nyou average {num(ladder.usual_per_week)}", MEALS)
    return replace(front, back=back)


def _cost_tile(d: PageData) -> Tile | None:
    """What the exchanges set to expire cost, from the plan price."""
    each, f = d.cfg.exchange_price, d.forecast.me
    if not each or f.leftover_p50 is None or f.leftover_p50 < 1:
        return None
    unused = round(f.leftover_p50)
    front = Tile("Unused exchanges cost about", money(unused * each), f"{unused} at {cents(each)} each", LOOK, "wide")
    used = d.cfg.starting_me - unused
    if used <= 0:
        return front
    back = Tile("Each meal you do eat costs", cents(each * d.cfg.starting_me / used), f"{cents(each)} if you used every one", DOLLARS)
    return replace(front, back=back)


def _gap_tile(d: PageData) -> Tile | None:
    """Which days of the week go without a swipe."""
    g = d.gaps
    if g is None or g.reason is not None:
        return None
    front = Tile(
        "Days with no swipe", f"{g.light_days - g.light_swiped} of {g.light_days}",
        f"{g.light} days · {g.other_days - g.other_swiped} of {g.other_days} {g.other}", DAYS, "wide",
    )
    back = Tile(f"{g.light}, swipes a day", num(g.light_rate, 2), f"{num(g.other_rate, 2)} on {g.other}", MEALS)
    return replace(front, back=back)


def _pace(d: PageData) -> list[Tile]:
    cfg, metrics = d.cfg, d.metrics
    v = "away" if cfg.exclude_away else "plain"
    me_burn, dd_burn = metrics.burn[v]["me"], metrics.burn[v]["dd"]
    targets, pace = metrics.targets[v], metrics.pace[v]
    run_out, unused, pooled = meal_run_out_matters(d), going_unused(d), not cfg.dd_rollover
    tiles: list[Tile] = []
    if unused:
        tiles += [t for t in (_ladder_tile(d), _cost_tile(d), _gap_tile(d)) if t is not None]
    else:
        spend = _spend_tile(d)
        if spend is not None:
            tiles.append(spend)
    if run_out and pace.me_only is not None:
        tiles.append(Tile("You can eat", num(pace.me_only, 2), "meals a day on exchanges alone", MEALS, "wide"))
        if pooled:
            tiles.append(Tile("or", num(pace.with_dd, 2), "meals a day counting dining dollars", MEALS, "wide"))
    delta = targets.strict_delta
    if delta is not None and unused and delta > 0:  # being "ahead" is the problem here
        tiles.append(Tile("Against an even pace", num(delta), "more exchanges left than even use would leave", LOOK, "wide"))
    elif delta is not None:
        ahead = delta >= 0
        tiles.append(
            Tile("Against an even pace", f"{delta:+.1f}", f"meals {'ahead' if ahead else 'behind'} · exchanges only", GOOD if ahead else PROBLEM, "wide")
        )
    if run_out and pooled and targets.pooled_delta is not None:
        ahead = targets.pooled_delta >= 0
        tiles.append(
            Tile("Against an even pace", f"{targets.pooled_delta:+.1f}", f"meals {'ahead' if ahead else 'behind'} · counting dining dollars",
                 GOOD if ahead else PROBLEM, "wide")
        )
    mix = metrics.day_mix[v]["with_dd" if pooled else "me_only"]
    if run_out and mix.two_per_week is not None:
        tiles.append(
            Tile("A week from here looks like", f"{num(mix.two_per_week)} + {num(mix.one_per_week)}", "two-meal days + one-meal days", DAYS, "wide")
        )
    if me_burn.rate is not None:
        tiles.append(Tile("", num(me_burn.rate, 2), "exchanges a day so far", MEALS))
    if dd_burn.rate is not None:
        tiles.append(Tile("", cents(dd_burn.rate), "a day in dining dollars", DOLLARS))
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
        def fact(label: str, wd: int) -> Tile:  # the value is a weekday
            return Tile(label, profile.loc[wd, "weekday"][:3], f"{num(profile.loc[wd, 'meals_avg'])} a day", DAYS)

        tiles.append(replace(fact("Biggest day", big), back=fact("Lightest day", small)))

    spends = float(seen["dd_spend_avg"].max()) > 0
    today_wd = d.cfg.as_of.weekday()
    for wd in range(7):
        row = profile.loc[wd]
        name = f"{row['weekday'][:3]} · today" if wd == today_wd else row["weekday"]
        if int(row["days"]) == 0:
            tiles.append(Tile(name, "—", "not seen yet", UNKNOWN, today=wd == today_wd))
            continue
        meals = num(row["meals_avg"])
        back = Tile(name, cents(row["dd_spend_avg"]), "dining $ a day", DOLLARS) if spends else None
        tiles.append(Tile(name, meals, "meal a day" if meals == "1" else "meals a day", MEALS, back=back, today=wd == today_wd))
    return tiles


def _weeks(d: PageData) -> list[Tile]:
    """Weekly recap: the headline count on the front, the recap's other sentences on the back."""
    this_monday = d.cfg.as_of - timedelta(days=d.cfg.as_of.weekday())
    tiles = []
    for r in d.recaps:
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
        back = Tile(label, "", "", MEALS, lines=details) if details else None
        tiles.append(Tile(label, str(r.meals), post, MEALS, "large", back=back))
    return tiles


def _spot_tile(d: PageData) -> Tile | None:
    swiped = d.places[d.places["swipes"] > 0] if not d.places.empty else d.places
    if swiped.empty:
        return None
    place = swiped["swipes"].astype(float).idxmax()
    row = swiped.loc[place]
    usual = habits.format_hour(round(float(row["typical_hour"]) * 4) / 4 % 24)
    weekday = forecast.WEEKDAY_NAMES[int(row["favorite_weekday_num"])]
    back = Tile("You're usually there around", usual, f"most often on a {weekday}", DAYS)  # a time and a weekday: when, not where
    return Tile("Your most-swiped spot", clip(str(place), 16), plural(int(row["swipes"]), "swipe"), PLACES, "wide", back=back)


def _late_tile(d: PageData) -> Tile | None:
    late = d.late
    if late.count <= 0:
        return None
    after = f"After {habits.format_hour(late.start_hour % 24)}"
    if late.dd_dollars > 0:
        share = f"{late.dd_share * 100:.0f}% of your dining dollars · " if late.dd_share is not None else ""
        front = Tile(f"{after} you've spent", money(late.dd_dollars), f"{share}{plural(late.nights, 'late night')}", DOLLARS, "wide")
    else:
        front = Tile(after, str(late.count), f"late-night {'meal' if late.count == 1 else 'meals'} on {plural(late.nights, 'night')}", MEALS, "wide")
    if not late.top_place:
        return front
    back = Tile("Late-night favorite", clip(late.top_place, 16), f"{late.top_place_count} of {plural(late.count, 'late visit')}", PLACES)
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
    front = Tile("Longest run of days with a meal", plural(run.length, "day"), note, DAYS, "wide")
    same = st.same_place
    if same.length < 2 or not same.place:
        return front
    back = Tile("Most days in a row at one place", plural(same.length, "day"), f"{clip(same.place, 10)} · {span(same.start, same.end)}", DAYS)
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
    for pot, label, kind in (("ME", "Meal exchanges", MEALS), ("DD", "Dining dollars", DOLLARS)):
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
            back = Tile(clip(f"{prev.name} favorite", 30), clip(top.place, 16), plural(top.visits, "visit"), PLACES)
        return Tile(clip(f"{label} vs {prev.name}", 30), value, post, kind, "wide", back=back)
    return None


def _habits(d: PageData) -> list[Tile]:
    tiles = [t for t in (_spot_tile(d), _late_tile(d), _streak_tile(d), _compare_tile(d)) if t is not None]
    if not d.daily.empty:
        last = d.daily.index[-1]
        when = "meals, last 7 days" if d.behind == 0 else f"meals, week to {day(last)}"
        tiles.append(Tile("", num(d.daily["meals"].tail(7).sum(), 0), when, MEALS))
    bd = d.metrics.dd_breakdown
    if bd.snack_count:
        tiles.append(Tile("", money(bd.snack_amount), f"on {plural(bd.snack_count, 'snack')}", DOLLARS))
    if bd.meal_count:
        tiles.append(Tile("", money(bd.meal_amount), f"on {plural(bd.meal_count, 'DD meal')}", DOLLARS))
    if d.through is not None:
        age = (d.today - d.through).days
        tiles.append(Tile("Data through", day(d.through), ago(age), DAYS if age <= 3 else PROBLEM))  # a date, until it is too old
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

#: Seconds for a full front-back-front cycle; consecutive live tiles take different ones.
FLIP_PERIODS = (17, 23, 15, 25, 20)


def flip_timing(k: int) -> tuple[int, float]:
    """(period, first-flip delay) in seconds for the k-th live tile, so no two neighbours flip together."""
    return FLIP_PERIODS[k % len(FLIP_PERIODS)], round(2.5 + (k * 3.7) % 10, 1)


def _lines_html(text: str) -> str:
    return "<br>".join(escape(keep_dates(part)) for part in text.split("\n"))


def _face_html(face: Tile, size: str, cls: str = "", style: str = "") -> str:
    lay = _layout(face, size)
    parts = []
    if face.kind.judges:  # a verdict is never colour alone
        parts.append(f'<i class="sign" aria-hidden="true">{face.kind.sign}</i>')
    if face.pre and lay.pre:
        parts.append(f'<div class="pre c{lay.pre}">{escape(keep_dates(face.pre))}</div>')
    if face.lines:
        spans = "".join(f"<span>{escape(keep_dates(line))}</span>" for line in face.lines)
        parts.append(f'<div class="say c{lay.say}">{spans}</div>')
    else:
        value_cls = f" {lay.value_cls}" if lay.value_cls else ""
        parts.append(f'<div class="val{value_cls}">{escape(face.value)}</div>')
        if face.post:
            parts.append(f'<div class="post c{lay.post}">{_lines_html(face.post)}</div>')
    if face.bar is not None:
        parts.append(f'<div class="bar"><i style="width:{max(0.0, min(1.0, face.bar)) * 100:.1f}%"></i></div>')
    style_attr = f' style="{style}"' if style else ""
    kind_attr = f' data-kind="{face.kind.slug}"' if cls else ""  # a live tile's faces each say what they mean
    return f'<div class="face{cls}"{kind_attr}{style_attr}>{"".join(parts)}</div>'


def groups_html(groups: list[Group], links: dict[str, str] | None = None) -> str:
    """The tile groups as markup, without CSS or a page title (the web app brings both).

    `links` maps a group title to a URL: that group's heading and tiles then link there.
    """
    links = links or {}
    out = ['<div class="groups">']
    i = live = 0
    for g in groups:
        href = links.get(g.title)
        tag, link = ("a", f' href="{escape(href)}"') if href else ("div", "")
        heading = f"<a{link}>{escape(g.title)}</a>" if href else escape(g.title)
        out.append(f'<section class="group"><h2>{heading}</h2><div class="grid">')
        for t in g.tiles:
            today = " today" if t.today else ""
            if t.back is None:
                out.append(
                    f'<{tag}{link} class="tile {t.size}{today}" data-kind="{t.kind.slug}" style="background:{t.color};--i:{i}">'
                    f"{_face_html(t, t.size)}</{tag}>"
                )
            else:
                period, delay = flip_timing(live)
                live += 1
                front = _face_html(t, t.size, " front", f"background:{t.color}")
                back = _face_html(t.back, t.size, " back", f"background:{t.back.color}")
                out.append(
                    f'<{tag}{link} class="tile {t.size} live{today}" style="--i:{i};--t:{period}s;--d:{delay}s">'
                    f'<div class="flip">{front}{back}</div></{tag}>'
                )
            i += 1
        out.append("</div></section>")
    out.append("</div>")
    return "".join(out)
