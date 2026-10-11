"""Every colour means one thing: the key, the stylesheet, the number band, the notes and the charts
all take their colours from dinemaster/palette.py, and nothing else on a page has one."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from dinemaster import palette
from dinemaster.palette import Meaning as M
from dinemaster.web import fmt
from dinemaster.web.charts import kit
from dinemaster.web.charts import now as charts
from dinemaster.web.context import load_context
from dinemaster.web.sections import SECTIONS

from conftest import AS_OF

WEB = Path(fmt.__file__).parent
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b")
PAGES = ["/", "/kit", *[s.path for s in SECTIONS]]
LABELS = ["meals and exchanges", "dollars", "days and dates", "places", "good", "worth a look", "a problem", "not enough data"]


def gray(color: str) -> bool:
    """White, black or a gray: a neutral, which needs no meaning."""
    h = color.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    return h[0:2].lower() == h[2:4].lower() == h[4:6].lower()


def means_something(color: str) -> bool:
    return gray(color) or color.upper() == palette.BG or palette.meaning_of(color) is not None


# --- the key ------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGES)
def test_the_key_is_the_last_thing_on_every_page(client, empty_client, path):
    for c in (client, empty_client):
        html = c.get(path).text
        assert html.count('<footer class="key"') == 1 and html.index("</main>") < html.index('<footer class="key"')
        key = html[html.index('<footer class="key"') : html.index("</footer>")]
        assert re.findall(r'<span class="key-title">([^<]+)</span>', key) == ["what it measures", "how it&#39;s going"]
        assert re.findall(r"</i>([^<]+)</li>", key) == LABELS
        squares = re.findall(r'<i style="--c:(#[0-9A-F]{6})" data-kind="(\w+)">', key)
        assert squares == [(m.fill, m.slug) for m in M]
        assert "—" not in key and key == key.replace("Meals", "meals")  # plain and lowercase


def test_the_key_lists_exactly_the_meanings_that_exist():
    listed = [m for _, group in palette.KEY for m in group]
    assert listed == list(M) and [m.label for m in listed] == LABELS
    assert [m.judges for m in listed] == [False] * 4 + [True] * 4


# --- one source of truth ------------------------------------------------------------------------


def test_the_stylesheet_gets_its_colours_from_the_palette(client):
    served = client.get("/static/palette.css")
    assert served.status_code == 200 and served.headers["content-type"].startswith("text/css")
    assert served.text == palette.css_vars() and f"--bg:{palette.BG}" in served.text
    for m in M:
        assert f"--{m.slug}:{m.fill}" in served.text and f"--{m.slug}-mark:{m.mark}" in served.text
    assert '<link rel="stylesheet" href="/static/palette.css">' in client.get("/now").text
    css = (WEB / "static" / "metro.css").read_text()
    assert all(gray(c) for c in HEX.findall(css)), "metro.css may only write white, black and grays; a colour is var(--meaning)"
    assert set(re.findall(r"rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)", css)) <= {("255", "255", "255"), ("0", "0", "0")}
    defined = set(re.findall(r"--([\w-]+):", served.text))
    used = set(re.findall(r"var\(--([\w-]+)", css))
    local = set(re.findall(r"--([\w-]+)\s*:", css)) | {"c", "i", "n", "t", "d", "fill"}  # set in markup: a colour slot, stagger and flip timings
    assert used <= defined | local, used - defined - local
    assert {"bg", "look", "problem"} <= used
    for gone in ("blue", "navy", "teal", "amber", "crimson", "violet", "magenta", "orange", "purple", "slate"):
        assert f"--{gone}" not in css


def test_no_colour_is_written_outside_the_palette():
    """Page code, chart code, templates and script: none of them spell a colour."""
    for path in [*WEB.rglob("*.py"), *WEB.rglob("*.html"), *WEB.rglob("*.js")]:
        text = path.read_text()
        assert not [c for c in HEX.findall(text) if not gray(c)], path
        assert "rgba(" not in text and "rgb(" not in text, path
    old_names = re.compile(r"\b(VIOLET|MAGENTA|CRIMSON|AMBER|POOLED|kit\.ME|kit\.DD|kit\.(BLUE|NAVY|TEAL|GREEN|RED|ORANGE|PURPLE|SLATE))\b")
    for path in [*WEB.rglob("*.py"), WEB.parent / "tiles.py"]:
        assert not old_names.search(path.read_text()), path


@pytest.mark.parametrize("path", ["/", "/now", "/now?unit=meals&away=0", "/kit", "/data"])
def test_every_colour_on_a_page_means_something(site, path):
    for today in (AS_OF, date(2026, 10, 1)):  # fresh data, then stale data
        html = site.client(today=today).get(path).text
        strangers = sorted({c for c in HEX.findall(html) if not means_something(c)})
        assert not strangers, f"{path}: {strangers} are not in the palette"
        assert "rgba(" not in html.replace(palette.DIM, "").replace(palette.FAINT, "")


def test_vanished_hues_are_gone(client):
    for path in ("/", "/now", "/kit"):
        html = client.get(path).text.upper()
        for gone in ("#603CBA", "#C1004F", "#B91D47", "#D2740A", "#00A3BF", "#E0569A"):
            assert gone not in html, (path, gone)


# --- the number band and the notes name a meaning -----------------------------------------------


def macros():
    env = Environment(loader=FileSystemLoader(WEB / "templates"), autoescape=True)
    fmt.register(env)
    return env.get_template("partials/ui.html").module


def test_band_and_notes_take_a_meaning_and_nothing_else():
    ui = macros()
    band = str(ui.band([{"value": "3", "label": "places", "kind": M.PLACES}, {"value": "$5", "label": "spent", "kind": kit.DOLLARS}]))
    assert 'data-kind="places" style="--c:#7E3878"' in band and 'data-kind="dollars" style="--c:#008299"' in band  # the tile teal, not the chart teal
    note = str(ui.notes([{"kind": M.PROBLEM, "text": "Old data."}]))
    assert '<li class="problem" style="--c:#E51400">' in note
    for bad in ({"value": "1", "label": "x", "color": "#603CBA"}, {"value": "1", "label": "x", "kind": "#603CBA"}, {"value": "1", "label": "x", "kind": kit.DIM}):
        with pytest.raises(TypeError):
            ui.band([bad])
    with pytest.raises(TypeError):
        ui.notes([{"level": "info", "text": "x"}])


def test_now_band_and_notes(site):
    html = site.client().get("/now").text
    assert re.findall(r'<p class="stat" data-kind="(\w+)"', html) == ["meals", "dollars", "days", "look", "look"]
    assert re.findall(r'<li class="(\w+)" style="--c:(#\w+)">', html) == [("days", "#2B5797"), ("dollars", "#008299")]
    stale = site.client(today=date(2026, 10, 1)).get("/now").text
    assert ("problem", "#E51400") in re.findall(r'<li class="(\w+)" style="--c:(#\w+)">', stale)


def test_the_verdict_keeps_a_date_together(client):
    assert "unused on Dec 18.</h1>" in client.get("/now").text


# --- charts -------------------------------------------------------------------------------------


def test_the_kit_refuses_a_colour_that_is_not_a_tone():
    p = kit.plot(kit.Size("l", 1080.0, 340.0))
    x, y = kit.band_scale(["a"], p.x0, p.x1), kit.linear_scale(0, 10, p.y0, p.y1)
    for bad in ("#603CBA", "purple", "rgba(255,255,255,.5)"):
        for draw in (
            lambda: kit.line(x, y, [("a", 1.0)], bad),
            lambda: kit.area_band(x, y, [("a", 1.0)], [("a", 2.0)], bad),
            lambda: kit.points(x, y, [("a", 1.0)], bad),
            lambda: kit.end_label(p, x, y, "a", 1.0, "1", bad),
            lambda: kit.hline(p, y, 5, "five", bad),
            lambda: kit.series_labels(p, [(10.0, "a", bad)]),
            lambda: kit.heatmap(x, kit.band_scale(["r"], p.y1, p.y0), {("a", "r"): 1.0}, bad),
            lambda: kit.Seg(1.0, bad),
            lambda: kit.Key("a", bad),
            lambda: kit.tint(bad),
        ):
            with pytest.raises(TypeError):
                draw()
    assert kit.MEALS == M.MEALS.fill and kit.MEALS.meaning is M.MEALS and kit.DIM.meaning is None


def test_meals_are_blue_and_dollars_teal_in_charts(client):
    html = client.get("/now").text
    me = html[html.index('id="meal-exchanges"') : html.index('id="dining-dollars"')]
    dd = html[html.index('id="dining-dollars"') : html.index('id="both"')]
    both = html[html.index('id="both"') : html.index('id="day-by-day"')]
    assert f'stroke="{kit.MEALS}"' in me and kit.DOLLARS not in me
    assert f'stroke="{kit.DOLLARS}"' in dd and kit.MEALS not in dd
    assert f'stroke="{kit.MEALS}"' in both and kit.DOLLARS not in both  # both pots counted as meals: meals
    places = html[html.index('id="places"') :]
    assert f'fill="{kit.MEALS}"' in places and f'fill="{kit.DOLLARS}"' in places and f'fill="{kit.tint(kit.DOLLARS, 0.5)}"' in places


def test_blue_and_teal_in_one_chart_differ_by_more_than_colour(client):
    html = client.get("/now").text
    daily = html[html.index('id="day-by-day"') : html.index('id="week-by-week"')]
    assert f'fill="{kit.MEALS}"' in daily and f'fill="{kit.DOLLARS}"' in daily
    assert daily.count('class="stripe"') >= 3  # dining-dollar meals are striped as well as teal
    assert f'<i class="k stripe" style="--c:{kit.DOLLARS}"></i>dining-dollar meals' in daily
    assert f'<i class="k box" style="--c:{kit.MEALS}"></i>exchange swipes' in daily
    assert f'<i class="k dash" style="--c:{kit.DIM}"></i>the pace that uses everything' in daily  # reference marks are gray
    assert "1 dining-dollar meal" in daily  # and the tooltip names it


def test_striped_bars(site):
    p = kit.plot(kit.Size("l", 1080.0, 340.0))
    x, y = kit.band_scale(["a"], p.x0, p.x1), kit.linear_scale(0, 4, p.y0, p.y1)
    plain = kit.vbars(p, x, y, [("a", [kit.Seg(2, kit.MEALS), kit.Seg(1, kit.DOLLARS)])])
    striped = kit.vbars(p, x, y, [("a", [kit.Seg(2, kit.MEALS), kit.Seg(1, kit.DOLLARS, striped=True)])])
    assert 'class="stripe"' not in plain and striped.count('class="stripe"') >= 5 and striped.count('class="bar"') == 2
    ctx = load_context(site.config_path, today=AS_OF)
    assert 'class="stripe"' in charts.daily_meals_chart(ctx.cfg, ctx.daily, 1.5, 1.7)


def srgb_to_oklab(color: str) -> tuple[float, float, float]:
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b))
    l, m, s = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b, 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
               0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)  # fmt: skip
    l, m, s = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)  # fmt: skip


def test_chart_blue_and_teal_are_far_enough_apart():
    """OKLab distance (x100) of at least 15: the floor below which two series read as one."""
    a, b = srgb_to_oklab(kit.MEALS), srgb_to_oklab(kit.DOLLARS)
    assert 100 * sum((u - v) ** 2 for u, v in zip(a, b)) ** 0.5 >= 15
    tile_teal = srgb_to_oklab(M.DOLLARS.fill)
    assert 100 * sum((u - v) ** 2 for u, v in zip(tile_teal, b)) ** 0.5 < 15  # and still the tile's teal
