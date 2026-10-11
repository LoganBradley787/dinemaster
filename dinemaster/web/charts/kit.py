"""The SVG chart kit: small pure functions that return SVG markup.

How a chart is put together:

    def draw(size: kit.Size):
        p = kit.plot(size, lanes=2)                       # the plot box inside the margins
        x = kit.time_scale(start, end, p.x0, p.x1)        # scales map data to pixels
        y = kit.linear_scale(0, 160, p.y0, p.y1)
        return [kit.grid_y(p, y), kit.axis_x(p, x), kit.line(x, y, points, kit.ME)]

    svg = kit.figure(draw, "Meal exchanges left, day by day")

`figure` calls `draw` three times, once per width class (`SIZES`: s, m, l), and wraps the three
`<svg>`s in one `<figure class="chart">`. CSS shows the one that fits the figure's width, so text
stays between about 10 and 16 px on a phone and on a wide screen without any script. A draw
function can read `size.name` or `size.compact` to leave things out when space is short.

Conventions:
    * Pixel space is the viewBox: x grows right, y grows down. `Plot.y0` is the baseline (bottom),
      `Plot.y1` the top, so `linear_scale(lo, hi, p.y0, p.y1)` puts `lo` on the baseline.
    * A series is a sequence of `(key, value)`; the key is whatever the x scale takes (a date for
      a time scale, a label for a band scale). A value of None breaks a line.
    * Every mark that carries a value takes a tooltip text and writes it as `data-tip`; `app.js`
      shows one shared tooltip for hover, tap and keyboard (arrow keys on a focused chart).
    * Text is white or dimmed white, never the series colour. Colours come from the Metro palette.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from markupsafe import Markup, escape

from ...tiles import BLUE, CRIMSON, GREEN, MAGENTA, NAVY, ORANGE, PURPLE, RED, SLATE, TEAL, VIOLET, day, em, num, span

__all__ = [
    "BLUE", "NAVY", "TEAL", "GREEN", "RED", "ORANGE", "CRIMSON", "PURPLE", "VIOLET", "MAGENTA", "SLATE",
    "ME", "DD", "POOLED", "INK", "DIM", "BG",
]  # fmt: skip

BG = "#1B0E45"
INK = "#FFFFFF"
DIM = "rgba(255,255,255,.55)"  # reference series (an even pace, an average)
#: The two pots keep these colours on every chart; a third "both together" series is teal.
#: Dining dollars are amber, not the palette's ORANGE: that one means "caution" in tiles and number bands.
ME, DD, POOLED = BLUE, "#D2740A", "#00A3BF"

FONT = 12.5  # label size in viewBox units
SMALL = 11.5
LANE = 17.0  # height of one row of labels above the plot
DASH = "7 5"


def tint(color: str, amount: float = 0.45) -> str:
    """`color` mixed toward white: the lighter partner of a series (snacks beside meals)."""
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(c + (255 - c) * amount):02X}" for c in (r, g, b))


def text_width(text: str, size: float = FONT) -> float:
    """Estimated width of `text` in viewBox units (errs on the wide side)."""
    return em(text) * size


def fit(text: str, width: float, size: float = FONT) -> str:
    """`text`, cut with an ellipsis when it is wider than `width`."""
    if text_width(text, size) <= width:
        return text
    out = text
    while out and text_width(out + "…", size) > width:
        out = out[:-1]
    return out.rstrip(" ,") + "…"


def _n(v: float) -> str:
    return f"{v:.1f}".removesuffix(".0")


def _tip(tip: str | None) -> str:
    return f' data-tip="{escape(tip)}"' if tip else ""


# ---------------------------------------------------------------------------
# sizes, the plot box, the figure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Size:
    name: str  # "s" | "m" | "l"
    w: float
    h: float

    @property
    def compact(self) -> bool:
        return self.name == "s"


#: viewBox widths. The figure is shown at s below 520px, m below 860px, l above (see metro.css).
WIDTHS = {"s": 380.0, "m": 660.0, "l": 1080.0}
HEIGHTS = (250.0, 290.0, 340.0)


@dataclass(frozen=True)
class Plot:
    """The drawing area: `x0..x1` left to right, `y0` (baseline) up to `y1` (top)."""

    size: Size
    x0: float
    x1: float
    y0: float
    y1: float
    lanes: int = 0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y0 - self.y1


def plot(size: Size, lanes: int = 0, left: float = 40, right: float = 12, top: float = 10, bottom: float = 26) -> Plot:
    """The plot box for `size`. `lanes` reserves rows above it for `notes` labels."""
    return Plot(size, left, size.w - right, size.h - bottom, top + lanes * LANE, lanes)


def figure(
    draw: Callable[[Size], Iterable[str]],
    label: str,
    heights: Sequence[float] = HEIGHTS,
    legend: str | None = None,
) -> Markup:
    """A responsive chart: `draw(size)` returns SVG parts and is called for each width class.

    `label` says what the chart shows, for screen readers. `heights` are the viewBox heights for
    (s, m, l). `legend` is the markup from `legend(...)`, shown above the plot.
    """
    out = [f'<figure class="chart" tabindex="0" role="group" aria-label="{escape(label)}">']
    if legend:
        out.append(str(legend))
    for (name, w), h in zip(WIDTHS.items(), heights):
        parts = "".join(str(part) for part in draw(Size(name, w, float(h))) if part)
        out.append(f'<svg class="v-{name}" viewBox="0 0 {_n(w)} {_n(h)}" role="img" aria-label="{escape(label)}">{parts}</svg>')
    out.append("</figure>")
    return Markup("".join(out))


@dataclass(frozen=True)
class Key:
    """One legend entry. kind: line | dash | thin | box."""

    label: str
    color: str
    kind: str = "line"


def legend(keys: Sequence[Key]) -> Markup:
    """A compact flat legend (HTML, so it wraps on a phone). Pass it to `figure(legend=...)`."""
    items = "".join(f'<li><i class="k {escape(k.kind)}" style="--c:{escape(k.color)}"></i>{escape(k.label)}</li>' for k in keys)
    return Markup(f'<ul class="legend">{items}</ul>')


def empty(size: Size, message: str = "Nothing to chart yet.") -> list[Markup]:
    """What a chart draws when it has no data: one plain sentence, centred."""
    return [text(size.w / 2, size.h / 2, message, anchor="middle", cls="ax empty")]


# ---------------------------------------------------------------------------
# scales
# ---------------------------------------------------------------------------


def nice_step(extent: float, count: int = 5) -> float:
    """A round step (1, 2, 2.5, 5 times a power of ten) giving about `count` ticks over `extent`."""
    if extent <= 0 or count <= 0:
        return 1.0
    raw = extent / count
    power = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mult * power * (1 + 1e-9):
            return mult * power
    return 10 * power


def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    """Round tick values covering lo..hi."""
    if hi < lo:
        lo, hi = hi, lo
    if hi == lo:
        hi = lo + 1
    step = nice_step(hi - lo, count)
    first = math.floor(lo / step + 1e-9)
    last = math.ceil(hi / step - 1e-9)
    return [round(i * step, 10) for i in range(first, last + 1)]


@dataclass(frozen=True)
class Linear:
    lo: float
    hi: float
    r0: float
    r1: float
    ticks: tuple[float, ...]

    def __call__(self, v: float) -> float:
        return self.r0 + (v - self.lo) / (self.hi - self.lo) * (self.r1 - self.r0)


def linear_scale(lo: float, hi: float, r0: float, r1: float, count: int = 5, nice: bool = True) -> Linear:
    """Map lo..hi onto r0..r1. With `nice` the domain grows to round ticks (so `hi` may be larger)."""
    if hi <= lo:
        hi = lo + 1
    ticks = nice_ticks(lo, hi, count)
    if nice:
        return Linear(ticks[0], ticks[-1], r0, r1, tuple(ticks))
    return Linear(lo, hi, r0, r1, tuple(t for t in ticks if lo - 1e-9 <= t <= hi + 1e-9))


@dataclass(frozen=True)
class Time:
    """A day scale: every date gets a slot `bw` wide; calling the scale gives the slot's centre."""

    start: date
    end: date
    r0: float
    r1: float

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def bw(self) -> float:
        return (self.r1 - self.r0) / self.days

    def left(self, d: date) -> float:
        return self.r0 + (d - self.start).days * self.bw

    def right(self, d: date) -> float:
        return self.left(d) + self.bw

    def __call__(self, d: date) -> float:
        return self.left(d) + self.bw / 2

    def ticks(self, min_gap: float = 46) -> list[tuple[date, str]]:
        """Sparse date ticks: the first of each month, or Mondays for a span under seven weeks."""
        if self.days > 48:
            picks = [d for d in self._each() if d.day == 1]
            labels = [f"{d:%b}" for d in picks]
        else:
            picks = [d for d in self._each() if d.weekday() == 0]
            labels = [day(d) for d in picks]
        out: list[tuple[date, str]] = []
        last = -math.inf
        for d, label in zip(picks, labels):
            if self(d) - last >= min_gap:
                out.append((d, label))
                last = self(d)
        return out

    def _each(self) -> Iterable[date]:
        return (self.start + timedelta(days=i) for i in range(self.days))


def time_scale(start: date, end: date, r0: float, r1: float) -> Time:
    return Time(start, max(start, end), r0, r1)


@dataclass(frozen=True)
class Band:
    """A category scale: each key gets a band `bw` wide; calling the scale gives the band's centre."""

    keys: tuple
    r0: float
    r1: float
    pad: float

    @property
    def step(self) -> float:
        return (self.r1 - self.r0) / max(1, len(self.keys))

    @property
    def bw(self) -> float:
        return self.step * (1 - self.pad)

    def __call__(self, key) -> float:
        return self.r0 + (self.keys.index(key) + 0.5) * self.step


def band_scale(keys: Iterable, r0: float, r1: float, pad: float = 0.25) -> Band:
    return Band(tuple(keys), r0, r1, pad)


# ---------------------------------------------------------------------------
# text, axes, gridlines
# ---------------------------------------------------------------------------


def text(x: float, y: float, s: str, anchor: str = "start", cls: str = "ax", size: float | None = None) -> Markup:
    """One line of text. cls: "ax" (dim axis text), "lbl" (white label with a halo), "note"."""
    font = f' font-size="{_n(size)}"' if size else ""
    return Markup(f'<text x="{_n(x)}" y="{_n(y)}" text-anchor="{anchor}" class="{cls}"{font}>{escape(s)}</text>')


def grid_y(p: Plot, y: Linear, fmt: Callable[[float], str] = lambda v: num(v, 0), ticks: Sequence[float] | None = None) -> Markup:
    """Thin horizontal gridlines with their values at the left."""
    out = []
    for t in y.ticks if ticks is None else ticks:
        yy = y(t)
        cls = "base" if abs(yy - p.y0) < 0.5 else "grid"
        out.append(f'<line class="{cls}" x1="{_n(p.x0)}" x2="{_n(p.x1)}" y1="{_n(yy)}" y2="{_n(yy)}"/>')
        out.append(str(text(p.x0 - 7, yy + FONT * 0.34, fmt(t), anchor="end")))
    return Markup("".join(out))


def grid_x(p: Plot, x: Linear, fmt: Callable[[float], str] = lambda v: num(v, 0)) -> Markup:
    """Thin vertical gridlines with their values along the bottom (for horizontal bars)."""
    out = []
    for t in x.ticks:
        xx = x(t)
        cls = "base" if abs(xx - p.x0) < 0.5 else "grid"
        out.append(f'<line class="{cls}" x1="{_n(xx)}" x2="{_n(xx)}" y1="{_n(p.y1)}" y2="{_n(p.y0)}"/>')
        out.append(str(text(xx, p.y0 + FONT + 5, fmt(t), anchor="middle")))
    return Markup("".join(out))


def axis_x(p: Plot, x: Time | Band, labels: Callable | None = None, min_gap: float = 46) -> Markup:
    """The baseline and sparse labels under it.

    A time scale labels months (or Mondays for short spans). A band scale labels its keys with
    `labels(key)` (default `str`), skipping some when they would touch.
    """
    out = [f'<line class="base" x1="{_n(p.x0)}" x2="{_n(p.x1)}" y1="{_n(p.y0)}" y2="{_n(p.y0)}"/>']
    if isinstance(x, Time):
        ticks = [(x.left(d), label, "start") for d, label in x.ticks(min_gap)]
        for xx, _, _ in ticks:
            out.append(f'<line class="tick" x1="{_n(xx)}" x2="{_n(xx)}" y1="{_n(p.y0)}" y2="{_n(p.y0 + 5)}"/>')
        ticks = [(xx + 4, label, anchor) for xx, label, anchor in ticks if xx + 4 + text_width(label) <= p.size.w]
    else:
        named = [(x(k), (labels or str)(k)) for k in x.keys]
        widest = max((text_width(s) for _, s in named), default=0.0)
        every = max(1, math.ceil((widest + 8) / x.step)) if x.step > 0 else 1
        ticks = [(xx, s, "middle") for i, (xx, s) in enumerate(named) if i % every == 0]
    for xx, label, anchor in ticks:
        out.append(str(text(xx, p.y0 + FONT + 5, label, anchor=anchor)))
    return Markup("".join(out))


def axis_y(p: Plot, y: Band, labels: Callable | None = None, width: float | None = None) -> Markup:
    """Category names at the left of a band scale that runs top to bottom (horizontal bars, heatmaps)."""
    room = (p.x0 - 8) if width is None else width
    out = [str(text(p.x0 - 8, y(k) + FONT * 0.34, fit((labels or str)(k), room), anchor="end", cls="ax name")) for k in y.keys]
    return Markup("".join(out))


def hline(
    p: Plot, y: Linear, value: float, label: str = "", color: str = DIM, dash: str | None = DASH, tip: str | None = None,
    below: bool = False,
) -> Markup:  # fmt: skip
    """A horizontal reference line across the plot, labelled above (or `below`) its right end."""
    yy = y(value)
    if not (p.y1 - 0.5 <= yy <= p.y0 + 0.5):
        return Markup("")
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out = f'<line x1="{_n(p.x0)}" x2="{_n(p.x1)}" y1="{_n(yy)}" y2="{_n(yy)}" stroke="{color}" stroke-width="1.5"{d}{_tip(tip)}/>'
    if label:
        out += str(text(p.x1, yy + SMALL + 3 if below else yy - 5, fit(label, p.w), anchor="end", cls="lbl", size=SMALL))
    return Markup(out)


# ---------------------------------------------------------------------------
# lines, areas, points
# ---------------------------------------------------------------------------


def _xy(x, y, points: Sequence[tuple]) -> list[tuple[float, float] | None]:
    return [None if v is None or (isinstance(v, float) and math.isnan(v)) else (x(k), y(v)) for k, v in points]


def line(
    x, y, points: Sequence[tuple], color: str, width: float = 2.5, dash: str | None = None, step: bool = False, opacity: float = 1.0
) -> Markup:
    """A line through `points` [(key, value), ...]. A None value leaves a gap. `step` holds each value to the next key."""
    d, pen_up, prev = [], True, None
    for pt in _xy(x, y, points):
        if pt is None:
            pen_up = True
            continue
        if pen_up:
            d.append(f"M{_n(pt[0])},{_n(pt[1])}")
        elif step and prev is not None:
            d.append(f"H{_n(pt[0])}V{_n(pt[1])}")
        else:
            d.append(f"L{_n(pt[0])},{_n(pt[1])}")
        pen_up, prev = False, pt
    if not d:
        return Markup("")
    extra = (f' stroke-dasharray="{dash}"' if dash else "") + (f' opacity="{opacity:g}"' if opacity < 1 else "")
    join = "miter" if step else "round"
    return Markup(
        f'<path d="{"".join(d)}" fill="none" stroke="{color}" stroke-width="{width:g}" stroke-linejoin="{join}" stroke-linecap="butt"{extra}/>'
    )


def step_line(x, y, points: Sequence[tuple], color: str, width: float = 2.5, dash: str | None = None) -> Markup:
    """A stair-step line: right for a balance that only changes when something is bought."""
    return line(x, y, points, color, width, dash, step=True)


def dashed_line(x, y, points: Sequence[tuple], color: str, width: float = 2.0) -> Markup:
    """A dashed line: a projection or forecast of the series with the same colour."""
    return line(x, y, points, color, width, dash=DASH)


def area_band(x, y, lo: Sequence[tuple], hi: Sequence[tuple], color: str, opacity: float = 0.22, tip: str | None = None) -> Markup:
    """A filled range between two series with the same keys (a forecast's likely range)."""
    top = [pt for pt in _xy(x, y, hi) if pt is not None]
    bottom = [pt for pt in _xy(x, y, lo) if pt is not None]
    if not top or not bottom:
        return Markup("")
    d = "M" + "L".join(f"{_n(a)},{_n(b)}" for a, b in top + bottom[::-1]) + "Z"
    return Markup(f'<path d="{d}" fill="{color}" opacity="{opacity:g}"{_tip(tip)}/>')


def points(x, y, pts: Sequence[tuple], color: str, r: float = 4.0, hollow: bool = False, tips: Sequence[str] | None = None) -> Markup:
    """Point markers, each with a ring in the background colour. `hollow` draws an outline (a part value)."""
    out = []
    for i, pt in enumerate(_xy(x, y, pts)):
        if pt is None:
            continue
        tip = _tip(tips[i]) if tips else ""
        style = f'fill="{BG}" stroke="{color}" stroke-width="2"' if hollow else f'fill="{color}" stroke="{BG}" stroke-width="2"'
        out.append(f'<circle class="pt" cx="{_n(pt[0])}" cy="{_n(pt[1])}" r="{r:g}" {style}{tip}/>')
    return Markup("".join(out))


def end_label(p: Plot, x, y, key, value: float, label: str, color: str) -> Markup:
    """A dot on one point with its value beside it: the direct label for where a line stands now."""
    cx, cy = x(key), y(value)
    w = text_width(label, FONT + 1)
    left = cx + 8 + w > p.x1
    tx = cx - 8 if left else cx + 8
    ty = cy - 8 if cy - 8 - FONT > p.y1 else cy + FONT + 6
    dot = f'<circle class="pt" cx="{_n(cx)}" cy="{_n(cy)}" r="4.5" fill="{color}" stroke="{BG}" stroke-width="2"/>'
    return Markup(dot + str(text(tx, ty, label, anchor="end" if left else "start", cls="lbl", size=FONT + 1)))


def series_labels(p: Plot, items: Sequence[tuple[float, str, str]], gap: float = 15.0) -> Markup:
    """Direct labels in the right margin, one per series: items are (y in pixels, text, colour).

    Labels that would touch are moved apart and joined to their line end with a thin leader.
    Reserve the room with `plot(size, right=...)`.
    """
    placed: list[list] = [[yy, yy, s, c] for yy, s, c in sorted(items)]
    for i in range(1, len(placed)):
        placed[i][1] = max(placed[i][1], placed[i - 1][1] + gap)
    overflow = placed[-1][1] - p.y0 if placed else 0
    if overflow > 0:
        for row in placed:
            row[1] -= overflow
    out = []
    for true_y, yy, s, color in placed:
        out.append(f'<rect x="{_n(p.x1 + 6)}" y="{_n(yy - 4)}" width="8" height="8" fill="{color}"/>')
        if abs(true_y - yy) > 1:
            out.append(f'<line class="leader" x1="{_n(p.x1)}" y1="{_n(true_y)}" x2="{_n(p.x1 + 6)}" y2="{_n(yy)}"/>')
        out.append(str(text(p.x1 + 18, yy + FONT * 0.34, fit(s, p.size.w - p.x1 - 18), cls="lbl")))
    return Markup("".join(out))


def hover_columns(p: Plot, x: Time | Band, tips: Sequence[tuple]) -> Markup:
    """One invisible full-height column per key, carrying that key's tooltip: [(key, text), ...].

    Use on line charts so the whole day (not a 2px line) is the hover target. Put it last.
    """
    out = []
    for key, tip in tips:
        if not tip:
            continue
        left = x.left(key) if isinstance(x, Time) else x(key) - x.step / 2
        width = x.bw if isinstance(x, Time) else x.step
        out.append(f'<rect class="hit" x="{_n(left)}" y="{_n(p.y1)}" width="{_n(width)}" height="{_n(p.h)}"{_tip(tip)}/>')
    return Markup("".join(out))


# ---------------------------------------------------------------------------
# bars
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Seg:
    """One bar, or one segment of a stacked bar."""

    value: float
    color: str
    tip: str = ""


def _label_fits(labels: Sequence[str], room: float) -> bool:
    return all(text_width(s, SMALL) <= room for s in labels)


def vbars(
    p: Plot,
    x: Time | Band,
    y: Linear,
    rows: Sequence[tuple[object, Sequence[Seg]]],
    grouped: bool = False,
    label: Callable[[float], str] | None = None,
    max_width: float = 30.0,
    gap: float = 1.5,
) -> Markup:
    """Vertical bars. rows = [(key, [Seg, ...]), ...]; segments stack unless `grouped`.

    `label` formats a value written above each bar (the total of a stack). Labels are left out
    when they would not all fit, so they never overlap. Stacked segments are parted by a thin
    gap in the background colour.
    """
    out, base = [], y(0)
    slot = x.bw
    n = max((len(segs) for _, segs in rows), default=1) if grouped else 1
    width = min(max_width, max(1.0, slot / n - (gap if slot / n > 4 else 0)))
    captions: list[tuple[float, float, str]] = []
    for key, segs in rows:
        cx = x(key)
        if grouped:
            left = cx - width * len(segs) / 2
            for i, seg in enumerate(segs):
                top = y(seg.value)
                bx = left + i * width
                inner = width - (gap if width > 4 else 0)
                out.append(_rect(bx + (width - inner) / 2, min(top, base), inner, abs(base - top), seg.color, seg.tip))
                if label:
                    captions.append((bx + width / 2, min(top, base) - 4, label(seg.value)))
        else:
            run = 0.0
            for seg in segs:
                if seg.value <= 0:
                    continue
                y_lo, y_hi = y(run), y(run + seg.value)
                height = y_lo - y_hi
                cut = gap if run > 0 and height > gap * 2 else 0
                out.append(_rect(cx - width / 2, y_hi, width, height - cut, seg.color, seg.tip))
                run += seg.value
            if label and run > 0:
                captions.append((cx, y(run) - 4, label(run)))
    if captions and _label_fits([s for _, _, s in captions], (width if grouped else slot) + 2):
        out += [str(text(cx, cy, s, anchor="middle", cls="lbl", size=SMALL)) for cx, cy, s in captions]
    return Markup("".join(out))


def hbars(
    p: Plot,
    x: Linear,
    y: Band,
    rows: Sequence[tuple[object, Sequence[Seg]]],
    grouped: bool = False,
    label: Callable[[float], str] | None = None,
    max_width: float = 22.0,
    gap: float = 1.5,
    offset: float = 0.0,
) -> Markup:
    """Horizontal bars. rows = [(key, [Seg, ...]), ...]; segments stack unless `grouped`.

    `label` formats the value written past the end of each bar (the total of a stack).
    `offset` moves the bars down inside their band, leaving room for a name above (see `hbar_figure`).
    """
    out, base = [], x(0)
    n = max((len(segs) for _, segs in rows), default=1) if grouped else 1
    width = min(max_width, y.bw / n)
    for key, segs in rows:
        cy = y(key) + offset
        if grouped:
            top = cy - width * len(segs) / 2
            for i, seg in enumerate(segs):
                end = x(seg.value)
                out.append(_rect(base, top + i * width + gap / 2, max(0.0, end - base), width - gap, seg.color, seg.tip))
                if label:
                    out.append(str(text(end + 6, top + (i + 0.5) * width + SMALL * 0.34, label(seg.value), cls="lbl", size=SMALL)))
        else:
            run = 0.0
            for seg in segs:
                if seg.value <= 0:
                    continue
                x_lo, x_hi = x(run), x(run + seg.value)
                length = x_hi - x_lo
                cut = gap if run > 0 and length > gap * 2 else 0
                out.append(_rect(x_lo + cut, cy - width / 2, length - cut, width, seg.color, seg.tip))
                run += seg.value
            if label:
                out.append(str(text(x(run) + 6, cy + FONT * 0.34, label(run), cls="lbl")))
    return Markup("".join(out))


def _rect(x: float, y: float, w: float, h: float, color: str, tip: str = "") -> str:
    return f'<rect class="bar" x="{_n(x)}" y="{_n(y)}" width="{_n(max(0.0, w))}" height="{_n(max(0.0, h))}" fill="{color}"{_tip(tip)}/>'


def hbar_figure(
    rows: Sequence[tuple[str, Sequence[Seg]]],
    label: str,
    fmt: Callable[[float], str] = lambda v: num(v, 0),
    legend_keys: Sequence[Key] | None = None,
    empty_text: str = "Nothing to chart yet.",
) -> Markup:
    """A whole horizontal bar chart of named rows, longest first as given: names, bars, values.

    On a phone the name sits above its bar; on wider screens it sits at the left.
    """

    def heights(row: float, extra: float) -> float:
        return max(80.0, len(rows) * row + extra)

    def draw(size: Size):
        if not rows:
            return empty(size, empty_text)
        top = max((sum(max(0.0, s.value) for s in segs) for _, segs in rows), default=0.0) or 1.0
        value_room = max(text_width(fmt(sum(s.value for s in segs))) for _, segs in rows) + 12
        if size.compact:
            p = plot(size, left=2, right=value_room, top=4, bottom=6)
            x = linear_scale(0, top, p.x0, p.x1, nice=False)
            y = band_scale([k for k, _ in rows], p.y1, p.y0, pad=0.0)
            names = [str(text(p.x0, y(k) - y.step / 2 + FONT + 4, fit(str(k), size.w - 4), cls="ax name")) for k, _ in rows]
            return [*names, hbars(p, x, y, rows, label=fmt, max_width=14, offset=y.step / 2 - 12)]
        name_room = min(size.w * 0.34, max(text_width(str(k)) for k, _ in rows) + 14)
        p = plot(size, left=name_room, right=value_room, top=4, bottom=6)
        x = linear_scale(0, top, p.x0, p.x1, nice=False)
        y = band_scale([k for k, _ in rows], p.y1, p.y0, pad=0.3)
        return [axis_y(p, y), hbars(p, x, y, rows, label=fmt)]

    sizes = (heights(46, 10), heights(30, 10), heights(32, 10))
    return figure(draw, label, sizes, legend(legend_keys) if legend_keys else None)


# ---------------------------------------------------------------------------
# heatmap
# ---------------------------------------------------------------------------


def heatmap(
    x: Band,
    y: Band,
    cells: dict[tuple, float],
    color: str = BLUE,
    vmax: float | None = None,
    tip: Callable[[object, object, float], str] | None = None,
    label: Callable[[float], str] | None = None,
    gap: float = 2.0,
) -> Markup:
    """A grid of squares, one per (x key, y key); a larger value is a stronger `color`.

    Cells with no value (or zero) are a faint blank. `label` writes the value inside each cell
    when it fits.
    """
    top = vmax if vmax is not None else max((v for v in cells.values() if v), default=1.0)
    out = []
    w, h = x.step - gap, y.step - gap
    for yk in y.keys:
        for xk in x.keys:
            v = cells.get((xk, yk)) or 0.0
            left, upper = x(xk) - w / 2, y(yk) - h / 2
            if v <= 0:
                out.append(f'<rect class="cell blank" x="{_n(left)}" y="{_n(upper)}" width="{_n(w)}" height="{_n(h)}"/>')
                continue
            strength = 0.25 + 0.75 * min(1.0, v / top) if top > 0 else 1.0
            t = _tip(tip(xk, yk, v)) if tip else ""
            out.append(
                f'<rect class="cell" x="{_n(left)}" y="{_n(upper)}" width="{_n(w)}" height="{_n(h)}" fill="{color}" fill-opacity="{strength:.2f}"{t}/>'
            )
            if label and text_width(label(v), SMALL) <= w - 4 and h >= SMALL + 2:
                out.append(str(text(x(xk), y(yk) + SMALL * 0.34, label(v), anchor="middle", cls="lbl plain", size=SMALL)))
    return Markup("".join(out))


# ---------------------------------------------------------------------------
# date notes: today, semester end, away periods, calendar markers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    """A labelled date or date range on a time axis. kind: today | end | away | marker | line."""

    label: str
    start: date
    end: date | None = None
    kind: str = "line"


_ORDER = {"today": 0, "end": 1, "away": 2, "marker": 3, "line": 4}


def notes(p: Plot, x: Time, items: Sequence[Note]) -> Markup:
    """Vertical reference lines and shaded date bands, with their labels in the lanes above the plot.

    Labels never overlap: each takes the lowest free lane (`plot(size, lanes=n)` reserves them),
    and a label with no free lane is left out (its band still has a tooltip). Today and the
    semester end are placed first.
    """
    lanes: list[list[tuple[float, float]]] = [[] for _ in range(max(1, p.lanes))]
    marks, labels = [], []
    for n in sorted(items, key=lambda n: (_ORDER.get(n.kind, 9), n.start)):
        last = n.end or n.start
        if last < x.start or n.start > x.end:
            continue
        a, b = max(n.start, x.start), min(last, x.end)
        banded = n.kind in ("away", "marker") and (n.end is not None and n.end > n.start or n.kind == "away")
        when = span(n.start, last)
        if banded:
            xa, xb = x.left(a), x.right(b)
            marks.append(
                f'<rect class="note-{n.kind}" x="{_n(xa)}" y="{_n(p.y1)}" width="{_n(xb - xa)}" height="{_n(p.h)}"'
                f"{_tip(f'{n.label}\n{when}')}/>"
            )
        else:
            xa = xb = x(a)
        width = text_width(n.label, SMALL)
        pad = 0 if banded else 5
        if xa + pad + width <= p.size.w - 2:
            lo, anchor, tx = xa + pad, "start", xa + pad
        else:
            lo, anchor, tx = xb - pad - width, "end", xb - pad
        lane = next((i for i, taken in enumerate(lanes) if all(lo > t1 + 8 or lo + width < t0 - 8 for t0, t1 in taken)), None)
        reach = p.y1 if lane is None else p.y1 - lane * LANE - (LANE - 3 if not banded else 2)
        if not banded:
            marks.append(f'<line class="note-{n.kind}" x1="{_n(xa)}" x2="{_n(xa)}" y1="{_n(reach)}" y2="{_n(p.y0)}"{_tip(f"{n.label}\n{when}")}/>')
        elif lane:  # a tick from the band up to its label, wherever it crosses no label in the lanes below
            below = [t for taken in lanes[:lane] for t in taken]
            edge = next((e for e in (xa, xb, (xa + xb) / 2) if lo <= e <= lo + width and all(not t0 - 3 <= e <= t1 + 3 for t0, t1 in below)), None)
            if edge is not None:
                marks.append(f'<line class="leader" x1="{_n(edge)}" x2="{_n(edge)}" y1="{_n(reach)}" y2="{_n(p.y1)}"/>')
        if lane is None or lo < 0:
            continue
        lanes[lane].append((lo, lo + width))
        labels.append(str(text(tx + (3 if banded and lane else 0), p.y1 - lane * LANE - 5, n.label, anchor=anchor, cls=f"note t-{n.kind}", size=SMALL)))
    return Markup("".join(marks + labels))


def semester_notes(cfg, today: date | None = None, today_label: str = "today", markers: bool = True) -> list[Note]:
    """The notes every semester-long chart carries: today, the last day, days away, calendar markers."""
    out = [Note(f"ends {day(cfg.semester_end)}", cfg.semester_end, kind="end")]
    if today is not None:
        out.append(Note(today_label, today, kind="today"))
    out += [Note(period.name, period.start, period.end, "away") for period in cfg.away_periods if period.enabled]
    if markers:
        out += [Note(mk.name, mk.start, mk.end, "marker") for mk in cfg.markers]
    return out
