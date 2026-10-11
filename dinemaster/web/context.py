"""Everything a page needs for one request: `PageContext`, built by `load_context`.

The order config is put together in:

    config.toml  ->  saved settings (data/settings.json)  ->  calendar (.ics away periods, markers)
                 ->  saved away-period switches  ->  view parameters from the URL (as_of, away)

Data is tiny, so every request reads the files again; nothing is kept between requests except
the calendar result, which `CalendarCache` remembers for a few minutes so a slow or dead feed
is not retried on every page.

The heavier results (`metrics`, `forecast`, `daily`, `tiles`) are worked out the first time a
page asks for them, so a page that needs only the config does not pay for a forecast.
"""

from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd

from .. import forecast as fcast
from .. import metrics as m
from ..calendar_sync import CalendarResult, apply_calendar
from ..config import ROOT, Config, load_config
from ..freshness import at_observed, data_through, days_behind, is_stale, observed_through
from ..ingest import IngestReport, load_transactions
from . import settings


def default_config_path() -> Path:
    """config.toml, or the file named by the DINEMASTER_CONFIG environment variable."""
    return Path(os.environ.get("DINEMASTER_CONFIG", ROOT / "config.toml"))


@dataclass(frozen=True)
class ViewParams:
    """The view parameters that travel in the query string of every internal link.

    as_of: look at a past day (None = today).  away: override `cfg.exclude_away` (None = the setting).
    """

    as_of: date | None = None
    away: bool | None = None

    @classmethod
    def parse(cls, query: Mapping[str, str]) -> ViewParams:
        """Read `as_of=YYYY-MM-DD` and `away=0|1`; anything else, or a bad value, is ignored."""
        as_of = away = None
        try:
            as_of = date.fromisoformat(query["as_of"]) if query.get("as_of") else None
        except ValueError:
            pass
        if query.get("away") in ("0", "1"):
            away = query["away"] == "1"
        return cls(as_of, away)

    def query(self) -> dict[str, str]:
        out = {}
        if self.as_of is not None:
            out["as_of"] = self.as_of.isoformat()
        if self.away is not None:
            out["away"] = "1" if self.away else "0"
        return out


class CalendarCache:
    """Remembers `apply_calendar`'s result until the config or an .ics file changes, or `ttl` passes."""

    def __init__(self, ttl: float = 600.0):
        self.ttl = ttl
        self._kept: dict[tuple, tuple[float, tuple, tuple, CalendarResult]] = {}

    def clear(self) -> None:
        self._kept.clear()

    def merge(self, cfg: Config, config_path: Path) -> tuple[Config, CalendarResult]:
        folder = cfg.calendar.ics_dir
        files: tuple = ()
        if folder is not None and folder.is_dir():
            files = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(folder.glob("*.ics")))
        signature = (str(config_path), config_path.stat().st_mtime_ns, files, cfg.semester_start, cfg.semester_end, cfg.away_periods)
        now = time.monotonic()
        self._kept = {k: v for k, v in self._kept.items() if now - v[0] <= self.ttl}
        if signature not in self._kept:
            merged, result = apply_calendar(cfg)
            self._kept[signature] = (now, merged.away_periods, merged.markers, result)
        _, away_periods, markers, result = self._kept[signature]
        return dataclasses.replace(cfg, away_periods=away_periods, markers=markers), result


class PageContext:
    """One request's view of the data. Pages read from it; they never load files themselves.

    Plain attributes:
        cfg            the config in force (settings, calendar and view parameters applied)
        base_cfg       the same without the view parameters (what "reset" goes back to)
        file_cfg       config.toml alone, with the calendar merged in (what "Reset to config.toml" gives)
        df             every transaction (`ingest.load_transactions`); `has_data` is False when empty
        report         the ingest report
        calendar       the `CalendarResult` (what the .ics files offered, warnings)
        params         the `ViewParams` from the URL
        today          the real calendar day
        saved          the saved settings as loaded (a dict; {} when none)
        config_path, settings_path

    Worked out on first use: `observed_through`, `at_observed`, `through`, `behind`, `stale`,
    `now`, `counts`, `metrics`, `forecast`, `daily`, `tiles`.
    """

    def __init__(
        self,
        *,
        config_path: Path,
        cfg: Config,
        base_cfg: Config,
        file_cfg: Config,
        df: pd.DataFrame,
        report: IngestReport,
        calendar: CalendarResult,
        params: ViewParams,
        today: date,
        saved: dict,
        calendar_cache: CalendarCache | None = None,
    ):
        self.config_path = config_path
        self.cfg = cfg
        self.base_cfg = base_cfg
        self.file_cfg = file_cfg
        self.df = df
        self.report = report
        self.calendar = calendar
        self.params = params
        self.today = today
        self.saved = saved
        self.calendar_cache = calendar_cache
        self.settings_path = settings.path_for(file_cfg)

    # --- links --------------------------------------------------------------------------------

    def url(self, path: str, **extra) -> str:
        """An internal link that keeps the view parameters. `extra` adds or replaces query values;
        a value of None removes one: `ctx.url("/now", unit="meals")`, `ctx.url("/now", as_of=None)`."""
        query: dict[str, str] = self.params.query()
        for key, value in extra.items():
            if value is None:
                query.pop(key, None)
            else:
                query[key] = value.isoformat() if isinstance(value, date) else str(value)
        path, _, fragment = path.partition("#")
        return path + ("?" + urlencode(query) if query else "") + ("#" + fragment if fragment else "")

    @property
    def view_changed(self) -> bool:
        """True when the URL asks for a past day or overrides the away setting."""
        p = self.params
        return (p.as_of is not None and p.as_of != self.today) or (p.away is not None and p.away != self.base_cfg.exclude_away)

    # --- the data -----------------------------------------------------------------------------

    @property
    def has_data(self) -> bool:
        return not self.df.empty

    @property
    def variant(self) -> str:
        """Which half of the plain/away metric pairs is in force: "away" or "plain"."""
        return "away" if self.cfg.exclude_away else "plain"

    @cached_property
    def observed_through(self) -> date:
        """The last day whose usage is known (see `freshness.observed_through`)."""
        return observed_through(self.cfg, self.df)

    @cached_property
    def at_observed(self) -> Config:
        """`cfg` with its as-of date pulled back to the last observed day; metrics are worked out here."""
        return at_observed(self.cfg, self.df)

    @cached_property
    def through(self) -> date | None:
        """The last day the exports cover for every pot, or None with no data."""
        return data_through(self.cfg, self.df)

    @cached_property
    def behind(self) -> int:
        """Days between the last observed day and the as-of date."""
        return days_behind(self.cfg, self.df)

    @cached_property
    def stale(self) -> bool:
        return is_stale(self.cfg, self.df)

    @cached_property
    def now(self) -> m.DayCounts:
        """The calendar at the as-of date: progress, days to go."""
        return m.day_counts(self.cfg)

    @cached_property
    def counts(self) -> m.DayCounts:
        """The calendar at the last observed day (`counts.a` is that day)."""
        return m.day_counts(self.at_observed)

    @cached_property
    def metrics(self) -> m.Metrics:
        return m.compute_metrics(self.df, self.at_observed)

    @cached_property
    def forecast(self) -> fcast.Forecast:
        return fcast.forecast(self.df, self.cfg)

    @cached_property
    def daily(self) -> pd.DataFrame:
        """`metrics.daily` through the last observed day."""
        return m.daily(self.df, self.at_observed)

    @cached_property
    def tiles(self):
        """`tiles.gather(...)`: everything the Start screen shows."""
        from .. import tiles

        return tiles.gather(self.cfg, self.df, self.today)


def load_context(
    config_path: Path | None = None,
    params: ViewParams | Mapping[str, str] | None = None,
    today: date | None = None,
    calendar_cache: CalendarCache | None = None,
) -> PageContext:
    """Read config, settings, calendar and transactions and build the `PageContext` for one request.

    `params` is the request's query string (or a `ViewParams`). `today` pins the clock in tests.
    """
    config_path = Path(config_path) if config_path is not None else default_config_path()
    if not isinstance(params, ViewParams):
        params = ViewParams.parse(params or {})
    today = today or date.today()
    cache = calendar_cache or CalendarCache()

    from_file = dataclasses.replace(load_config(config_path, root=config_path.resolve().parent), as_of=today)
    saved = settings.load(settings.path_for(from_file))
    file_cfg, _ = cache.merge(from_file, config_path)
    base_cfg, calendar = cache.merge(settings.apply(from_file, saved), config_path)
    base_cfg = settings.apply_away(base_cfg, saved)

    changes: dict = {}
    if params.as_of is not None:
        changes["as_of"] = params.as_of
    if params.away is not None:
        changes["exclude_away"] = params.away
    cfg = dataclasses.replace(base_cfg, **changes) if changes else base_cfg

    df, report = load_transactions(cfg)
    return PageContext(
        config_path=config_path, cfg=cfg, base_cfg=base_cfg, file_cfg=file_cfg, df=df, report=report, calendar=calendar,
        params=params, today=today, saved=saved, calendar_cache=calendar_cache,
    )  # fmt: skip
