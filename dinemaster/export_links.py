"""Build per-account statement links for the dining site from any URL of a logged-in session.

The site's session lives in query parameters (cfg.export.session_params), so links can't be
saved ahead of time. Given one pasted URL, we reuse its host and session parameters and fill
in the account number and a date range running from that account's last known transaction
through today. Nothing from the pasted URL is stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import pandas as pd

from .config import Config


@dataclass(frozen=True)
class ExportLink:
    name: str
    start: date
    end: date
    url: str


def latest_by_account(df: pd.DataFrame) -> dict[str, date]:
    """Date of the most recent transaction per account in the ledger frame."""
    if df.empty:
        return {}
    return {acct: ts.date() for acct, ts in df.groupby("account")["timestamp"].max().items()}


def build_links(pasted_url: str, cfg: Config, latest: dict[str, date], today: date) -> list[ExportLink]:
    """One statement link per configured export account.

    Each range starts on the account's latest known transaction date (overlap is deduped on
    ingest) or cfg.data_cutoff if the account has no history, and ends today.
    Raises ValueError if the pasted URL lacks a host or any required session parameter.
    """
    ex = cfg.export
    parts = urlsplit(pasted_url.strip())
    if not parts.scheme or not parts.netloc:
        raise ValueError("That doesn't look like a URL from the dining site.")
    query = parse_qs(parts.query)
    missing = [p for p in ex.session_params if not query.get(p)]
    if missing:
        raise ValueError(f"That URL is missing {', '.join(missing)} — copy one from a page you reach after logging in.")
    session = [(p, query[p][0]) for p in ex.session_params]

    links = []
    for account in ex.accounts:
        start = latest.get(account.name, cfg.data_cutoff)
        params = session + [
            (ex.start_param, start.isoformat()),
            (ex.end_param, today.isoformat()),
            (ex.account_param, account.acct),
        ]
        url = urlunsplit((parts.scheme, parts.netloc, ex.statement_path, urlencode(params), ""))
        links.append(ExportLink(account.name, start, today, url))
    return links
