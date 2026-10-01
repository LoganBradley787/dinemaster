from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from dinemaster.config import ExportAccount, ExportConfig, load_config
from dinemaster.export_links import build_links, latest_by_account

PASTED = "https://dining.example.edu/statementdetail.php?cid=9&skey=abc123&startdate=2026-01-01&enddate=2026-01-31&acct=99"


@pytest.fixture
def cfg():
    export = ExportConfig(accounts=(ExportAccount("Meals", "22"), ExportAccount("Dollars", "4")))
    return replace(load_config(), export=export, data_cutoff=date(2026, 8, 1))


def frame(rows):
    df = pd.DataFrame(rows, columns=["account", "timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def test_links_reuse_session_and_start_at_last_transaction(cfg):
    df = frame([("Meals", "2026-09-20 12:00"), ("Meals", "2026-09-23 13:01"), ("Dollars", "2026-09-21 23:19")])
    links = build_links(PASTED, cfg, latest_by_account(df), today=date(2026, 10, 1))
    assert [l.name for l in links] == ["Meals", "Dollars"]
    assert links[0].url == (
        "https://dining.example.edu/statementdetail.php?cid=9&skey=abc123"
        "&startdate=2026-09-23&enddate=2026-10-01&acct=22"
    )
    assert links[1].start == date(2026, 9, 21)
    assert "acct=4" in links[1].url


def test_account_with_no_history_starts_at_cutoff(cfg):
    links = build_links(PASTED, cfg, {}, today=date(2026, 10, 1))
    assert all(l.start == date(2026, 8, 1) for l in links)


def test_url_from_another_page_of_the_site_still_works(cfg):
    links = build_links("https://dining.example.edu/home.php?skey=zzz&cid=9", cfg, {}, today=date(2026, 10, 1))
    assert links[0].url.startswith("https://dining.example.edu/statementdetail.php?cid=9&skey=zzz&")


@pytest.mark.parametrize("bad", ["", "not a url", "https://dining.example.edu/statementdetail.php?cid=9"])
def test_missing_session_params_raise(cfg, bad):
    with pytest.raises(ValueError):
        build_links(bad, cfg, {}, today=date(2026, 10, 1))
