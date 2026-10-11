"""Which plan size fits: each plan the school sells against how many exchanges actually get used.

Pure functions over `Config` (its `[[plans]]`) and the meal-exchange forecast. The usage figure
is this semester's projected total, so the answer is only as good as this semester is typical.
"""

from __future__ import annotations

from dataclasses import dataclass

from dinemaster.config import Config
from dinemaster.forecast import PotForecast


@dataclass(frozen=True)
class PlanFit:
    """One plan against the projected number of exchanges used this semester.

    Fields:
        name, price: the plan and what it costs per semester.
        exchanges: exchanges it includes; None for an unlimited plan.
        dining_dollars: dining dollars it includes (counted as money back, not as cost).
        current: True for the plan in use now (same exchanges as `cfg.starting_me`).
        unused: exchanges that would go unused at the projected usage (0 when unlimited).
        short: meals the plan would not cover (0 when unlimited).
        extra: dollars to buy the `short` meals at `cfg.meal_price`.
        food_cost: `price - dining_dollars + extra` — what the meals cost in the end.
        per_meal: `food_cost / used`; None when nothing is projected to be used.
        versus_current: `food_cost` minus the current plan's; None when no plan is current.
    """

    name: str
    price: float
    exchanges: float | None
    dining_dollars: float
    current: bool
    unused: float
    short: float
    extra: float
    food_cost: float
    per_meal: float | None
    versus_current: float | None


@dataclass(frozen=True)
class PlanFits:
    """`plan_fits` result: `used` (with its likely range `used_lo..used_hi`) and one row per plan."""

    used: float
    used_lo: float
    used_hi: float
    rows: tuple[PlanFit, ...]
    reason: str | None


def plan_fits(cfg: Config, me: PotForecast) -> PlanFits:
    """Compare every plan in `cfg.plans` at the number of exchanges the forecast expects to be used."""
    if not cfg.plans:
        return PlanFits(0.0, 0.0, 0.0, (), "no plans listed — add [[plans]] entries to config.toml")
    if not me.available or me.leftover_p50 is None:
        return PlanFits(0.0, 0.0, 0.0, (), me.reason or "no forecast yet")

    used = max(0.0, cfg.starting_me - me.leftover_p50)
    used_lo = max(0.0, cfg.starting_me - me.leftover_hi)
    used_hi = max(0.0, cfg.starting_me - me.leftover_lo)

    rows = []
    for p in cfg.plans:
        unlimited = p.exchanges is None
        unused = 0.0 if unlimited else max(0.0, p.exchanges - used)
        short = 0.0 if unlimited else max(0.0, used - p.exchanges)
        extra = short * cfg.meal_price
        food_cost = p.price - p.dining_dollars + extra
        rows.append(
            PlanFit(
                name=p.name, price=p.price, exchanges=p.exchanges, dining_dollars=p.dining_dollars,
                current=not unlimited and abs(p.exchanges - cfg.starting_me) < 0.5,
                unused=unused, short=short, extra=extra, food_cost=food_cost,
                per_meal=food_cost / used if used > 0 else None, versus_current=None,
            )
        )
    mine = next((r for r in rows if r.current), None)
    if mine is not None:
        rows = [PlanFit(**{**r.__dict__, "versus_current": r.food_cost - mine.food_cost}) for r in rows]
    return PlanFits(used, used_lo, used_hi, tuple(rows), None)
