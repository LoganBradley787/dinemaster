from dataclasses import replace
from datetime import date, datetime

import pandas as pd

from dinemaster import metrics as m
from dinemaster.config import load_config
from dinemaster.tiles import build_groups, render_html

COLS = ["account", "pot", "timestamp", "date", "description", "amount", "balance", "kind", "dd_class"]


def frame(rows):
    out = []
    for pot, ts, desc, amount, kind, dd_class in rows:
        t = datetime.fromisoformat(ts)
        out.append((pot, pot, t, t.date(), desc, amount, 0.0, kind, dd_class))
    df = pd.DataFrame(out, columns=COLS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def texts(cfg, rows):
    df = frame(rows)
    groups = build_groups(m.compute_metrics(df, cfg), cfg, df, m.daily(df, cfg), cfg.as_of)
    tiles = [t for g in groups for t in g.tiles]
    return [f"{t.pre}|{t.value}|{t.post}" for t in tiles], groups


def cfg_for(**kw):
    return replace(load_config(), as_of=date(2026, 9, 23), starting_me=160, starting_dd=360.0, **kw)


def swipes(n, per_day_from="2026-08-21"):
    start = date.fromisoformat(per_day_from)
    return [("ME", f"{start + pd.Timedelta(days=i % 30)} 12:00", "Hall A", -1.0, "usage", None) for i in range(n)]


def test_on_track_and_dd_holds():
    lines, groups = texts(cfg_for(), swipes(34) + [("DD", "2026-09-01 20:00", "Cafe", -20.0, "usage", "meal")])
    joined = "\n".join(lines)
    assert "On track to have|" in joined and "extra meal exchanges" in joined
    assert "Dining dollars will hold with|" in joined and "days to spare" in joined
    assert "goes unspent" in joined  # rollover off and a big projected leftover
    assert [g.title for g in groups] == ["right now", "projections", "pace", "habits"]


def test_running_out_of_both():
    rows = swipes(120) + [("DD", "2026-09-01 20:00", "Cafe", -300.0, "usage", "meal")]
    joined = "\n".join(texts(cfg_for(), rows)[0])
    assert "Meal exchanges run out|" in joined and "days before the semester ends" in joined
    assert "Projected to run out of dining dollars by|" in joined and "days short" in joined


def test_rollover_changes_leftover_wording():
    rows = swipes(34) + [("DD", "2026-09-01 20:00", "Cafe", -20.0, "usage", "meal")]
    assert "carries into spring" in "\n".join(texts(cfg_for(dd_rollover=True), rows)[0])


def test_no_usage_yet_does_not_crash():
    lines, _ = texts(cfg_for(), [("ME", "2026-08-20 09:00", "Deposit", 160.0, "load", None)])
    assert any("nothing to project from" in line for line in lines)


def test_render_escapes_text():
    _, groups = texts(cfg_for(), [("ME", "2026-09-01 12:00", "<b>Hall</b>", -1.0, "usage", None)])
    html = render_html(groups, "Dining", "as of today")
    assert "<b>Hall</b>" not in html and "&lt;b&gt;Hall&lt;/b&gt;" in html
