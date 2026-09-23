"""Tests for dinemaster.ingest. All data is synthetic, built in tmp_path — never copy raw-data/ rows."""

from __future__ import annotations

import csv
import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from dinemaster.config import load_config
from dinemaster.ingest import OUTPUT_COLUMNS, load_transactions

HEADER = ["Date", "Description", "Amount", "Balance"]


def base_cfg(tmp_path: Path):
    cfg = load_config()
    return dataclasses.replace(
        cfg,
        raw_dir=tmp_path / "raw-data",
        ledger_path=tmp_path / "data" / "ledger.csv",
    )


def write_csv(dir_path: Path, filename: str, rows: list[tuple[str, str, float, float]]) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / filename
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow(row)
    return path


def me_filename(suffix: str = "a") -> str:
    return f"Block 160 Meals_statement_2026-01-01_to_2026-02-{suffix}.csv"


# ---------------------------------------------------------------------------
# Basic ingest
# ---------------------------------------------------------------------------


def test_basic_me_file_ingested(tmp_path):
    cfg = base_cfg(tmp_path)
    write_csv(
        cfg.raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv",
        [
            ("2026-01-02 12:00:00", "GH The Den", -1, 159),
            ("2026-01-01 12:00:00", "GH The Den", -1, 160),
        ],
    )
    df, report = load_transactions(cfg)

    assert list(df.columns) == OUTPUT_COLUMNS
    assert len(df) == 2
    # sorted by timestamp ascending
    assert df["timestamp"].is_monotonic_increasing
    assert set(df["account"]) == {"Block 160 Meals"}
    assert set(df["pot"]) == {"ME"}
    assert all(df["kind"] == "usage")
    assert all(df["dd_class"].isna())
    assert isinstance(df["date"].iloc[0], type(df["timestamp"].iloc[0].date()))
    assert report.total_rows == 2
    assert report.ledger_path == cfg.ledger_path
    assert len(report.files) == 1
    f = report.files[0]
    assert f["account"] == "Block 160 Meals"
    assert f["pot"] == "ME"
    assert f["rows"] == 2
    assert f["new"] == 2
    assert f["duplicate"] == 0
    assert f["warnings"] == []


# ---------------------------------------------------------------------------
# Merge behavior
# ---------------------------------------------------------------------------


def test_overlapping_files_no_double_count(tmp_path):
    cfg = base_cfg(tmp_path)
    write_csv(
        cfg.raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-01-15.csv",
        [
            ("2026-01-01 12:00:00", "GH The Den", -1, 160),
            ("2026-01-02 12:00:00", "GH The Den", -1, 159),
        ],
    )
    write_csv(
        cfg.raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv",
        [
            ("2026-01-01 12:00:00", "GH The Den", -1, 160),
            ("2026-01-02 12:00:00", "GH The Den", -1, 159),
            ("2026-01-03 12:00:00", "GH The Den", -1, 158),
        ],
    )
    df, report = load_transactions(cfg)

    assert len(df) == 3  # union, not 5
    assert report.total_rows == 3
    by_file = {f["file"]: f for f in report.files}
    first_file = "Block 160 Meals_statement_2026-01-01_to_2026-01-15.csv"
    second_file = "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv"
    assert by_file[first_file]["new"] == 2
    assert by_file[first_file]["duplicate"] == 0
    assert by_file[second_file]["new"] == 1
    assert by_file[second_file]["duplicate"] == 2


def test_identical_rows_within_one_file_kept(tmp_path):
    cfg = base_cfg(tmp_path)
    write_csv(
        cfg.raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv",
        [
            ("2026-01-01 12:00:00", "GH The Den", -1, 160),
            ("2026-01-01 12:00:00", "GH The Den", -1, 160),  # genuine same-minute duplicate
        ],
    )
    df, report = load_transactions(cfg)

    assert len(df) == 2
    f = report.files[0]
    assert f["rows"] == 2
    assert f["new"] == 2
    assert f["duplicate"] == 0


def test_ledger_survives_raw_file_deletion(tmp_path):
    cfg = base_cfg(tmp_path)
    path = write_csv(
        cfg.raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv",
        [("2026-01-01 12:00:00", "GH The Den", -1, 160)],
    )
    df1, report1 = load_transactions(cfg)
    assert len(df1) == 1

    path.unlink()
    df2, report2 = load_transactions(cfg)
    assert len(df2) == 1
    assert df2.iloc[0]["description"] == "GH The Den"
    assert report2.files == []  # no raw files to process this run


def test_second_run_adds_zero_rows(tmp_path):
    cfg = base_cfg(tmp_path)
    write_csv(
        cfg.raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv",
        [
            ("2026-01-01 12:00:00", "GH The Den", -1, 160),
            ("2026-01-02 12:00:00", "GH The Den", -1, 159),
        ],
    )
    load_transactions(cfg)
    df2, report2 = load_transactions(cfg)

    assert len(df2) == 2
    assert sum(f["new"] for f in report2.files) == 0
    assert sum(f["duplicate"] for f in report2.files) == 2


def test_ledger_written_only_when_rows_added(tmp_path):
    cfg = base_cfg(tmp_path)
    write_csv(
        cfg.raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv",
        [("2026-01-01 12:00:00", "GH The Den", -1, 160)],
    )
    load_transactions(cfg)
    assert cfg.ledger_path.exists()
    mtime_after_first = cfg.ledger_path.stat().st_mtime_ns

    load_transactions(cfg)
    mtime_after_second = cfg.ledger_path.stat().st_mtime_ns
    assert mtime_after_first == mtime_after_second


# ---------------------------------------------------------------------------
# Split-tender DD purchases & meal/snack classification
# ---------------------------------------------------------------------------


def test_split_tender_purchase_classed_once_by_summed_amount(tmp_path):
    cfg = base_cfg(tmp_path)
    # Two DD accounts, same timestamp+description, individually below threshold (8.0)
    # but summing to a meal.
    write_csv(
        cfg.raw_dir,
        "Dining Dollars_statement_2026-01-01_to_2026-01-31.csv",
        [("2026-01-05 12:00:00", "GH Newcomb Dining Room", -4.00, 100)],
    )
    write_csv(
        cfg.raw_dir,
        "Promotional Dining Dollars_statement_2026-01-01_to_2026-01-31.csv",
        [("2026-01-05 12:00:00", "GH Newcomb Dining Room", -5.00, 50)],
    )
    df, report = load_transactions(cfg)

    dd_rows = df[df["pot"] == "DD"]
    assert len(dd_rows) == 2
    assert all(dd_rows["dd_class"] == "meal")
    assert not report.chain_breaks


def test_snack_pattern_overrides_amount(tmp_path):
    cfg = base_cfg(tmp_path)
    # Description matches a snack pattern (Gastons) even though the amount alone
    # would clear the meal threshold.
    write_csv(
        cfg.raw_dir,
        "Dining Dollars_statement_2026-01-01_to_2026-01-31.csv",
        [("2026-01-05 12:00:00", "GH Gastons Market Just Walk Out", -12.00, 100)],
    )
    df, _ = load_transactions(cfg)
    row = df.iloc[0]
    assert row["dd_class"] == "snack"


def test_meal_threshold_boundary(tmp_path):
    cfg = base_cfg(tmp_path)
    write_csv(
        cfg.raw_dir,
        "Dining Dollars_statement_2026-01-01_to_2026-01-31.csv",
        [
            ("2026-01-05 12:00:00", "GH The Den", -8.00, 100),  # exactly threshold -> meal
            ("2026-01-06 12:00:00", "GH The Den", -7.99, 92),  # just under -> snack
        ],
    )
    df, _ = load_transactions(cfg)
    df = df.sort_values("timestamp")
    assert df.iloc[0]["dd_class"] == "meal"
    assert df.iloc[1]["dd_class"] == "snack"


# ---------------------------------------------------------------------------
# Kind classification
# ---------------------------------------------------------------------------


def test_reversal_load_and_adjustment_kinds(tmp_path):
    cfg = base_cfg(tmp_path)
    write_csv(
        cfg.raw_dir,
        "Dining Dollars_statement_2026-01-01_to_2026-01-31.csv",
        [
            ("2026-01-01 09:00:00", "Deposit", 100.00, 100),
            ("2026-01-02 09:00:00", "GH The Den", -10.00, 90),
            ("2026-01-03 09:00:00", "GH The Den", 10.00, 100),  # refund
            ("2026-01-04 09:00:00", "revoke promo flex", -20.00, 80),
        ],
    )
    df, _ = load_transactions(cfg)
    df = df.sort_values("timestamp").reset_index(drop=True)
    assert df.iloc[0]["kind"] == "load"
    assert df.iloc[1]["kind"] == "usage"
    assert df.iloc[2]["kind"] == "reversal"
    assert df.iloc[3]["kind"] == "adjustment"
    # dd_class only set on usage rows
    assert df.iloc[0]["dd_class"] is None
    assert df.iloc[2]["dd_class"] is None
    assert df.iloc[3]["dd_class"] is None
    assert df.iloc[1]["dd_class"] in {"meal", "snack"}


# ---------------------------------------------------------------------------
# Bad files
# ---------------------------------------------------------------------------


def test_bad_schema_file_skipped_with_warning(tmp_path):
    cfg = base_cfg(tmp_path)
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    bad_path = cfg.raw_dir / "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv"
    with open(bad_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Desc", "Amt", "Bal"])  # wrong headers
        writer.writerow(["2026-01-01 12:00:00", "GH The Den", -1, 160])

    df, report = load_transactions(cfg)

    assert df.empty
    assert report.total_rows == 0
    assert len(report.files) == 1
    assert report.files[0]["rows"] == 0
    assert report.files[0]["warnings"]
    assert report.warnings


def test_unknown_account_skipped(tmp_path):
    cfg = base_cfg(tmp_path)
    write_csv(
        cfg.raw_dir,
        "Random Account_statement_2026-01-01_to_2026-01-31.csv",
        [("2026-01-01 12:00:00", "Something", -1, 100)],
    )
    df, report = load_transactions(cfg)

    assert df.empty
    assert len(report.files) == 1
    assert report.files[0]["pot"] is None
    assert report.files[0]["rows"] == 0
    assert any("unknown account" in w.lower() for w in report.warnings)


def test_unparsable_row_skipped_valid_rows_kept(tmp_path):
    cfg = base_cfg(tmp_path)
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.raw_dir / "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerow(["2026-01-01 12:00:00", "GH The Den", -1, 160])
        writer.writerow(["not-a-date", "GH The Den", -1, 159])

    df, report = load_transactions(cfg)

    assert len(df) == 1
    f = report.files[0]
    assert f["rows"] == 1
    assert f["new"] == 1
    assert f["warnings"]


# ---------------------------------------------------------------------------
# Chain breaks
# ---------------------------------------------------------------------------


def test_chain_break_detected(tmp_path):
    cfg = base_cfg(tmp_path)
    write_csv(
        cfg.raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv",
        [
            ("2026-01-01 12:00:00", "GH The Den", -1, 160),  # first row, no predecessor check
            ("2026-01-02 12:00:00", "GH The Den", -1, 155),  # should be 159, breaks
        ],
    )
    df, report = load_transactions(cfg)

    assert len(report.chain_breaks) == 1
    brk = report.chain_breaks[0]
    assert brk["account"] == "Block 160 Meals"
    assert brk["expected"] == pytest.approx(159)
    assert brk["actual"] == pytest.approx(155)


def test_same_timestamp_tie_ordering_does_not_false_flag(tmp_path):
    cfg = base_cfg(tmp_path)
    # Two rows share a timestamp; written in an order that would look broken
    # if checked naively, but a valid ordering exists (160 -1=159 -2=157).
    write_csv(
        cfg.raw_dir,
        "Block 160 Meals_statement_2026-01-01_to_2026-01-31.csv",
        [
            ("2026-01-01 12:00:00", "GH The Den", -1, 160),  # first row, sets balance at 160
            ("2026-01-02 12:00:00", "GH Newcomb", -2, 157),  # written second in file
            ("2026-01-02 12:00:00", "GH The Den", -1, 159),  # written first, but chrono-order needs this first
        ],
    )
    df, report = load_transactions(cfg)

    assert report.chain_breaks == []


# ---------------------------------------------------------------------------
# Empty / missing raw dir
# ---------------------------------------------------------------------------


def test_missing_raw_dir_returns_empty_frame(tmp_path):
    cfg = base_cfg(tmp_path)
    assert not cfg.raw_dir.exists()

    df, report = load_transactions(cfg)

    assert df.empty
    assert list(df.columns) == OUTPUT_COLUMNS
    assert report.total_rows == 0
    assert report.files == []
    assert report.chain_breaks == []
    assert not cfg.ledger_path.exists()


def test_empty_raw_dir_returns_empty_frame(tmp_path):
    cfg = base_cfg(tmp_path)
    cfg.raw_dir.mkdir(parents=True)

    df, report = load_transactions(cfg)

    assert df.empty
    assert list(df.columns) == OUTPUT_COLUMNS
    assert report.total_rows == 0
