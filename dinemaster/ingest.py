"""Parse raw-data exports, merge into the ledger, classify, and validate balance chains.

Public API: ``load_transactions(cfg) -> (DataFrame, IngestReport)``.
"""

from __future__ import annotations

import itertools
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from dinemaster.config import Config

LEDGER_COLUMNS = [
    "account",
    "pot",
    "timestamp",
    "description",
    "amount",
    "balance",
    "source_file",
    "first_seen",
]

OUTPUT_COLUMNS = [
    "account",
    "pot",
    "timestamp",
    "date",
    "description",
    "amount",
    "balance",
    "kind",
    "dd_class",
]

CHAIN_TOL = 0.005
_MAX_TIE_PERMUTE = 8


@dataclass
class IngestReport:
    files: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    chain_breaks: list[dict] = field(default_factory=list)
    ledger_path: Path | None = None
    total_rows: int = 0


def _round2(x: float) -> float:
    return round(float(x) + (1e-9 if x >= 0 else -1e-9), 2)


def _key(row: dict) -> tuple:
    return (row["account"], row["timestamp"], row["description"], _round2(row["amount"]))


def _pot_for_account(cfg: Config, account: str) -> str | None:
    for rule in cfg.pots:
        if re.search(rule.pattern, account):
            return rule.pot
    return None


def _load_ledger(ledger_path: Path) -> list[dict]:
    if not ledger_path.exists():
        return []
    df = pd.read_csv(ledger_path, dtype={"account": str, "pot": str, "description": str, "source_file": str, "first_seen": str})
    if df.empty:
        return []
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    rows = []
    for r in df.to_dict("records"):
        r["timestamp"] = r["timestamp"].to_pydatetime()
        r["amount"] = _round2(r["amount"])
        r["balance"] = _round2(r["balance"])
        rows.append(r)
    return rows


def _parse_file(path: Path, cfg: Config, account: str, pot: str) -> tuple[list[dict], list[str]]:
    """Parse one raw CSV. Returns (valid_rows, warnings). Never raises."""
    warnings: list[str] = []
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"{path.name}: could not read file ({exc})")
        return [], warnings

    col_map = cfg.columns
    required = [col_map["timestamp"], col_map["description"], col_map["amount"], col_map["balance"]]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        warnings.append(f"{path.name}: missing required column(s) {missing}; file skipped")
        return [], warnings

    rows: list[dict] = []
    for i, r in enumerate(raw.to_dict("records")):
        try:
            ts = datetime.strptime(r[col_map["timestamp"]].strip(), "%Y-%m-%d %H:%M:%S")
            desc = r[col_map["description"]].strip()
            amount = _round2(float(r[col_map["amount"]]))
            balance = _round2(float(r[col_map["balance"]]))
        except Exception as exc:
            warnings.append(f"{path.name}: skipped unparsable row {i} ({exc})")
            continue
        rows.append(
            {
                "account": account,
                "pot": pot,
                "timestamp": ts,
                "description": desc,
                "amount": amount,
                "balance": balance,
                "source_file": path.name,
            }
        )
    return rows, warnings


def _merge_file_into_ledger(
    ledger_rows: list[dict],
    ledger_counts: Counter,
    file_rows: list[dict],
    today: date,
) -> tuple[int, int]:
    """Max-count merge. Mutates ledger_rows/ledger_counts in place. Returns (new, duplicate)."""
    file_counts: Counter = Counter()
    rows_by_key: dict[tuple, list[dict]] = {}
    for r in file_rows:
        k = _key(r)
        file_counts[k] += 1
        rows_by_key.setdefault(k, []).append(r)

    new = 0
    duplicate = 0
    for k, file_count in file_counts.items():
        existing = ledger_counts.get(k, 0)
        add = max(0, file_count - existing)
        duplicate += file_count - add
        new += add
        for r in rows_by_key[k][:add]:
            new_row = dict(r)
            new_row["first_seen"] = today.isoformat()
            ledger_rows.append(new_row)
        if add:
            ledger_counts[k] = existing + add
    return new, duplicate


def _classify_kind(desc: str, amount: float, cfg: Config) -> str:
    for pat in cfg.load_patterns:
        if re.search(pat, desc):
            return "load"
    for pat in cfg.adjustment_patterns:
        if re.search(pat, desc):
            return "adjustment"
    if amount > 0:
        return "reversal"
    return "usage"


def _is_snack_pattern(desc: str, cfg: Config) -> bool:
    return any(re.search(pat, desc) for pat in cfg.snack_patterns)


def _assign_dd_class(df: pd.DataFrame, cfg: Config) -> pd.Series:
    dd_class = pd.Series([None] * len(df), index=df.index, dtype=object)
    mask = (df["pot"] == "DD") & (df["kind"] == "usage")
    if not mask.any():
        return dd_class
    usage = df[mask]
    for (_ts, _desc), grp in usage.groupby(["timestamp", "description"], sort=False):
        total = sum(grp["amount"])
        desc = grp["description"].iloc[0]
        if _is_snack_pattern(desc, cfg):
            cls = "snack"
        else:
            cls = "meal" if abs(total) >= cfg.meal_threshold else "snack"
        dd_class.loc[grp.index] = cls
    return dd_class


def _resolve_tie_order(prev_balance: float | None, group_rows: list[tuple]) -> list[tuple]:
    """group_rows: list of (idx, amount, balance). Returns best-ordered list minimizing chain breaks."""
    if len(group_rows) == 1 or len(group_rows) > _MAX_TIE_PERMUTE:
        return group_rows
    best = None
    best_breaks = None
    for perm in itertools.permutations(group_rows):
        breaks = 0
        pb = prev_balance
        for _idx, amount, balance in perm:
            if pb is not None and abs(pb + amount - balance) > CHAIN_TOL:
                breaks += 1
            pb = balance
        if best_breaks is None or breaks < best_breaks:
            best_breaks = breaks
            best = perm
            if breaks == 0:
                break
    return list(best)


def _chain_breaks_for_account(account: str, df_acc: pd.DataFrame) -> list[dict]:
    df_acc = df_acc.sort_values("timestamp", kind="stable")
    groups: list[list[tuple]] = []
    for _ts, grp in df_acc.groupby("timestamp", sort=True):
        groups.append([(idx, row["amount"], row["balance"]) for idx, row in grp.iterrows()])

    ordered: list[tuple] = []
    prev_balance: float | None = None
    for grp in groups:
        best = _resolve_tie_order(prev_balance, grp)
        ordered.extend(best)
        prev_balance = best[-1][2]

    breaks = []
    prev_row = None
    for idx, amount, balance in ordered:
        if prev_row is not None:
            expected = prev_row[1] + amount
            if abs(expected - balance) > CHAIN_TOL:
                row = df_acc.loc[idx]
                breaks.append(
                    {
                        "account": account,
                        "timestamp": row["timestamp"],
                        "description": row["description"],
                        "expected": expected,
                        "actual": balance,
                    }
                )
        prev_row = (idx, balance)
    return breaks


def _empty_output_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "account": pd.Series(dtype="object"),
            "pot": pd.Series(dtype="object"),
            "timestamp": pd.Series(dtype="datetime64[ns]"),
            "date": pd.Series(dtype="object"),
            "description": pd.Series(dtype="object"),
            "amount": pd.Series(dtype="float64"),
            "balance": pd.Series(dtype="float64"),
            "kind": pd.Series(dtype="object"),
            "dd_class": pd.Series(dtype="object"),
        }
    )


def load_transactions(cfg: Config) -> tuple[pd.DataFrame, IngestReport]:
    report = IngestReport(ledger_path=cfg.ledger_path)

    ledger_rows = _load_ledger(cfg.ledger_path)
    ledger_counts: Counter = Counter(_key(r) for r in ledger_rows)
    rows_added_total = 0
    today = date.today()

    raw_dir = cfg.raw_dir
    if raw_dir.exists():
        files = sorted(raw_dir.glob("*.csv"), key=lambda p: p.name)
    else:
        files = []

    for path in files:
        stem = path.stem
        match = re.match(cfg.account_regex, stem)
        if not match:
            msg = f"{path.name}: could not determine account from filename"
            report.warnings.append(msg)
            report.files.append(
                {
                    "file": path.name,
                    "account": None,
                    "pot": None,
                    "rows": 0,
                    "new": 0,
                    "duplicate": 0,
                    "warnings": [msg],
                }
            )
            continue

        account = match.group("account")
        pot = _pot_for_account(cfg, account)
        if pot is None:
            msg = f"{path.name}: unknown account '{account}' (no pot rule matched); file skipped"
            report.warnings.append(msg)
            report.files.append(
                {
                    "file": path.name,
                    "account": account,
                    "pot": None,
                    "rows": 0,
                    "new": 0,
                    "duplicate": 0,
                    "warnings": [msg],
                }
            )
            continue

        file_rows, parse_warnings = _parse_file(path, cfg, account, pot)
        report.warnings.extend(parse_warnings)

        new, duplicate = _merge_file_into_ledger(ledger_rows, ledger_counts, file_rows, today)
        rows_added_total += new

        report.files.append(
            {
                "file": path.name,
                "account": account,
                "pot": pot,
                "rows": len(file_rows),
                "new": new,
                "duplicate": duplicate,
                "warnings": list(parse_warnings),
            }
        )

    if rows_added_total > 0:
        cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        out_df = pd.DataFrame(ledger_rows, columns=LEDGER_COLUMNS)
        out_df = out_df.sort_values("timestamp", kind="stable")
        out_df.to_csv(cfg.ledger_path, index=False)

    if not ledger_rows:
        report.total_rows = 0
        return _empty_output_frame(), report

    df = pd.DataFrame(ledger_rows, columns=LEDGER_COLUMNS)
    df["kind"] = [
        _classify_kind(desc, amount, cfg) for desc, amount in zip(df["description"], df["amount"])
    ]
    df["dd_class"] = _assign_dd_class(df, cfg)
    df["date"] = df["timestamp"].apply(lambda ts: ts.date())

    chain_breaks: list[dict] = []
    for account, df_acc in df.groupby("account", sort=False):
        chain_breaks.extend(_chain_breaks_for_account(account, df_acc))
    report.chain_breaks = chain_breaks

    df = df[OUTPUT_COLUMNS].sort_values("timestamp", kind="stable").reset_index(drop=True)
    report.total_rows = len(df)
    return df, report
