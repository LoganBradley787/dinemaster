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
    source: str = "config"  # "config" | "calendar"

    def contains(self, d: date) -> bool:
        return self.enabled and self.start <= d <= self.end


@dataclass(frozen=True)
class PotRule:
    pattern: str
    pot: str  # "ME" | "DD"


@dataclass(frozen=True)
class ExportAccount:
    name: str  # account name as it appears in export file names
    acct: str  # the dining site's account number


@dataclass(frozen=True)
class ExportConfig:
    statement_path: str = "/statementdetail.php"
    session_params: tuple[str, ...] = ("cid", "skey")
    start_param: str = "startdate"
    end_param: str = "enddate"
    account_param: str = "acct"
    accounts: tuple[ExportAccount, ...] = ()


@dataclass(frozen=True)
class Marker:
    """A dated note shown on charts (reading days, exams); does not affect day counting."""

    name: str
    start: date
    end: date


@dataclass(frozen=True)
class ForecastConfig:
    half_life_days: float = 21.0
    prior_days: float = 2.0
    simulations: int = 2000
    seed: int = 7
    band: tuple[int, int] = (10, 90)


@dataclass(frozen=True)
class CalendarConfig:
    ics_dir: Path | None = None
    ics_urls: tuple[str, ...] = ()
    away_patterns: tuple[str, ...] = ()
    marker_patterns: tuple[str, ...] = ()
    cache_hours: float = 24.0
    cache_dir: Path | None = None


@dataclass(frozen=True)
class HabitsConfig:
    late_night_start: int = 21
    late_night_end: int = 4


@dataclass(frozen=True)
class SemesterDef:
    name: str
    start: date
    end: date


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
    export: ExportConfig = ExportConfig()
    forecast: ForecastConfig = ForecastConfig()
    max_meals_per_day: int = 3
    calendar: CalendarConfig = CalendarConfig()
    markers: tuple[Marker, ...] = ()  # filled in by calendar_sync.apply_calendar
    habits: HabitsConfig = HabitsConfig()
    history: tuple[SemesterDef, ...] = ()

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
    exp = c.get("export", {})
    fc, cal, hab = c.get("forecast", {}), c.get("calendar", {}), c.get("habits", {})
    ledger_path = root / files.get("ledger_path", "data/ledger.csv")
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
        ledger_path=ledger_path,
        account_regex=files.get("account_regex", r"^(?P<account>.+?)_statement"),
        columns=dict(files["columns"]),
        pots=tuple(PotRule(p["pattern"], p["pot"]) for p in files.get("pots", [])),
        export=ExportConfig(
            statement_path=exp.get("statement_path", "/statementdetail.php"),
            session_params=tuple(exp.get("session_params", ["cid", "skey"])),
            start_param=exp.get("start_param", "startdate"),
            end_param=exp.get("end_param", "enddate"),
            account_param=exp.get("account_param", "acct"),
            accounts=tuple(ExportAccount(a["name"], str(a["acct"])) for a in exp.get("accounts", [])),
        ),
        forecast=ForecastConfig(
            half_life_days=float(fc.get("half_life_days", 21.0)),
            prior_days=float(fc.get("prior_days", 2.0)),
            simulations=int(fc.get("simulations", 2000)),
            seed=int(fc.get("seed", 7)),
            band=tuple(fc.get("band", [10, 90])),
        ),
        max_meals_per_day=int(c.get("budget", {}).get("max_meals_per_day", 3)),
        calendar=CalendarConfig(
            ics_dir=root / cal["ics_dir"] if cal.get("ics_dir") else None,
            ics_urls=tuple(cal.get("ics_urls", [])),
            away_patterns=tuple(cal.get("away_patterns", [])),
            marker_patterns=tuple(cal.get("marker_patterns", [])),
            cache_hours=float(cal.get("cache_hours", 24.0)),
            cache_dir=ledger_path.parent / "calendar-cache",
        ),
        habits=HabitsConfig(int(hab.get("late_night_start", 21)), int(hab.get("late_night_end", 4))),
        history=tuple(SemesterDef(h["name"], h["start"], h["end"]) for h in c.get("history", {}).get("semesters", [])),
    )
