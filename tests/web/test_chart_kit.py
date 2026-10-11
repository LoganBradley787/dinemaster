"""The SVG chart kit: scales and ticks, every mark, tooltips, and that nothing crashes on no data."""

from __future__ import annotations

import re
from datetime import date, timedelta

from markupsafe import Markup

from dinemaster.web.charts import kit

S = kit.Size("s", 380, 250)
L = kit.Size("l", 1080, 340)
START, END = date(2026, 8, 21), date(2026, 12, 18)


def count(markup, tag: str) -> int:
    return len(re.findall(rf"<{tag}[ >]", str(markup)))


def texts(markup) -> list[str]:
    return re.findall(r"<text[^>]*>([^<]*)</text>", str(markup))


# --- scales and ticks ---------------------------------------------------------------------------


def test_nice_ticks_are_round_and_cover_the_range():
    assert kit.nice_ticks(0, 160, 4) == [0, 50, 100, 150, 200]
    assert kit.nice_ticks(0, 3, 3) == [0, 1, 2, 3]
    assert kit.nice_ticks(0, 0.9, 4) == [0, 0.25, 0.5, 0.75, 1.0]
    assert kit.nice_ticks(5, 5) == [5.0, 5.2, 5.4, 5.6, 5.8, 6.0]  # a flat range still gets ticks
    assert kit.nice_ticks(10, 0, 2) == [0, 5, 10]  # the ends may come in either order


def test_linear_scale_maps_the_domain_onto_the_range():
    y = kit.linear_scale(0, 160, 300, 20, count=4)
    assert (y.lo, y.hi) == (0, 200) and y(0) == 300 and y(200) == 20 and y(100) == 160
    exact = kit.linear_scale(0, 160, 300, 20, count=4, nice=False)
    assert exact.hi == 160 and exact.ticks == (0, 50, 100, 150) and exact(160) == 20
    assert kit.linear_scale(4, 4, 0, 10).hi > 4  # never a zero-width domain


def test_time_scale_gives_every_day_a_slot():
    x = kit.time_scale(START, START + timedelta(days=9), 100, 200)
    assert x.days == 10 and x.bw == 10
    assert x.left(START) == 100 and x(START) == 105 and x.right(START + timedelta(days=9)) == 200
    assert kit.time_scale(START, START - timedelta(days=3), 0, 10).days == 1  # an end before the start


def test_time_ticks_are_months_for_a_semester_and_mondays_for_a_few_weeks():
    months = kit.time_scale(START, END, 0, 1000).ticks()
    assert [label for _, label in months] == ["Sep", "Oct", "Nov", "Dec"]
    assert all(d.day == 1 for d, _ in months)
    weeks = kit.time_scale(START, START + timedelta(days=27), 0, 1000).ticks()
    assert [label for _, label in weeks] == ["Aug 24", "Aug 31", "Sep 7", "Sep 14"]
    crowded = kit.time_scale(START, START + timedelta(days=27), 0, 120).ticks()
    assert 1 <= len(crowded) < 4  # labels thin out instead of touching


def test_band_scale_centres_each_key():
    x = kit.band_scale(["a", "b", "c", "d"], 0, 400, pad=0.25)
    assert x.step == 100 and x.bw == 75 and x("a") == 50 and x("d") == 350
    assert kit.band_scale([], 0, 100).step == 100  # no keys: no division by zero


# --- text helpers -------------------------------------------------------------------------------


def test_fit_cuts_long_text_with_an_ellipsis():
    assert kit.fit("Hall", 200) == "Hall"
    cut = kit.fit("The Long Name Dining Hall and Grill", 80)
    assert cut.endswith("…") and kit.text_width(cut) <= 80


def test_tint_moves_a_colour_toward_white():
    light = kit.tint(kit.DOLLARS, 0.5)
    assert light == "color-mix(in srgb, var(--dollars-mark), var(--mix-up) 50%)" and kit.tint(kit.DOLLARS, 0) == kit.DOLLARS
    assert light.meaning is kit.Meaning.DOLLARS  # a tint keeps what the colour means
    assert light.resolve("dark") == "#80D4D4" and light.resolve("light") == "#004A4A"  # away from the ground in both themes
    assert kit.tint(kit.MEALS, -1).resolve("dark") == "#000000" and kit.tint(kit.MEALS, -1).resolve("light") == "#FFFFFF"


def test_text_is_escaped():
    assert "&lt;b&gt;" in kit.text(0, 0, "<b>") and "<b>" not in kit.text(0, 0, "<b>")


# --- marks --------------------------------------------------------------------------------------


def scales(size=L):
    p = kit.plot(size, lanes=2)
    return p, kit.time_scale(START, END, p.x0, p.x1), kit.linear_scale(0, 160, p.y0, p.y1)


def series(n=30, start=150.0, step=-1.0):
    return [(START + timedelta(days=i), start + step * i) for i in range(n)]


def test_line_draws_one_path_and_breaks_at_missing_values():
    p, x, y = scales()
    out = kit.line(x, y, series(5), kit.MEALS)
    assert count(out, "path") == 1 and out.count("L") == 4 and f'stroke="{kit.MEALS}"' in out
    gap = kit.line(x, y, [(START, 1.0), (START + timedelta(days=1), None), (START + timedelta(days=2), 3.0)], kit.MEALS)
    assert gap.count("M") == 2 and gap.count("L") == 0
    assert kit.line(x, y, [], kit.MEALS) == "" and kit.line(x, y, [(START, None)], kit.MEALS) == ""


def test_step_and_dashed_lines():
    p, x, y = scales()
    step = kit.step_line(x, y, series(4), kit.MEALS)
    assert step.count("H") == 3 and step.count("V") == 3
    assert "stroke-dasharray" in kit.dashed_line(x, y, series(4), kit.MEALS)
    assert "stroke-dasharray" not in kit.line(x, y, series(4), kit.MEALS)


def test_area_band_is_one_closed_shape():
    p, x, y = scales()
    out = kit.area_band(x, y, series(10, 100), series(10, 120), kit.MEALS, tip="likely range")
    assert count(out, "path") == 1 and out.rstrip('"/>').count("Z") == 1 and 'data-tip="likely range"' in out
    assert kit.area_band(x, y, [], [], kit.MEALS) == ""


def test_points_carry_tooltips_and_can_be_hollow():
    p, x, y = scales()
    out = kit.points(x, y, series(3), kit.MEALS, tips=["a", "b", "c"])
    assert count(out, "circle") == 3 and out.count("data-tip=") == 3 and f'fill="{kit.MEALS}"' in out
    assert f'fill="{kit.BG}"' in kit.points(x, y, series(1), kit.MEALS, hollow=True)
    assert kit.points(x, y, [], kit.MEALS) == ""


def test_end_label_stays_inside_the_plot():
    p, x, y = scales(S)
    left = kit.end_label(p, x, y, END, 20, "20 left", kit.MEALS)  # at the right edge the label flips to the left
    assert 'text-anchor="end"' in left and count(left, "circle") == 1
    assert 'text-anchor="start"' in kit.end_label(p, x, y, START, 20, "20 left", kit.MEALS)


def test_stacked_bars_have_one_rect_per_segment_with_a_tooltip():
    p = kit.plot(L)
    x = kit.band_scale(["a", "b"], p.x0, p.x1)
    y = kit.linear_scale(0, 4, p.y0, p.y1)
    rows = [("a", [kit.Seg(2, kit.MEALS, "a: 2 swipes"), kit.Seg(1, kit.DOLLARS, "a: 1 meal")]), ("b", [kit.Seg(0, kit.MEALS), kit.Seg(3, kit.DOLLARS, "b: 3 meals")])]
    out = kit.vbars(p, x, y, rows, label=lambda v: f"{v:.0f}")
    assert count(out, "rect") == 3  # the empty segment is not drawn
    assert out.count("data-tip=") == 3 and 'data-tip="a: 2 swipes"' in out
    assert texts(out) == ["3", "3"]  # the total above each stack


def test_grouped_bars_sit_side_by_side_with_a_label_each():
    p = kit.plot(L)
    x = kit.band_scale(["a", "b"], p.x0, p.x1)
    y = kit.linear_scale(0, 4, p.y0, p.y1)
    rows = [("a", [kit.Seg(2, kit.MEALS), kit.Seg(1, kit.DOLLARS)]), ("b", [kit.Seg(4, kit.MEALS), kit.Seg(3, kit.DOLLARS)])]
    out = kit.vbars(p, x, y, rows, grouped=True, label=lambda v: f"{v:.0f}")
    xs = [float(v) for v in re.findall(r'<rect class="bar" x="([\d.]+)"', out)]
    assert len(xs) == 4 and xs[0] < xs[1] < xs[2] < xs[3]
    assert texts(out) == ["2", "1", "4", "3"]


def test_bar_labels_are_left_out_when_they_would_not_fit():
    p = kit.plot(S)
    days = [START + timedelta(days=i) for i in range(100)]
    x = kit.time_scale(days[0], days[-1], p.x0, p.x1)
    y = kit.linear_scale(0, 4, p.y0, p.y1)
    out = kit.vbars(p, x, y, [(d, [kit.Seg(2, kit.MEALS)]) for d in days], label=lambda v: f"{v:.2f}")
    assert count(out, "rect") == 100 and texts(out) == []


def test_horizontal_bars_label_the_end_of_each_bar():
    p = kit.plot(L, left=120, right=40)
    x = kit.linear_scale(0, 20, p.x0, p.x1)
    y = kit.band_scale(["Hall", "Cafe"], p.y1, p.y0)
    rows = [("Hall", [kit.Seg(12, kit.DOLLARS, "meals"), kit.Seg(6, kit.tint(kit.DOLLARS), "snacks")]), ("Cafe", [kit.Seg(4, kit.DOLLARS, "meals")])]
    out = kit.hbars(p, x, y, rows, label=lambda v: f"${v:.0f}")
    assert count(out, "rect") == 3 and texts(out) == ["$18", "$4"]
    grouped = kit.hbars(p, x, y, rows, grouped=True, label=lambda v: f"${v:.0f}")
    assert texts(grouped) == ["$12", "$6", "$4"]
    assert texts(kit.axis_y(p, y)) == ["Hall", "Cafe"]


def test_hbar_figure_names_every_row_at_every_size_and_handles_no_rows():
    rows = [("North Hall", [kit.Seg(12, kit.MEALS, "North Hall\n12 swipes")]), ("Cafe", [kit.Seg(4, kit.MEALS, "Cafe\n4 swipes")])]
    out = kit.hbar_figure(rows, "Swipes by place")
    assert count(out, "svg") == 3 and out.count(">North Hall<") == 3 and out.count(">12<") == 3
    assert "North Hall\n12 swipes" in out
    empty = kit.hbar_figure([], "Swipes by place", empty_text="No swipes yet.")
    assert empty.count("No swipes yet.") == 3 and count(empty, "rect") == 0


def test_heatmap_draws_every_cell_and_fades_with_value():
    x = kit.band_scale([8, 9, 10], 0, 300, pad=0)
    y = kit.band_scale(["Mon", "Tue"], 0, 100, pad=0)
    out = kit.heatmap(x, y, {(8, "Mon"): 4, (9, "Mon"): 1, (10, "Tue"): 0}, tip=lambda h, wd, v: f"{wd} {h}: {v:.0f}", label=lambda v: f"{v:.0f}")
    assert count(out, "rect") == 6 and out.count("cell blank") == 4
    strengths = [float(v) for v in re.findall(r'fill-opacity="([\d.]+)"', out)]
    assert strengths == [1.0, 0.44]
    assert 'data-tip="Mon 8: 4"' in out and texts(out) == ["4", "1"]
    assert count(kit.heatmap(x, y, {}), "rect") == 6  # nothing recorded: all blank, no crash


# --- axes, reference lines, notes ---------------------------------------------------------------


def test_grid_and_axes_label_sparsely():
    p, x, y = scales()
    grid = kit.grid_y(p, y, lambda v: f"${v:.0f}")
    assert texts(grid) == ["$0", "$50", "$100", "$150", "$200"] and grid.count('class="grid"') == 4 and grid.count('class="base"') == 1
    assert texts(kit.axis_x(p, x)) == ["Sep", "Oct", "Nov", "Dec"]
    band = kit.band_scale([f"Week {i}" for i in range(20)], p.x0, p.x1)
    shown = texts(kit.axis_x(kit.plot(S), kit.band_scale(band.keys, 40, 368)))
    assert 0 < len(shown) < 20 and shown[0] == "Week 0"  # some labels are skipped on a phone
    assert texts(kit.grid_x(p, kit.linear_scale(0, 10, p.x0, p.x1, count=2))) == ["0", "5", "10"]


def test_hline_is_labelled_and_skipped_when_off_the_plot():
    p, x, y = scales()
    out = kit.hline(p, y, 80, "1.6 a day", kit.MEALS, tip="pace")
    assert count(out, "line") == 1 and texts(out) == ["1.6 a day"] and 'data-tip="pace"' in out
    assert kit.hline(p, y, 999, "off the top") == ""


def test_notes_draw_lines_and_bands_and_never_overlap_labels():
    items = [
        kit.Note("today", date(2026, 10, 9), kind="today"),
        kit.Note("ends Dec 18", END, kind="end"),
        kit.Note("Thanksgiving recess", date(2026, 11, 25), date(2026, 11, 29), "away"),
        kit.Note("Reading days", date(2026, 12, 8), date(2026, 12, 9), "marker"),
        kit.Note("Final exams", date(2026, 12, 10), date(2026, 12, 18), "marker"),
        kit.Note("Next year", date(2027, 3, 1), kind="marker"),  # outside the axis: left out
    ]
    for size in (S, L):
        p, x, _ = scales(size)
        out = kit.notes(p, x, items)
        assert out.count('class="note-today"') == 1 and out.count('<rect class="note-away"') == 1 and "Next year" not in out
        assert 'data-tip="Thanksgiving recess\nNov 25–29"' in out
        labels = re.findall(r'<text x="([\d.]+)" y="([\d.]+)" text-anchor="(\w+)" class="note[^"]*"[^>]*>([^<]*)</text>', out)
        assert "today" in [t for *_, t in labels]  # today always gets its label
        boxes = []
        for lx, ly, anchor, label in labels:
            w = kit.text_width(label, kit.SMALL)
            left = float(lx) - (w if anchor == "end" else 0)
            assert left >= 0 and left + w <= size.w
            boxes.append((float(ly), left, left + w))
        for i, (ya, a0, a1) in enumerate(boxes):
            for yb, b0, b1 in boxes[i + 1 :]:
                assert ya != yb or a1 < b0 or b1 < a0, "two labels in one lane overlap"
    assert kit.notes(p, x, []) == ""


def test_semester_notes_come_from_the_config():
    from types import SimpleNamespace as NS

    cfg = NS(
        semester_end=END,
        away_periods=(NS(name="Recess", start=date(2026, 11, 25), end=date(2026, 11, 29), enabled=True), NS(name="Off", start=START, end=START, enabled=False)),
        markers=(NS(name="Exams", start=date(2026, 12, 10), end=END),),
    )
    kinds = [(n.kind, n.label) for n in kit.semester_notes(cfg, date(2026, 10, 9))]
    assert kinds == [("end", "ends Dec 18"), ("today", "today"), ("away", "Recess"), ("marker", "Exams")]
    assert [n.kind for n in kit.semester_notes(cfg, markers=False)] == ["end", "away"]


def test_series_labels_are_moved_apart():
    p = kit.plot(L, right=90)
    out = kit.series_labels(p, [(100.0, "this fall", kit.MEALS), (103.0, "last spring", kit.tint(kit.MEALS))])
    ys = [float(v) for v in re.findall(r'<text x="[\d.]+" y="([\d.]+)"', out)]
    assert texts(out) == ["this fall", "last spring"] and ys[1] - ys[0] >= 15 and 'class="leader"' in out
    assert kit.series_labels(p, []) == ""


def test_hover_columns_cover_the_plot_one_per_key():
    p, x, y = scales()
    out = kit.hover_columns(p, x, [(START, "Aug 21\n150 left"), (START + timedelta(days=1), "")])
    assert count(out, "rect") == 1 and 'class="hit"' in out and 'data-tip="Aug 21\n150 left"' in out
    band = kit.band_scale(["a", "b"], p.x0, p.x1)
    assert count(kit.hover_columns(p, band, [("a", "A"), ("b", "B")]), "rect") == 2


# --- the figure ---------------------------------------------------------------------------------


def test_figure_draws_three_sizes_with_a_label_and_legend():
    seen = []

    def draw(size):
        seen.append(size)
        return [kit.text(0, 10, "compact" if size.compact else "roomy"), None, ""]

    out = kit.figure(draw, 'Meals "left" by day', heights=(200, 250, 300), legend=kit.legend([kit.Key("what is left", kit.MEALS), kit.Key("pace", kit.DIM, "dash")]))
    assert isinstance(out, Markup) and out.startswith('<figure class="chart" tabindex="0"')
    assert [(s.name, s.w, s.h) for s in seen] == [("s", 380, 200), ("m", 660, 250), ("l", 1080, 300)]
    assert 'viewBox="0 0 380 200"' in out and 'viewBox="0 0 1080 300"' in out and 'class="v-m"' in out
    assert "Meals &#34;left&#34; by day" in out and out.count("compact") == 1 and out.count("roomy") == 2
    assert '<ul class="legend">' in out and 'class="k dash"' in out and "what is left" in out


def test_empty_says_so_in_words():
    out = kit.figure(lambda size: kit.empty(size, "No meals yet."), "Meals")
    assert out.count("No meals yet.") == 3


def test_tooltips_are_escaped():
    p, x, y = scales()
    out = kit.points(x, y, series(1), kit.MEALS, tips=['Bob\'s "Grill" <1>'])
    assert "<1>" not in out and "&lt;1&gt;" in out
