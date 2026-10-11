"""Charts for the `data` section."""

from __future__ import annotations

from markupsafe import Markup

from .. import fmt
from . import kit

#: Snacks sit beside meals in the same measure (dining dollars), so they take a lighter teal.
SNACKS = kit.tint(kit.DOLLARS, 0.5)


def dd_split_chart(by_location: dict) -> Markup:
    """Dining dollars at each place, split into meal-sized and snack-sized purchases. Biggest first."""
    places = sorted(by_location.items(), key=lambda kv: -(kv[1].get("meal_amount", 0.0) + kv[1].get("snack_amount", 0.0)))
    rows = []
    for place, v in places:
        meals, snacks = v.get("meal_amount", 0.0), v.get("snack_amount", 0.0)
        if meals + snacks <= 0:
            continue
        segs = []
        if meals > 0:
            segs.append(kit.Seg(meals, kit.DOLLARS, f"{place}\n{fmt.cents(meals)} on {fmt.plural(v.get('meal_count', 0), 'meal')}"))
        if snacks > 0:
            segs.append(kit.Seg(snacks, SNACKS, f"{place}\n{fmt.cents(snacks)} on {fmt.plural(v.get('snack_count', 0), 'snack')}"))
        rows.append((str(place), segs))
    return kit.hbar_figure(
        rows,
        "Dining dollars spent at each place, split into meals and snacks",
        fmt=fmt.cents,
        legend_keys=[kit.Key("counted as a meal", kit.DOLLARS, "box"), kit.Key("counted as a snack", SNACKS, "box")],
        empty_text="No dining-dollar purchases yet.",
    )
