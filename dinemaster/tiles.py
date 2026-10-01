"""Metro-style (Windows 8 Start screen) tile page: plain-sentence verdicts over the same metrics.

`build_groups` turns Metrics into tile text (pure, tested); `render_html` turns tiles into markup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from html import escape

import pandas as pd

from .config import Config
from .metrics import Metrics

# Metro palette, darkened where needed so white text stays readable
BLUE, NAVY, TEAL, GREEN, RED = "#2672EC", "#2B5797", "#008299", "#008A00", "#E51400"
ORANGE, CRIMSON, PURPLE, VIOLET, MAGENTA, SLATE = "#D24726", "#B91D47", "#7E3878", "#603CBA", "#C1004F", "#525252"


@dataclass(frozen=True)
class Tile:
    pre: str  # small line above the number
    value: str  # the big number / date
    post: str  # small line below
    color: str
    size: str = "sq"  # sq (1x1) | wide (2x1) | large (2x2)
    bar: float | None = None  # optional 0..1 progress bar


@dataclass(frozen=True)
class Group:
    title: str
    tiles: list[Tile]


def num(x: float, digits: int = 1) -> str:
    """Compact number: one decimal by default, without a trailing .0."""
    return f"{x:,.{digits}f}".removesuffix("." + "0" * digits) if digits else f"{x:,.0f}"


def money(x: float) -> str:
    return f"${x:,.0f}"


def day(d: date) -> str:
    return f"{d:%b} {d.day}"


def ago(days: int) -> str:
    return {0: "today", 1: "yesterday"}.get(days, f"{days} days ago")


def build_groups(
    metrics: Metrics, cfg: Config, df: pd.DataFrame, daily: pd.DataFrame, today: date, through: date | None = None
) -> list[Group]:
    """Tile groups for the current away setting (cfg.exclude_away).

    `through` is the last day the exports cover (see freshness.data_through).
    """
    v = "away" if cfg.exclude_away else "plain"
    dc, bal = metrics.day_counts, metrics.balances
    days_left = dc.dr_away if cfg.exclude_away else dc.dr
    me_burn, dd_burn = metrics.burn[v]["me"], metrics.burn[v]["dd"]
    targets, pace = metrics.targets[v], metrics.pace[v]
    end = day(cfg.semester_end)

    # --- right now ---------------------------------------------------------
    if me_burn.rate is None:
        verdict = Tile("No meals used yet", "—", "nothing to project from", SLATE, "large")
    elif me_burn.leftover_raw >= 0:
        verdict = Tile("On track to have", num(me_burn.leftover_raw, 0), f"extra meal exchanges on {end}", GREEN, "large")
    else:
        verdict = Tile(
            "Meal exchanges run out",
            day(me_burn.runout_date),
            f"{num(-me_burn.spare_days)} days before the semester ends",
            RED,
            "large",
        )
    now = [
        verdict,
        Tile("", num(bal.me_left, 0), "exchanges left", BLUE),
        Tile(f"≈ {num(cfg.round_meals(bal.dd_left / cfg.meal_price))} meals", money(bal.dd_left), "dining dollars", TEAL),
        Tile(
            "",
            f"{metrics.progress * 100:.0f}%",
            f"of the semester done · {days_left} {'usable ' if cfg.exclude_away and dc.away_rem else ''}days to go",
            NAVY,
            "wide",
            bar=metrics.progress,
        ),
    ]

    # --- projections -------------------------------------------------------
    proj: list[Tile] = []
    if dd_burn.rate is None:
        proj.append(Tile("Dining dollars", "untouched", "no spending yet to project from", SLATE, "wide"))
    elif dd_burn.leftover_raw >= 0:
        proj.append(Tile("Dining dollars will hold with", num(dd_burn.spare_days, 0), "days to spare", GREEN, "wide"))
        if dd_burn.leftover_raw >= cfg.dd_leftover_flag:
            fate = "carries into spring" if cfg.dd_rollover else "goes unspent — use it or lose it"
            proj.append(Tile(f"Left over on {end}", money(dd_burn.leftover_raw), fate, PURPLE if cfg.dd_rollover else ORANGE, "wide"))
    else:
        proj.append(
            Tile(
                "Projected to run out of dining dollars by",
                day(dd_burn.runout_date),
                f"{num(-dd_burn.spare_days)} days short",
                RED,
                "wide",
            )
        )
    for label, key in (("exchanges only", "me_only"), ("with dining dollars", "with_dd")):
        ro = metrics.run_out[v][key]
        if ro.date is None:
            continue
        short = ro.spare_days is not None and ro.spare_days < 0
        tail = f"{num(abs(ro.spare_days), 0)} days {'short' if short else 'spare'}"
        proj.append(Tile("Two meals every day lasts until", day(ro.date), f"{label} · {tail}", CRIMSON if short else GREEN, "wide"))

    # --- pace --------------------------------------------------------------
    pace_tiles: list[Tile] = []
    if pace.me_only is not None:
        pace_tiles.append(Tile("You can eat", num(pace.me_only, 2), "meals a day on exchanges alone", VIOLET, "wide"))
        pace_tiles.append(Tile("or", num(pace.with_dd, 2), "meals a day counting dining dollars", MAGENTA, "wide"))
    for delta, label in ((targets.strict_delta, "exchanges only"), (targets.pooled_delta, "counting dining dollars")):
        if delta is not None:
            ahead = delta >= 0
            pace_tiles.append(
                Tile("Against an even pace", f"{delta:+.1f}", f"meals {'ahead' if ahead else 'behind'} · {label}", GREEN if ahead else RED, "wide")
            )
    mix = metrics.day_mix[v]["with_dd"]
    if mix.two_per_week is not None:
        pace_tiles.append(
            Tile("A week from here looks like", f"{num(mix.two_per_week)} + {num(mix.one_per_week)}", "two-meal days + one-meal days", BLUE, "wide")
        )
    if me_burn.rate is not None:
        pace_tiles.append(Tile("", num(me_burn.rate, 2), "exchanges a day so far", NAVY))
    if dd_burn.rate is not None:
        pace_tiles.append(Tile("", f"${dd_burn.rate:.2f}", "a day in dining dollars", TEAL))

    # --- habits ------------------------------------------------------------
    habits: list[Tile] = []
    window = df[(df["date"] >= cfg.data_cutoff) & (df["date"] <= dc.a)] if not df.empty else df
    swipes = window[(window["pot"] == "ME") & window["kind"].isin(["usage", "reversal"])] if not window.empty else window
    if not swipes.empty:
        by_spot = (-swipes.groupby("description")["amount"].sum()).sort_values(ascending=False)
        if by_spot.iloc[0] > 0:
            habits.append(Tile("Your most-swiped spot", str(by_spot.index[0]), f"{num(by_spot.iloc[0], 0)} swipes", PURPLE, "wide"))
    if not daily.empty:
        habits.append(Tile("", num(daily["meals"].tail(7).sum(), 0), "meals, last 7 days", BLUE))
    bd = metrics.dd_breakdown
    if bd.snack_count:
        habits.append(Tile("", money(bd.snack_amount), f"on {bd.snack_count} snack{'s' if bd.snack_count != 1 else ''}", ORANGE))
    if bd.meal_count:
        habits.append(Tile("", money(bd.meal_amount), f"on {bd.meal_count} DD meal{'s' if bd.meal_count != 1 else ''}", TEAL))
    if through is not None:
        age = (today - through).days
        habits.append(Tile("Data covers through", day(through), ago(age), SLATE if age <= 3 else RED))
    upcoming = [p for p in cfg.away_periods if p.enabled and p.end >= dc.a]
    if cfg.exclude_away and upcoming:
        p = upcoming[0]
        until = (p.start - dc.a).days
        habits.append(Tile(p.name, f"{day(p.start)}–{p.end.day}" if p.start.month == p.end.month else f"{day(p.start)}–{day(p.end)}",
                           f"away · in {until} days" if until > 0 else "away now", NAVY, "wide"))

    groups = [Group("right now", now), Group("projections", proj), Group("pace", pace_tiles), Group("habits", habits)]
    return [g for g in groups if g.tiles]


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
.metro .tile { position: relative; display: flex; flex-direction: column; justify-content: flex-end;
  padding: 10px 14px 12px; overflow: hidden; border-radius: 0; outline: 3px solid transparent; outline-offset: -3px;
  transition: outline-color .12s, transform .12s; animation: metro-in .5s cubic-bezier(.1,.9,.2,1) both;
  animation-delay: calc(var(--i) * 28ms); }
.metro .tile:hover { outline-color: rgba(255,255,255,.45); }
.metro .tile:active { transform: scale(.975); }
.metro .wide { grid-column: span 2; }
.metro .large { grid-column: span 2; grid-row: span 2; padding: 16px 18px 18px; }
.metro .pre { position: absolute; top: 10px; left: 14px; right: 14px; font-size: 14px; font-weight: 400; line-height: 1.25; }
.metro .large .pre { top: 16px; left: 18px; font-size: 22px; font-weight: 300; }
.metro .val { font-weight: 200; font-size: calc(var(--u) * .36); line-height: 1.02; letter-spacing: -1px;
  white-space: nowrap; font-variant-numeric: tabular-nums; }
.metro .val.long { font-size: calc(var(--u) * .2); letter-spacing: 0; white-space: normal; font-weight: 300; }
.metro .large .val { font-size: calc(var(--u) * .92); letter-spacing: -3px; }
.metro .large .val.long { font-size: calc(var(--u) * .5); letter-spacing: -1px; }
.metro .post { font-size: 13.5px; font-weight: 400; line-height: 1.25; margin-top: 4px; }
.metro .large .post { font-size: 19px; font-weight: 300; margin-top: 8px; }
.metro .bar { height: 5px; background: rgba(255,255,255,.28); margin-top: 8px; }
.metro .bar i { display: block; height: 100%; background: #fff; }
@keyframes metro-in { from { opacity: 0; transform: translateX(46px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .metro .tile { animation: none; transition: none; } }
@container (max-width: 935px) { .metro .groups { --u: clamp(96px, calc((100cqw - 24px) / 4), 150px); } }
@container (max-width: 430px) {
  .metro .groups { --u: calc((100cqw - 8px) / 2); }
  .metro .grid { grid-template-columns: repeat(2, var(--u)); }
  .metro h1 { font-size: 44px; }
}
</style>
"""


def render_html(groups: list[Group], title: str, subtitle: str) -> str:
    out = [CSS, f'<div class="metro"><div class="top"><h1>{escape(title)}</h1><div class="asof">{escape(subtitle)}</div></div>']
    out.append('<div class="groups">')
    i = 0
    for g in groups:
        out.append(f'<section class="group"><h2>{escape(g.title)}</h2><div class="grid">')
        for t in g.tiles:
            long = " long" if len(t.value) > (9 if t.size != "sq" else 5) else ""
            bar = f'<div class="bar"><i style="width:{max(0.0, min(1.0, t.bar)) * 100:.1f}%"></i></div>' if t.bar is not None else ""
            pre = f'<div class="pre">{escape(t.pre)}</div>' if t.pre else ""
            out.append(
                f'<div class="tile {t.size}" style="background:{t.color};--i:{i}">{pre}'
                f'<div class="val{long}">{escape(t.value)}</div><div class="post">{escape(t.post)}</div>{bar}</div>'
            )
            i += 1
        out.append("</div></section>")
    out.append("</div></div>")
    return "".join(out)


def build_page_html(cfg: Config, df: pd.DataFrame, today: date) -> str:
    """Everything the Tiles page shows, as one HTML string. app.py calls only this."""
    from . import metrics as m
    from .freshness import data_through

    metrics = m.compute_metrics(df, cfg)
    a = metrics.day_counts.a
    groups = build_groups(metrics, cfg, df, m.daily(df, cfg), today, data_through(cfg, df))
    return render_html(groups, "Dining", f"as of {a:%A, %b} {a.day}")
