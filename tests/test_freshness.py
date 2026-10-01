import os
from dataclasses import replace
from datetime import date, datetime

import pandas as pd

from dinemaster.config import load_config
from dinemaster.freshness import data_through, file_coverage


def touch(path, when: datetime):
    path.write_text("Date,Description,Amount,Balance\n")
    os.utime(path, (when.timestamp(), when.timestamp()))


def cfg_for(tmp_path):
    return replace(load_config(), raw_dir=tmp_path)


def frame(rows):
    df = pd.DataFrame(rows, columns=["pot", "date"])
    return df


def test_coverage_is_range_end_when_downloaded_later(tmp_path):
    f = tmp_path / "Block 160 Meals_statement_2026-09-01_to_2026-09-30.csv"
    touch(f, datetime(2026, 10, 1, 0, 45))
    assert file_coverage(f) == date(2026, 9, 30)


def test_coverage_is_download_day_when_range_runs_past_it(tmp_path):
    f = tmp_path / "Block 160 Meals_statement_2026-03-01_to_2026-09-31.csv"  # invalid day, future end
    touch(f, datetime(2026, 9, 23, 15, 18))
    assert file_coverage(f) == date(2026, 9, 23)


def test_unparseable_name_falls_back_to_download_day(tmp_path):
    f = tmp_path / "Block 160 Meals_statement.csv"
    touch(f, datetime(2026, 9, 23, 15, 18))
    assert file_coverage(f) == date(2026, 9, 23)


def test_data_through_is_the_stalest_pot(tmp_path):
    touch(tmp_path / "Block 160 Meals_statement_2026-09-01_to_2026-09-30.csv", datetime(2026, 10, 1, 0, 45))
    touch(tmp_path / "Dining Dollars_statement_2026-03-01_to_2026-09-31.csv", datetime(2026, 9, 23, 15, 19))
    touch(tmp_path / "Promotional Dining Dollars_statement_2026-03-01_to_2026-09-31.csv", datetime(2026, 9, 20, 9, 0))
    df = frame([("ME", date(2026, 9, 29)), ("DD", date(2026, 9, 21))])
    # ME covered through Sep 30; DD's freshest account export is Sep 23
    assert data_through(cfg_for(tmp_path), df) == date(2026, 9, 23)


def test_without_raw_files_falls_back_to_last_transaction(tmp_path):
    df = frame([("ME", date(2026, 9, 29)), ("DD", date(2026, 9, 21))])
    assert data_through(cfg_for(tmp_path), df) == date(2026, 9, 21)
    assert data_through(cfg_for(tmp_path), frame([])) is None
