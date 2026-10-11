"""What every colour in the app means. This is the only place a colour is written down.

The rule, in order:

    1. Something that judges:   GOOD (green), LOOK (orange, worth a look), PROBLEM (red),
                                UNKNOWN (slate gray, not enough data yet).
    2. Otherwise, by what the number measures:
                                MEALS (blue: meals and meal exchanges), DOLLARS (teal),
                                DAYS (navy: days, dates, weekdays, date ranges, streak lengths,
                                percent of the semester), PLACES (purple).

Nothing else gets a colour. Reference marks in charts (an even pace, today, days away) are white
or gray (`INK`, `DIM`) and are not in the key.

Everything is derived from `Meaning`:

    tiles           `Tile.kind` is a Meaning; the tile is painted with `kind.fill`
    number band     each item names a `kind`; the block above the figure is `kind.fill`
    notes           each note names a `kind`; its square is `kind.fill`
    CSS             `css_vars()` writes `--meals`, `--meals-mark`, ... for metro.css (base.html inlines it)
    charts          `kit.MEALS` etc. are `Tone`s made from `kind.mark`; `tint` keeps the meaning
    the key         `KEY`, drawn by templates/partials/key.html at the bottom of every page

`fill` is the flat block white text sits on. `mark` is the same hue for thin marks drawn straight
on the page background (lines, bars). They differ only where the fill is too dark or too close
to a neighbour to read as a mark: chart teal is lighter than tile teal so that blue and teal
stay apart in one chart (checked for colour-blind viewers: OKLab distance 20 normal, 19 deutan).
"""

from __future__ import annotations

from enum import Enum

BG = "#1B0E45"  # the page background

MEASURES, VERDICTS = "what it measures", "how it's going"


class Meaning(Enum):
    """The one thing a colour stands for: (slug, fill, mark, label in the key, group in the key)."""

    MEALS = ("meals", "#2672EC", "#2672EC", "meals and exchanges", MEASURES)
    DOLLARS = ("dollars", "#008299", "#00A8A8", "dollars", MEASURES)
    DAYS = ("days", "#2B5797", "#5F7FB8", "days and dates", MEASURES)
    PLACES = ("places", "#7E3878", "#A853A1", "places", MEASURES)
    GOOD = ("good", "#008A00", "#008A00", "good", VERDICTS)
    LOOK = ("look", "#D24726", "#D24726", "worth a look", VERDICTS)
    PROBLEM = ("problem", "#E51400", "#E51400", "a problem", VERDICTS)
    UNKNOWN = ("unknown", "#525252", "#858585", "not enough data", VERDICTS)

    def __init__(self, slug: str, fill: str, mark: str, label: str, group: str):
        self.slug = slug
        self.fill = fill
        self._mark = mark
        self.label = label
        self.group = group

    @property
    def mark(self) -> Tone:
        """This meaning as a chart colour."""
        return Tone(self._mark, self)

    @property
    def judges(self) -> bool:
        return self.group == VERDICTS


class Tone(str):
    """A colour a chart may draw with: a hex string that remembers what it means.

    `meaning` is None only for the neutral reference tones (white, dimmed white, the background).
    The chart kit refuses any colour that is not a Tone, so a series cannot be painted with a
    made-up hue.
    """

    meaning: Meaning | None

    def __new__(cls, value: str, meaning: Meaning | None = None) -> Tone:
        tone = super().__new__(cls, value)
        tone.meaning = meaning
        if meaning is not None:
            _KNOWN[str(value).upper()] = meaning
        return tone


_KNOWN: dict[str, Meaning] = {}
for _m in Meaning:
    _KNOWN[_m.fill.upper()] = _m
    _KNOWN[_m._mark.upper()] = _m

#: Neutral tones for reference marks. Not meanings, not in the key.
INK = Tone("#FFFFFF")
DIM = Tone("rgba(255,255,255,.55)")
FAINT = Tone("rgba(255,255,255,.17)")
SURFACE = Tone(BG)


def tint(color: Tone, amount: float = 0.45) -> Tone:
    """`color` mixed toward white (or toward black for a negative amount), keeping its meaning.

    For the lighter partner of a series (snacks beside meals, last semester beside this one) and
    for one-hue ramps.
    """
    tone = need_tone(color)
    if not tone.startswith("#"):
        raise ValueError(f"only a hex tone can be tinted, not {tone!r}")
    target = 255 if amount >= 0 else 0
    r, g, b = (int(tone[i : i + 2], 16) for i in (1, 3, 5))
    mixed = "#" + "".join(f"{round(c + (target - c) * abs(amount)):02X}" for c in (r, g, b))
    return Tone(mixed, tone.meaning)


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


def meaning_of(color: str) -> Meaning | None:
    """What a hex colour means, if it is a palette colour or a tint made from one."""
    return _KNOWN.get(color.upper())


#: The key at the bottom of every page: [(group title, [Meaning, ...]), ...].
KEY: tuple[tuple[str, tuple[Meaning, ...]], ...] = tuple(
    (group, tuple(m for m in Meaning if m.group == group)) for group in (MEASURES, VERDICTS)
)


def css_vars() -> str:
    """`:root { --bg; --meals; --meals-mark; ... }` for the stylesheet."""
    parts = [f"--bg:{BG}"]
    for m in Meaning:
        parts += [f"--{m.slug}:{m.fill}", f"--{m.slug}-mark:{m._mark}"]
    return ":root{" + ";".join(parts) + "}"
