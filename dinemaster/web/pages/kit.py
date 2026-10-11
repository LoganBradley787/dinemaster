"""`/kit`: every page block, control and chart-kit mark on one page, drawn from made-up numbers.

Open it while building a section to see what is available. Nothing here reads your data.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from ..charts import kit

START, END, TODAY = date(2026, 8, 24), date(2026, 12, 11), date(2026, 10, 9)
DAYS = [START + timedelta(days=i) for i in range((END - START).days + 1)]
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
NOTES = [
    kit.Note("today", TODAY, kind="today"),
    kit.Note("ends Dec 11", END, kind="end"),
    kit.Note("Fall break", date(2026, 11, 21), date(2026, 11, 29), "away"),
    kit.Note("Reading day", date(2026, 12, 2), kind="marker"),
    kit.Note("Exams", date(2026, 12, 4), date(2026, 12, 10), "marker"),
]


def lines_chart():
    """line, step_line, dashed_line, area_band, points, end_label, notes, hover_columns."""
    past = [d for d in DAYS if d <= TODAY]
    ahead = [d for d in DAYS if d >= TODAY]
    actual = [(d, 150 - 1.1 * i - 4 * math.sin(i / 5)) for i, d in enumerate(past)]
    now = actual[-1][1]
    expected = [(d, max(0.0, now - 1.2 * i)) for i, d in enumerate(ahead)]
    lo = [(d, max(0.0, v - 0.28 * i)) for i, (d, v) in enumerate(expected)]
    hi = [(d, v + 0.28 * i) for i, (d, v) in enumerate(expected)]
    even = [(d, 150 * (1 - i / (len(DAYS) - 1))) for i, d in enumerate(DAYS)]
    steps = [(d, 150 - 12 * (i // 14)) for i, d in enumerate(DAYS)]

    def draw(size):
        p = kit.plot(size, lanes=2, left=40)
        x = kit.time_scale(START, END, p.x0, p.x1)
        y = kit.linear_scale(0, 156, p.y0, p.y1, count=4, nice=False)
        return [
            kit.notes(p, x, NOTES),
            kit.grid_y(p, y),
            kit.axis_x(p, x),
            kit.area_band(x, y, lo, hi, kit.ME),
            kit.line(x, y, even, kit.DIM, width=1.5),
            kit.step_line(x, y, steps, kit.POOLED, width=2),
            kit.dashed_line(x, y, expected, kit.ME),
            kit.line(x, y, actual, kit.ME, width=3),
            kit.points(x, y, steps[14::28], kit.POOLED, hollow=True),
            kit.end_label(p, x, y, TODAY, now, f"{now:.0f}", kit.ME),
            kit.hover_columns(p, x, [(d, f"{d:%a, %b} {d.day}\n{v:.0f} left") for d, v in actual]),
        ]

    keys = [kit.Key("line", kit.ME), kit.Key("dashed_line", kit.ME, "dash"), kit.Key("step_line", kit.POOLED), kit.Key("thin reference line", kit.DIM, "thin"),
            kit.Key("area_band", kit.ME, "box")]  # fmt: skip
    return kit.figure(draw, "Lines, a range band and date notes", legend=kit.legend(keys))


def stacked_chart():
    """vbars (stacked, on a time scale), hline."""
    days = DAYS[:35]
    rows = [(d, [kit.Seg((i * 7 % 3), kit.ME), kit.Seg(1 if i % 6 == 0 else 0, kit.DD)]) for i, d in enumerate(days)]

    def draw(size):
        p = kit.plot(size, left=30)
        x = kit.time_scale(days[0], days[-1], p.x0, p.x1)
        y = kit.linear_scale(0, 3, p.y0, p.y1, count=3)
        return [
            kit.grid_y(p, y),
            kit.axis_x(p, x),
            kit.vbars(p, x, y, rows, max_width=18),
            kit.hline(p, y, 1.6, "1.6 a day", kit.tint(kit.ME, 0.35)),
            kit.hover_columns(p, x, [(d, f"{d:%a, %b} {d.day}\n{sum(s.value for s in segs):.0f} meals") for d, segs in rows]),
        ]

    keys = [kit.Key("exchange swipes", kit.ME, "box"), kit.Key("dining-dollar meals", kit.DD, "box"), kit.Key("hline", kit.tint(kit.ME, 0.35), "dash")]
    return kit.figure(draw, "Stacked vertical bars on a time scale", heights=(220, 240, 260), legend=kit.legend(keys))


def grouped_chart():
    """vbars (grouped, on a band scale, with value labels), series_labels."""
    now_, then = (1.4, 1.1, 1.7, 1.9, 0.9, 0.4, 0.6), (1.2, 1.3, 1.5, 1.6, 1.2, 0.7, 0.5)
    rows = [(wd, [kit.Seg(a, kit.ME, f"{wd}\n{a} a day now"), kit.Seg(b, kit.VIOLET, f"{wd}\n{b} a day then")]) for wd, a, b in zip(WEEKDAYS, now_, then)]

    def draw(size):
        p = kit.plot(size, left=30, right=86 if not size.compact else 12)
        x = kit.band_scale(WEEKDAYS, p.x0, p.x1)
        y = kit.linear_scale(0, 2, p.y0, p.y1, count=4)
        parts = [kit.grid_y(p, y, lambda v: f"{v:g}"), kit.axis_x(p, x), kit.vbars(p, x, y, rows, grouped=True, label=lambda v: f"{v:g}")]
        if not size.compact:
            parts.append(kit.series_labels(p, [(y(now_[-1]), "this fall", kit.ME), (y(then[-1]), "last spring", kit.VIOLET)]))
        return parts

    keys = [kit.Key("this fall", kit.ME, "box"), kit.Key("last spring", kit.VIOLET, "box")]
    return kit.figure(draw, "Grouped vertical bars with value labels", heights=(220, 240, 260), legend=kit.legend(keys))


def heat_chart():
    """heatmap, axis_y, axis_x on band scales."""
    hours = list(range(8, 23))
    cells = {(h, wd): max(0, round(4 * math.sin((h - 6) / 5) * (1 if i < 5 else 0.4) + (h % 3) - 1)) for h in hours for i, wd in enumerate(WEEKDAYS)}

    def draw(size):
        p = kit.plot(size, left=36, top=4, bottom=24)
        x = kit.band_scale(hours, p.x0, p.x1, pad=0)
        y = kit.band_scale(WEEKDAYS, p.y1, p.y0, pad=0)
        cells_drawn = kit.heatmap(x, y, cells, kit.ME, tip=lambda h, wd, v: f"{wd} around {h}:00\n{v:.0f} meals", label=lambda v: f"{v:.0f}")
        return [cells_drawn, kit.axis_y(p, y), kit.axis_x(p, x, labels=lambda h: f"{h}")]

    return kit.figure(draw, "A heatmap of meals by hour and weekday", heights=(190, 230, 280))


def hbar_chart():
    """hbar_figure (built on hbars): names, stacked bars, value labels."""
    snack = kit.tint(kit.DD, 0.5)
    rows = [
        ("Corner Market", [kit.Seg(12, kit.DD, "Corner Market\n$12 on meals"), kit.Seg(58, snack, "Corner Market\n$58 on snacks")]),
        ("The Long Name Dining Hall and Grill", [kit.Seg(41, kit.DD, "meals $41"), kit.Seg(6, snack, "snacks $6")]),
        ("Cafe", [kit.Seg(9, kit.DD, "meals $9")]),
    ]
    return kit.hbar_figure(rows, "Horizontal stacked bars", fmt=lambda v: f"${v:,.0f}", legend_keys=[kit.Key("meals", kit.DD, "box"), kit.Key("snacks", snack, "box")])


def build(ctx, request) -> dict:
    return {
        "band": [
            {"value": "104", "label": "exchanges left", "detail": "Of 160.", "color": kit.ME},
            {"value": "$269", "label": "dining dollars left", "detail": "About 17 meals.", "color": kit.DD},
            {"value": "+15", "label": "against an even pace", "detail": "Green is good, red is bad, orange is caution.", "color": kit.GREEN},
            {"value": "Nov 20", "label": "run-out day", "detail": "28 days early.", "color": kit.RED},
        ],
        "notes": [
            {"level": "bad", "text": "A red note is something that makes the numbers wrong.", "href": ctx.url("/data"), "link": "Go to data"},
            {"level": "warn", "text": "An orange note is a caution."},
            {"level": "info", "text": "A blue note is worth knowing."},
        ],
        "table": {
            "columns": [("Week", False), ("Meals", True), ("Dining dollars", True)],
            "rows": [["Week of Oct 5", "5", "$10.18"], ["Week of Sep 28", "7", "$11.88"], ["Week of Sep 21", "12", "$0.00"]],
        },
        "seg": [{"label": "Dollars", "href": ctx.url("/kit"), "on": True}, {"label": "Meals", "href": ctx.url("/kit"), "on": False}],
        "charts": [
            ("lines, ranges and date notes", lines_chart()),
            ("stacked bars", stacked_chart()),
            ("grouped bars", grouped_chart()),
            ("heatmap", heat_chart()),
            ("horizontal bars", hbar_chart()),
        ],
    }
