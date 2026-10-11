"""What every colour in the app means, in both themes. This is the only place a colour is written down.

The rule, in order:

    1. Something that judges:   GOOD (green), LOOK (amber, worth a look), PROBLEM (red),
                                UNKNOWN (slate gray, not enough data yet).
    2. Otherwise, by what the number measures:
                                MEALS (blue: meals and meal exchanges), DOLLARS (teal),
                                DAYS (navy: days, dates, weekdays, date ranges, streak lengths,
                                percent of the semester), PLACES (purple).

Nothing else gets a colour. Reference marks in charts (an even pace, today, days away) are the
theme's ink at some strength (`INK`, `DIM`) and are not in the key.

Two themes, `dark` and `light` (`THEMES`). A theme is a ground, an ink, a strength for each
neutral role (`ROLES`), and a chart shade for each meaning. The tiles are the same saturated
blocks with white text in both. Everything is derived from here:

    tiles           `Tile.kind` is a Meaning; the tile is painted with `kind.fill` (same in both themes)
    number band     each item names a `kind`; the block above the figure is `kind.fill`
    notes           each note names a `kind`; its square is `kind.fill`
    CSS             `css_vars()` writes every role and `--meals`, `--meals-mark`, ... for both themes;
                    metro.css only ever says `var(--role)`
    charts          `kit.MEALS` etc. are `Tone`s: the text `var(--meals-mark)`, so one SVG follows the
                    theme. `tint` is a CSS colour mix with the same meaning. `Tone.resolve(theme)`
                    gives the actual colour, for the contrast tests.
    the key         `KEY`, drawn by templates/partials/key.html at the bottom of every page

`fill` is the flat block white text sits on. `mark` is the same hue for thin marks drawn straight
on the page ground (lines, bars), chosen per theme so that it stands at least 3:1 against the
ground and so that blue and teal stay apart in one chart (OKLab distance 20 on dark, 18 on light;
19 and 17 for red-green colour blindness).

A verdict is never said by colour alone: amber cannot be told from red or green by everyone
(no amber that holds white text can), so each verdict also has a `sign` drawn on its tile, its
note and its square in the key.
"""

from __future__ import annotations

import re
from enum import Enum

MEASURES, VERDICTS = "what it measures", "how it's going"
ON_FILL = "#FFFFFF"  # text on a tile or any other block of a meaning's fill, in both themes


class Meaning(Enum):
    """The one thing a colour stands for: (slug, fill, label in the key, group in the key, sign)."""

    MEALS = ("meals", "#2672EC", "meals and exchanges", MEASURES, "")
    DOLLARS = ("dollars", "#008299", "dollars", MEASURES, "")
    DAYS = ("days", "#2B5797", "days and dates", MEASURES, "")
    PLACES = ("places", "#7E3878", "places", MEASURES, "")
    GOOD = ("good", "#008A00", "good", VERDICTS, "✓")  # a check
    LOOK = ("look", "#AD7A00", "worth a look", VERDICTS, "!")
    PROBLEM = ("problem", "#E51400", "a problem", VERDICTS, "✕")  # a cross
    UNKNOWN = ("unknown", "#525252", "not enough data", VERDICTS, "?")

    def __init__(self, slug: str, fill: str, label: str, group: str, sign: str):
        self.slug = slug
        self.fill = fill
        self.label = label
        self.group = group
        self.sign = sign

    @property
    def mark(self) -> Tone:
        """This meaning as a chart colour (follows the theme)."""
        return Tone(f"var(--{self.slug}-mark)", self, f"{self.slug}-mark")

    @property
    def judges(self) -> bool:
        return self.group == VERDICTS


#: Neutral roles: each is the theme's ink at a strength (alpha) over the ground.
#: Text roles must reach 4.5:1 (`TEXT_ROLES`), display and mark roles 3:1 (`MARK_ROLES`); the rest
#: are rules, grids and fills that carry no value.
ROLES = ("dim", "faint", "off", "ref", "blank", "rule", "grid", "axis", "wash", "soft", "hover", "edge")
TEXT_ROLES = ("ink", "dim", "faint")
MARK_ROLES = ("off", "ref", "axis")

#: theme -> ground, ink, role strengths, chart shade per meaning, and the two ends a tint mixes toward.
THEMES: dict[str, dict] = {
    "dark": {
        "scheme": "dark",
        "bg": "#1B0E45",  # deep indigo
        "ink": "#FFFFFF",
        "roles": {
            "dim": 0.74,  # secondary text, chart text
            "faint": 0.56,  # hints, the key
            "off": 0.42,  # pivot names that are not the current one
            "ref": 0.55,  # reference series: an even pace, an average
            "blank": 0.17,  # an empty slot in a chart (a day without a meal)
            "rule": 0.14,  # table and section rules
            "grid": 0.12,  # chart gridlines
            "axis": 0.45,  # chart baselines, ticks, leaders, control underlines
            "wash": 0.10,  # shaded date bands, quiet control fills
            "soft": 0.22,  # stronger fills: calendar markers, switch and slider tracks
            "hover": 0.16,  # hover fills
            "edge": 0.80,  # dotted marker edges
        },
        "marks": {
            Meaning.MEALS: "#2672EC", Meaning.DOLLARS: "#00A8A8", Meaning.DAYS: "#5F7FB8", Meaning.PLACES: "#A853A1",
            Meaning.GOOD: "#008A00", Meaning.LOOK: "#D69A00", Meaning.PROBLEM: "#E51400", Meaning.UNKNOWN: "#858585",
        },  # fmt: skip
        "up": "#FFFFFF",  # a positive tint goes away from the ground
        "down": "#000000",
    },
    "light": {
        "scheme": "light",
        "bg": "#F5F4FA",  # near-white with a little of the indigo in it
        "ink": "#1B0E45",  # the dark theme's ground is the light theme's ink
        "roles": {
            "dim": 0.78,
            "faint": 0.66,
            "off": 0.52,
            "ref": 0.58,
            "blank": 0.16,
            "rule": 0.16,
            "grid": 0.11,
            "axis": 0.50,
            "wash": 0.08,
            "soft": 0.20,
            "hover": 0.12,
            "edge": 0.75,
        },
        "marks": {
            Meaning.MEALS: "#2672EC", Meaning.DOLLARS: "#009494", Meaning.DAYS: "#2B5797", Meaning.PLACES: "#7E3878",
            Meaning.GOOD: "#008A00", Meaning.LOOK: "#AD7A00", Meaning.PROBLEM: "#E51400", Meaning.UNKNOWN: "#6B6B6B",
        },  # fmt: skip
        "up": "#000000",
        "down": "#FFFFFF",
    },
}
DEFAULT_THEME = "dark"  # used when the browser states no preference
BG = THEMES["dark"]["bg"]  # the dark theme's page background


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(c):02X}" for c in rgb)


def mix(a: str, b: str, share_of_b: float) -> str:
    """`a` with `share_of_b` (0..1) of `b` mixed in, in sRGB (what CSS `color-mix(in srgb, ...)` does)."""
    return _hex(x + (y - x) * share_of_b for x, y in zip(_rgb(a), _rgb(b)))


def role(theme: str, name: str) -> str:
    """The actual colour of a neutral role in a theme: the ink at the role's strength, over the ground."""
    t = THEMES[theme]
    if name in ("bg", "ink"):
        return t[name]
    return mix(t["bg"], t["ink"], t["roles"][name])


def luminance(color: str) -> float:
    r, g, b = (c / 255 for c in _rgb(color))
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two hex colours."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class Tone(str):
    """A colour a chart may draw with: CSS text that follows the theme and remembers what it means.

    `meaning` is None only for the neutral reference tones (ink, dimmed ink, the ground).
    The chart kit refuses any colour that is not a Tone, so a series cannot be painted with a
    made-up hue. `base` is the CSS variable behind it and `amount` the tint applied to it.
    """

    meaning: Meaning | None
    base: str
    amount: float

    def __new__(cls, value: str, meaning: Meaning | None = None, base: str = "", amount: float = 0.0) -> Tone:
        tone = super().__new__(cls, value)
        tone.meaning, tone.base, tone.amount = meaning, base, amount
        return tone

    def resolve(self, theme: str) -> str:
        """The hex colour this tone has in `theme`."""
        t = THEMES[theme]
        if self.meaning is None:
            return role(theme, self.base)
        color = t["marks"][self.meaning]
        if self.amount:
            color = mix(color, t["up" if self.amount > 0 else "down"], abs(self.amount))
        return color


#: Neutral tones for reference marks. Not meanings, not in the key.
INK = Tone("var(--ink)", None, "ink")
DIM = Tone("var(--ref)", None, "ref")
FAINT = Tone("var(--blank)", None, "blank")
SURFACE = Tone("var(--bg)", None, "bg")


def tint(color: Tone, amount: float = 0.45) -> Tone:
    """`color` moved away from the ground (toward it for a negative amount), keeping its meaning.

    On the dark theme a positive tint is lighter, on the light theme darker: in both it is the
    stronger partner of a ramp. For the partner of a series (snacks beside meals, last semester
    beside this one) and for one-hue ramps.
    """
    tone = need_tone(color)
    if tone.meaning is None:
        raise TypeError(f"only a tone with a meaning can be tinted, not {tone!r}")
    amount = max(-1.0, min(1.0, tone.amount + amount))
    if not amount:
        return tone.meaning.mark
    end = "up" if amount > 0 else "down"
    return Tone(f"color-mix(in srgb, var(--{tone.base}), var(--mix-{end}) {abs(amount) * 100:g}%)", tone.meaning, tone.base, amount)


def need_tone(color) -> Tone:
    """`color` if it is a Tone; otherwise a TypeError that says how to get one."""
    if not isinstance(color, Tone):
        raise TypeError(f"{color!r} is not a palette tone. Use kit.MEALS, kit.DOLLARS, ... or kit.tint(...) of one; see dinemaster/palette.py.")
    return color


def need_meaning(kind) -> Meaning:
    """The Meaning behind `kind` (a Meaning, or a Tone that has one); TypeError for anything else."""
    if isinstance(kind, Meaning):
        return kind
    if isinstance(kind, Tone) and kind.meaning is not None:
        return kind.meaning
    raise TypeError(f"{kind!r} does not name a meaning. Use one of dinemaster.palette.Meaning (MEALS, DOLLARS, DAYS, PLACES, GOOD, LOOK, PROBLEM, UNKNOWN).")


def fill(kind) -> str:
    """The block colour for `kind` (Jinja filter `fill`: number band, notes, the key)."""
    return need_meaning(kind).fill


_FILLS = {m.fill.upper(): m for m in Meaning}


def meaning_of(color: str) -> Meaning | None:
    """What a hex colour means, if it is a meaning's fill."""
    return _FILLS.get(color.upper())


#: The key at the bottom of every page: [(group title, [Meaning, ...]), ...].
KEY: tuple[tuple[str, tuple[Meaning, ...]], ...] = tuple(
    (group, tuple(m for m in Meaning if m.group == group)) for group in (MEASURES, VERDICTS)
)


def theme_vars(theme: str) -> dict[str, str]:
    """Every CSS variable a theme sets, name -> value."""
    t = THEMES[theme]
    r, g, b = _rgb(t["ink"])
    out = {"bg": t["bg"], "ink": t["ink"], "on-fill": ON_FILL, "mix-up": t["up"], "mix-down": t["down"]}
    out.update({name: f"rgba({r},{g},{b},{t['roles'][name]:g})" for name in ROLES})
    for m in Meaning:
        out[m.slug] = m.fill
        out[f"{m.slug}-mark"] = t["marks"][m]
    return out


def _block(selector: str, theme: str) -> str:
    body = ";".join(f"--{name}:{value}" for name, value in theme_vars(theme).items())
    return f"{selector}{{color-scheme:{THEMES[theme]['scheme']};{body}}}"


def css_vars() -> str:
    """The stylesheet of variables for both themes.

    `<html data-theme="light|dark">` picks one. With no attribute (the person has not chosen) the
    browser's `prefers-color-scheme` does.
    """
    other = "light" if DEFAULT_THEME == "dark" else "dark"
    return "\n".join([
        _block(":root", DEFAULT_THEME),
        f"@media (prefers-color-scheme: {other}){{{_block(':root:not([data-theme])', other)}}}",
        _block(f':root[data-theme="{other}"]', other),
        _block(f':root[data-theme="{DEFAULT_THEME}"]', DEFAULT_THEME),
    ])  # fmt: skip


VAR = re.compile(r"var\(--([\w-]+)")
