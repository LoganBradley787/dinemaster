"""The one chart of the `math` section: your own pace beside the paces the formulas work out."""

from __future__ import annotations

from markupsafe import Markup

from .. import fmt
from . import kit

#: The lighter partner of the meals colour: a pace the plan allows, beside the pace you keep.
ALLOWED = kit.tint(kit.MEALS, 0.5)


def pace_chart(own: float | None, me_only: float | None, with_dd: float | None, where: str) -> Markup:
    """Meals a day, as bars: your pace so far, the allowed paces, and the fixed two a day.

    `where` names the column the allowed paces come from ("leaving out days away"), for the tooltips.
    """

    def tip(name: str, v: float, more: str) -> str:
        return f"{name}\n{fmt.num(v, 2)} meals a day\n{more}"

    rows: list[tuple[str, list[kit.Seg]]] = []
    if own is not None:
        rows.append(("Your exchange pace so far", [kit.Seg(own, kit.MEALS, tip("Your exchange pace so far", own, "Exchanges used, divided by days gone by."))]))
    if me_only is not None:
        rows.append(("Allowed, exchanges only", [kit.Seg(me_only, ALLOWED, tip("Allowed, exchanges only", me_only, f"Exchanges left, divided by days to go, {where}."))]))
    if with_dd is not None:
        rows.append(("Allowed, counting dining dollars", [kit.Seg(with_dd, ALLOWED, tip("Allowed, counting dining dollars", with_dd, f"Both pots as meals, divided by days to go, {where}."))]))
    if rows:
        rows.append(("Two meals a day", [kit.Seg(2.0, kit.DIM, tip("Two meals a day", 2.0, "The fixed pace the run-out block uses."))]))
    return kit.hbar_figure(
        rows,
        "Meals a day: your exchange pace so far, the paces the plan allows, and two a day",
        fmt=lambda v: fmt.num(v, 2),
        legend_keys=[kit.Key("your pace", kit.MEALS, "box"), kit.Key("what the plan allows", ALLOWED, "box"), kit.Key("a fixed pace, for reference", kit.DIM, "box")],
        empty_text="No pace to show. The semester is over.",
    )  # fmt: skip
