"""How current the data is: the last day the exports cover, not the last day with a transaction.

A day with no swipes is still covered if an export's date range includes it, so freshness comes
from each export file's range end (clamped, since the site writes impossible dates like 09-31)
and when it was downloaded, whichever is earlier.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .config import Config
from .ingest import _pot_for_account

_RANGE_END = re.compile(r"_to_(\d{4})-(\d{2})-(\d{2})")


def file_coverage(path: Path) -> date:
    """Last day this export can vouch for: min(range end in its name, the day it was downloaded)."""
    downloaded = datetime.fromtimestamp(path.stat().st_mtime).date()
    match = _RANGE_END.search(path.stem)
    if not match:
        return downloaded
    year, month, day = (int(g) for g in match.groups())
    if not 1 <= month <= 12:
        return downloaded
    end = date(year, month, min(day, calendar.monthrange(year, month)[1]))
    return min(end, downloaded)


def coverage_by_pot(cfg: Config, df: pd.DataFrame) -> dict[str, date]:
    """Per pot, the latest day covered by any of its accounts' exports (or its last transaction)."""
    covered: dict[str, date] = {}
    if not df.empty:
        covered = {pot: d for pot, d in df.groupby("pot")["date"].max().items()}
    if cfg.raw_dir.exists():
        for path in cfg.raw_dir.glob("*.csv"):
            match = re.search(cfg.account_regex, path.stem)
            pot = _pot_for_account(cfg, match.group("account")) if match else None
            if pot:
                covered[pot] = max(covered.get(pot, date.min), file_coverage(path))
    return covered


def data_through(cfg: Config, df: pd.DataFrame) -> date | None:
    """The last day covered for every pot (the stalest pot decides), or None with no data."""
    covered = coverage_by_pot(cfg, df)
    return min(covered.values()) if covered else None
