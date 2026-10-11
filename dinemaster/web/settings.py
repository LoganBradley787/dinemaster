"""Saved settings: a small JSON file layered over config.toml.

The file lives next to the ledger (`data/settings.json`, gitignored). It holds only what was
changed on the Settings form; anything missing falls back to config.toml. `reset` deletes it.

    saved = settings.load(settings.path_for(cfg))
    cfg = settings.apply(cfg, saved)              # plan and semester fields
    cfg = settings.apply_away(cfg, saved)         # after the calendar is merged in

The as-of date is not a saved setting: it travels in the URL (`?as_of=`), see `context.ViewParams`.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from ..config import AwayPeriod, Config

#: Config fields the Settings form may change, with the type each is stored as.
FIELDS: dict[str, type] = {
    "semester_start": date,
    "semester_end": date,
    "data_cutoff": date,
    "starting_me": float,
    "starting_dd": float,
    "meal_price": float,
    "exclude_away": bool,
    "meal_threshold": float,
    "rounding_mode": str,
    "rounding_granularity": float,
    "dd_rollover": bool,
    "dd_leftover_flag": float,
}
ROUNDING_MODES = ("floor", "round", "ceil")
#: Fields that must be greater than zero (they are divided by).
POSITIVE = ("meal_price", "rounding_granularity")


def path_for(cfg: Config) -> Path:
    return cfg.ledger_path.parent / "settings.json"


def load(path: Path) -> dict:
    """The saved values, or {} when there is no file or it cannot be read."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path: Path, values: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(values), indent=2, sort_keys=True) + "\n")


def reset(path: Path) -> None:
    """Back to config.toml: delete the saved file."""
    path.unlink(missing_ok=True)


def _coerce(kind: type, raw):
    """`raw` (a JSON value or a form string) as `kind`; raises ValueError when it does not fit."""
    if kind is date:
        return raw if isinstance(raw, date) else date.fromisoformat(str(raw))
    if kind is bool:
        return raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "on", "yes")
    if kind is float:
        return float(raw)
    return str(raw)


def clean(values: Mapping) -> tuple[dict, list[str]]:
    """Check `values` (from a form or the file). Returns (JSON-ready values, problems in plain words).

    Unknown keys are dropped. A value that does not parse is dropped and reported.
    """
    out: dict = {}
    problems: list[str] = []
    for name, kind in FIELDS.items():
        if name not in values or values[name] in ("", None):
            continue
        try:
            value = _coerce(kind, values[name])
        except (ValueError, TypeError):
            problems.append(f"{name.replace('_', ' ')} is not a valid {'date' if kind is date else 'number'}.")
            continue
        if name in POSITIVE and value <= 0:
            problems.append(f"{name.replace('_', ' ')} must be more than zero.")
            continue
        if name == "rounding_mode" and value not in ROUNDING_MODES:
            problems.append("rounding mode must be floor, round or ceil.")
            continue
        out[name] = value.isoformat() if isinstance(value, date) else value
    away = values.get("away")
    if isinstance(away, Mapping):
        kept = {}
        for key, entry in away.items():
            if not isinstance(entry, Mapping):
                continue
            try:
                kept[str(key)] = {
                    "enabled": _coerce(bool, entry.get("enabled", True)),
                    "start": _coerce(date, entry["start"]).isoformat(),
                    "end": _coerce(date, entry["end"]).isoformat(),
                }
            except (KeyError, ValueError, TypeError):
                problems.append(f"away period {key} has a date that is not valid.")
        if kept:
            out["away"] = kept
    return out, problems


def apply(cfg: Config, saved: Mapping) -> Config:
    """`cfg` with the saved plan and semester fields. Bad values are ignored."""
    values, _ = clean(saved)
    changed = {name: _coerce(kind, values[name]) for name, kind in FIELDS.items() if name in values}
    return dataclasses.replace(cfg, **changed) if changed else cfg


def away_key(index: int, period: AwayPeriod) -> str:
    """The key an away period is saved under.

    Calendar periods come and go with the .ics files, so they are keyed by their dates; configured
    ones by their position in config.toml.
    """
    return f"cal_{period.start}_{period.end}" if period.source == "calendar" else str(index)


def apply_away(cfg: Config, saved: Mapping) -> Config:
    """`cfg` with each away period's saved switch and dates. Call after the calendar is merged in."""
    values, _ = clean({"away": saved.get("away")})
    away = values.get("away", {})
    if not away:
        return cfg
    periods = []
    for i, period in enumerate(cfg.away_periods):
        entry = away.get(away_key(i, period))
        if entry:
            period = dataclasses.replace(
                period, enabled=entry["enabled"], start=date.fromisoformat(entry["start"]), end=date.fromisoformat(entry["end"])
            )
        periods.append(period)
    return dataclasses.replace(cfg, away_periods=tuple(periods))
