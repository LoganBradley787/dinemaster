"""Load config.toml into a Config dataclass. The sidebar builds modified copies via dataclasses.replace."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class AwayPeriod:
    name: str
    start: date
    end: date
    enabled: bool = True

    def contains(self, d: date) -> bool:
        return self.enabled and self.start <= d <= self.end


@dataclass(frozen=True)
class PotRule:
    pattern: str
    pot: str  # "ME" | "DD"


@dataclass(frozen=True)
class Config:
    semester_start: date
    semester_end: date
    data_cutoff: date
    starting_me: float
    starting_dd: float
    meal_price: float
    dd_rollover: bool
    dd_leftover_flag: float
    rounding_mode: str
    rounding_granularity: float
    exclude_away: bool
    away_periods: tuple[AwayPeriod, ...]
    meal_threshold: float
    snack_patterns: tuple[str, ...]
    load_patterns: tuple[str, ...]
    adjustment_patterns: tuple[str, ...]
    raw_dir: Path
    ledger_path: Path
    account_regex: str
    columns: dict[str, str] = field(hash=False)
    pots: tuple[PotRule, ...] = ()
    as_of: date = field(default_factory=date.today)

    def round_meals(self, x: float) -> float:
        """Convert a fractional meal count to whole units of `rounding_granularity` using `rounding_mode`."""
        g = self.rounding_granularity or 1.0
        q = x / g
        if self.rounding_mode == "ceil":
            return math.ceil(q - 1e-9) * g
        if self.rounding_mode == "round":
            return round(q) * g
        return math.floor(q + 1e-9) * g  # epsilon guards float noise like 19.999999

    def is_away(self, d: date) -> bool:
        return any(p.contains(d) for p in self.away_periods)


def load_config(path: Path | str = ROOT / "config.toml", root: Path = ROOT) -> Config:
    with open(path, "rb") as f:
        c = tomllib.load(f)
    sem, plan, away, cls, files = c["semester"], c["plan"], c.get("away", {}), c["classification"], c["files"]
    rounding = plan.get("dd_rounding", {})
    return Config(
        semester_start=sem["start"],
        semester_end=sem["end"],
        data_cutoff=sem.get("data_cutoff", sem["start"]),
        starting_me=float(plan["starting_me"]),
        starting_dd=float(plan["starting_dd"]),
        meal_price=float(plan["meal_price"]),
        dd_rollover=bool(plan.get("dd_rollover", False)),
        dd_leftover_flag=float(plan.get("dd_leftover_flag", plan["meal_price"])),
        rounding_mode=rounding.get("mode", "floor"),
        rounding_granularity=float(rounding.get("granularity", 1.0)),
        exclude_away=bool(away.get("exclude", True)),
        away_periods=tuple(
            AwayPeriod(p["name"], p["start"], p["end"], bool(p.get("enabled", True)))
            for p in away.get("periods", [])
        ),
        meal_threshold=float(cls.get("meal_threshold", 8.0)),
        snack_patterns=tuple(cls.get("snack_patterns", [])),
        load_patterns=tuple(cls.get("load_patterns", ["^Deposit$"])),
        adjustment_patterns=tuple(cls.get("adjustment_patterns", [])),
        raw_dir=root / files.get("raw_dir", "raw-data"),
        ledger_path=root / files.get("ledger_path", "data/ledger.csv"),
        account_regex=files.get("account_regex", r"^(?P<account>.+?)_statement"),
        columns=dict(files["columns"]),
        pots=tuple(PotRule(p["pattern"], p["pot"]) for p in files.get("pots", [])),
    )
