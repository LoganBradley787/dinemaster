"""DineMaster Streamlit app.

Recomputes everything from raw-data/ on every rerun (ingest is not cached —
the data is tiny). Set the environment variable DINEMASTER_CONFIG to a path
to an alternate config.toml (used by tests); raw_dir/ledger_path in that file
are resolved relative to its own parent directory.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from dinemaster import charts as c
from dinemaster import metrics as m
from dinemaster.config import ROOT, Config, load_config
from dinemaster.export_links import build_links, latest_by_account
from dinemaster.freshness import data_through
from dinemaster.ingest import load_transactions
from dinemaster.tiles import ago, build_page_html

st.set_page_config(page_title="DineMaster", layout="wide")

_CONFIG_PATH = Path(os.environ.get("DINEMASTER_CONFIG", ROOT / "config.toml"))
_CONFIG_ROOT = _CONFIG_PATH.resolve().parent


def fmt(x: float | None, signed: bool = False, digits: int = 1, prefix: str = "") -> str:
    """Format a possibly-None metric value for tables/captions."""
    if x is None:
        return "n/a"
    sign = "+" if signed else ""
    return f"{prefix}{x:{sign}.{digits}f}"


def esc(text: str) -> str:
    """Escape "$" so Streamlit markdown doesn't render dollar amounts as LaTeX."""
    return text.replace("$", "\\$")


def side_by_side(rows: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """Build a Plain/Away comparison table from {label: (plain, away)}."""
    return pd.DataFrame(rows, index=["Plain", "Away"]).T


def sidebar_config(base_cfg: Config) -> Config:
    st.sidebar.header("Session parameters")
    st.sidebar.caption("Overrides here last only for this session. For permanent changes, edit config.toml.")

    with st.sidebar.expander("Semester & as-of", expanded=True):
        semester_start = st.date_input("Semester start", base_cfg.semester_start)
        semester_end = st.date_input("Semester end", base_cfg.semester_end)
        data_cutoff = st.date_input("Data cutoff", base_cfg.data_cutoff)
        as_of = st.date_input("As of", base_cfg.as_of)

    with st.sidebar.expander("Starting balances & price"):
        starting_me = st.number_input("Starting ME", value=float(base_cfg.starting_me), step=1.0)
        starting_dd = st.number_input("Starting DD ($)", value=float(base_cfg.starting_dd), step=1.0)
        meal_price = st.number_input("Meal price ($)", value=float(base_cfg.meal_price), step=0.5, min_value=0.01)

    with st.sidebar.expander("DD rounding"):
        modes = ["floor", "round", "ceil"]
        rounding_mode = st.selectbox("Rounding mode", modes, index=modes.index(base_cfg.rounding_mode))
        rounding_granularity = st.number_input(
            "Granularity (meals)", value=float(base_cfg.rounding_granularity), step=0.5, min_value=0.01
        )

    with st.sidebar.expander("Usage rules"):
        meal_threshold = st.number_input("Meal threshold ($)", value=float(base_cfg.meal_threshold), step=1.0)
        dd_rollover = st.checkbox("DD rolls over to spring", value=base_cfg.dd_rollover)
        dd_leftover_flag = st.number_input(
            "DD leftover warning threshold ($)", value=float(base_cfg.dd_leftover_flag), step=1.0
        )
        exclude_away = st.checkbox("Exclude away days from remaining/pace", value=base_cfg.exclude_away)

    away_periods = []
    with st.sidebar.expander("Away periods"):
        if not base_cfg.away_periods:
            st.caption("None configured.")
        for i, period in enumerate(base_cfg.away_periods):
            st.markdown(f"**{period.name}**")
            enabled = st.checkbox("Enabled", value=period.enabled, key=f"away_enabled_{i}")
            start = st.date_input("Start", period.start, key=f"away_start_{i}")
            end = st.date_input("End", period.end, key=f"away_end_{i}")
            away_periods.append(dataclasses.replace(period, enabled=enabled, start=start, end=end))

    return dataclasses.replace(
        base_cfg,
        semester_start=semester_start,
        semester_end=semester_end,
        data_cutoff=data_cutoff,
        as_of=as_of,
        starting_me=starting_me,
        starting_dd=starting_dd,
        meal_price=meal_price,
        rounding_mode=rounding_mode,
        rounding_granularity=rounding_granularity,
        meal_threshold=meal_threshold,
        dd_rollover=dd_rollover,
        dd_leftover_flag=dd_leftover_flag,
        exclude_away=exclude_away,
        away_periods=tuple(away_periods),
    )


def update_data_panel(cfg, df: pd.DataFrame) -> None:
    """Data-freshness line plus a generator for per-account statement links on the dining site."""
    latest = latest_by_account(df)
    today = date.today()
    through = data_through(cfg, df)
    if through:
        label = f"Update data — exports cover through {through:%b} {through.day} ({ago((today - through).days)})"
    else:
        label = "Update data — no transactions loaded yet"
    with st.expander(label, expanded=False):
        if latest:
            st.caption("Last transaction — " + " · ".join(f"{name}: {d:%b} {d.day}" for name, d in sorted(latest.items())))
        if not cfg.export.accounts:
            st.caption(f"Drop new CSV exports into `{cfg.raw_dir}` and reload.")
            return
        pasted = st.text_input(
            "Log in to the dining site, copy the URL of any page, and paste it here",
            help="Only used to build the links below (it carries your login session); it is not saved anywhere.",
        )
        if pasted:
            try:
                links = build_links(pasted, cfg, latest, today)
            except ValueError as err:
                st.error(str(err))
            else:
                for col, link in zip(st.columns(len(links)), links):
                    col.link_button(link.name, link.url, help=f"{link.start:%b %d} → {link.end:%b %d}")
                st.caption(
                    f"Each link opens that account's statement from its last known transaction through today. "
                    f"Export each as CSV, drop the files into `{cfg.raw_dir}`, then reload this page. "
                    "If a link says you're logged out, the session expired — paste a fresh URL."
                )


def tiles_page(cfg: Config, df: pd.DataFrame) -> None:
    """Metro-style summary: the same metrics as the dashboard, as plain-sentence tiles."""
    if df.empty:
        st.info(f"No dining data found yet. Drop your statement CSV exports into `{cfg.raw_dir}` and reload.")
        return
    st.html(build_page_html(cfg, df, date.today()))


def main(cfg: Config, df: pd.DataFrame, report) -> None:
    dc = m.day_counts(cfg)
    metrics = m.compute_metrics(df, cfg)
    bal = metrics.balances
    variant = "away" if cfg.exclude_away else "plain"

    st.title("DineMaster")
    st.caption(f"As of {dc.a.isoformat()}")
    st.caption(
        "Day convention: start and end dates are inclusive. Elapsed days run "
        "S → as-of; remaining days run the day after as-of → semester end. "
        "When the away toggle is on, enabled away-period days are subtracted "
        "from remaining (and elapsed) day counts."
    )

    update_data_panel(cfg, df)

    # --- Alerts -----------------------------------------------------------
    raw_has_csvs = any(cfg.raw_dir.glob("*.csv")) if cfg.raw_dir.exists() else False
    if not raw_has_csvs:
        st.info(
            f"No dining data found yet. Drop your UVA statement CSV exports into `{cfg.raw_dir}` "
            "(one file per account) and use **Reload data** in the sidebar."
        )
    if dc.notice:
        st.warning(dc.notice)
    if bal.me_warning:
        st.warning(esc(f"ME reconciliation: {bal.me_warning}"))
    if bal.dd_warning:
        st.warning(esc(f"DD reconciliation: {bal.dd_warning}"))
    for w in report.warnings:
        st.warning(esc(f"Ingest: {w}"))
    if report.chain_breaks:
        st.warning(f"{len(report.chain_breaks)} balance chain break(s) detected — see the Data tab.")
    dd_leftover_note = metrics.burn[variant]["dd"].leftover_note
    if dd_leftover_note:
        st.info(esc(f"DD leftover: {dd_leftover_note}"))

    # --- KPI row ------------------------------------------------------------
    dd_me_equiv = cfg.round_meals(bal.dd_left / cfg.meal_price)
    strict_delta = metrics.targets[variant].strict_delta
    pooled_delta = metrics.targets[variant].pooled_delta
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("ME left", fmt(bal.me_left, digits=0))
    k2.metric("DD left", fmt(bal.dd_left, digits=2, prefix="$"), f"≈{dd_me_equiv:.0f} meals")
    k3.metric("Semester progress", f"{metrics.progress * 100:.0f}%")
    k4.metric("Strict pace (ahead/behind)", fmt(strict_delta, signed=True) + " meals" if strict_delta is not None else "n/a")
    k5.metric("Pooled pace (ahead/behind)", fmt(pooled_delta, signed=True) + " meals" if pooled_delta is not None else "n/a")

    daily = m.daily(df, cfg)

    tab_balances, tab_usage, tab_plan, tab_whatif, tab_data = st.tabs(
        ["Balances", "Usage", "Plan math", "What-if", "Data"]
    )

    # --- Balances tab ---------------------------------------------------
    with tab_balances:
        me_rate = metrics.burn[variant]["me"].rate
        me_ideal = m.ideal_series(cfg, cfg.starting_me, cfg.exclude_away)
        me_proj = m.projection_series(cfg, dc.a, bal.me_left, me_rate, cfg.exclude_away) if me_rate else None
        st.plotly_chart(
            c.balance_chart(cfg, dc.a, daily["me_bal"], me_ideal, me_proj, c.COLORS["me"], "Meal Exchanges (ME)", "Meals left"),
            width="stretch",
        )

        dd_unit = st.radio("DD units", ["Dollars", "Meal-equivalents"], horizontal=True)
        dd_rate = metrics.burn[variant]["dd"].rate
        if dd_unit == "Dollars":
            dd_actual = daily["dd_bal"]
            dd_ideal = m.ideal_series(cfg, cfg.starting_dd, cfg.exclude_away)
            dd_proj = m.projection_series(cfg, dc.a, bal.dd_left, dd_rate, cfg.exclude_away) if dd_rate else None
            dd_y_title = "Dollars left"
        else:
            price = cfg.meal_price
            dd_actual = daily["dd_bal"] / price
            dd_ideal = m.ideal_series(cfg, cfg.starting_dd / price, cfg.exclude_away)
            dd_rate_conv = dd_rate / price if dd_rate else None
            dd_proj = (
                m.projection_series(cfg, dc.a, bal.dd_left / price, dd_rate_conv, cfg.exclude_away)
                if dd_rate_conv
                else None
            )
            dd_y_title = "Meal-equivalents left"
        st.plotly_chart(
            c.balance_chart(cfg, dc.a, dd_actual, dd_ideal, dd_proj, c.COLORS["dd"], "Dining Dollars (DD)", dd_y_title),
            width="stretch",
        )

        pooled_actual = daily["me_bal"] + daily["dd_bal"] / cfg.meal_price
        pooled_total = metrics.targets[variant].pooled_total
        pooled_ideal = (
            m.ideal_series(cfg, pooled_total, cfg.exclude_away) if pooled_total is not None else pd.Series(dtype=float)
        )
        st.plotly_chart(
            c.balance_chart(
                cfg, dc.a, pooled_actual, pooled_ideal, None, c.COLORS["pooled"], "Pooled (ME + DD/price)", "Meal-equivalents left"
            ),
            width="stretch",
        )

    # --- Usage tab --------------------------------------------------------
    with tab_usage:
        pace = metrics.pace[variant]
        st.plotly_chart(c.daily_meals_chart(cfg, daily, pace.me_only, pace.with_dd), width="stretch")

        mix_with_dd = metrics.day_mix[variant]["with_dd"]
        st.plotly_chart(
            c.weekly_mix_chart(cfg, daily, mix_with_dd.one_per_week, mix_with_dd.two_per_week),
            width="stretch",
        )

        loc_col1, loc_col2 = st.columns(2)
        if not df.empty:
            me_counts = (
                df[(df["pot"] == "ME") & (df["kind"].isin(["usage", "reversal"]))].groupby("description").size()
            )
        else:
            me_counts = pd.Series(dtype=float)
        with loc_col1:
            st.plotly_chart(c.me_location_chart(me_counts), width="stretch")

        breakdown = metrics.dd_breakdown
        loc_df = pd.DataFrame(
            [{"location": loc, **vals} for loc, vals in breakdown.by_location.items()],
            columns=["location", "meal_count", "meal_amount", "snack_count", "snack_amount"],
        )
        with loc_col2:
            st.plotly_chart(c.dd_location_chart(loc_df), width="stretch")

    # --- Plan math tab ------------------------------------------------------
    with tab_plan:
        st.markdown("#### 2. Targets")
        t_plain, t_away = metrics.targets["plain"], metrics.targets["away"]
        st.table(
            side_by_side(
                {
                    "Strict target (meals)": (fmt(t_plain.strict_target), fmt(t_away.strict_target)),
                    "Strict ahead/behind (meals)": (fmt(t_plain.strict_delta, signed=True), fmt(t_away.strict_delta, signed=True)),
                    "Pooled total P (meals)": (fmt(t_plain.pooled_total), fmt(t_away.pooled_total)),
                    "Pooled target (meals)": (fmt(t_plain.pooled_target), fmt(t_away.pooled_target)),
                    "Pooled ahead/behind (meals)": (fmt(t_plain.pooled_delta, signed=True), fmt(t_away.pooled_delta, signed=True)),
                }
            )
        )

        st.markdown("#### 3. Allowed pace (meals/day)")
        p_plain, p_away = metrics.pace["plain"], metrics.pace["away"]
        st.table(
            side_by_side(
                {
                    "ME-only": (fmt(p_plain.me_only, digits=2), fmt(p_away.me_only, digits=2)),
                    "With DD": (fmt(p_plain.with_dd, digits=2), fmt(p_away.with_dd, digits=2)),
                    "Note": (p_plain.reason or "", p_away.reason or ""),
                }
            )
        )

        st.markdown("#### 4. Day mix (meals available over remaining days)")
        for label, key in (("ME-only", "me_only"), ("With DD", "with_dd")):
            dm_plain, dm_away = metrics.day_mix["plain"][key], metrics.day_mix["away"][key]
            st.markdown(f"**{label}**")
            st.table(
                side_by_side(
                    {
                        "1-meal days": (fmt(dm_plain.one_meal_days), fmt(dm_away.one_meal_days)),
                        "2-meal days": (fmt(dm_plain.two_meal_days), fmt(dm_away.two_meal_days)),
                        "1-meal days/week": (fmt(dm_plain.one_per_week), fmt(dm_away.one_per_week)),
                        "2-meal days/week": (fmt(dm_plain.two_per_week), fmt(dm_away.two_per_week)),
                        "Note": (dm_plain.note or "", dm_away.note or ""),
                    }
                )
            )

        def spare_str(days: float | None) -> str:
            if days is None:
                return ""
            if abs(days) < 0.05:
                return "exactly at semester end"
            n = f"{abs(days):.1f}".removesuffix(".0")
            return f"{n} days {'spare' if days > 0 else 'short'}"

        def usd(x: float | None) -> str:
            # st.table renders markdown, so a bare "$" would start LaTeX math
            return "n/a" if x is None else f"\\${x:,.2f}"

        st.markdown("#### 5. Run-out at a fixed 2-meals/day pace")

        def runout_cell(r: m.RunOut) -> str:
            if r.date is None:
                return r.reason or "n/a"
            return f"{r.date:%b %d} ({spare_str(r.spare_days)})"

        st.table(
            side_by_side(
                {
                    label: (runout_cell(metrics.run_out["plain"][key]), runout_cell(metrics.run_out["away"][key]))
                    for label, key in (("ME-only", "me_only"), ("With DD", "with_dd"))
                }
            )
        )

        st.markdown("#### 6. Burn rate & projection (at your actual pace so far)")
        for label, key, is_dollar in (("Meal exchanges", "me", False), ("Dining dollars", "dd", True)):
            bp, ba = metrics.burn["plain"][key], metrics.burn["away"][key]

            def cells(b: m.BurnResult) -> dict[str, str]:
                if b.rate is None:
                    reason = b.reason or "n/a"
                    return {k: reason for k in ("Rate", "Run-out", "Spare / short", "Left on last day")}
                amount = usd if is_dollar else (lambda x: fmt(x))
                left = amount(b.leftover_display) + (f" (short {amount(b.shortfall)})" if b.shortfall else "")
                return {
                    "Rate": f"{amount(b.rate) if is_dollar else fmt(b.rate, digits=2)}/day",
                    "Run-out": f"{b.runout_date:%b %d, %Y}" if b.runout_date else "never (no usage)",
                    "Spare / short": spare_str(b.spare_days),
                    "Left on last day": left,
                }

            cp, ca = cells(bp), cells(ba)
            st.markdown(f"**{label}**")
            st.table(side_by_side({k: (cp[k], ca[k]) for k in cp}))
            notes = [n for n in (bp.leftover_note, ba.leftover_note) if n]
            if notes:
                note = f"Plain: {bp.leftover_note or '—'} · Away: {ba.leftover_note or '—'}"
                st.caption(esc(note))

    # --- What-if tab --------------------------------------------------------
    with tab_whatif:
        st.caption("Projects from tomorrow through semester end, consuming ME first, then DD at the configured meal price.")
        meals_per_day = st.slider("Meals per day", min_value=0.0, max_value=4.0, value=2.0, step=0.25)
        wi = m.what_if(cfg, dc.a, bal.me_left, bal.dd_left, meals_per_day, cfg.exclude_away)
        if wi.reason:
            st.info(wi.reason)
        w1, w2 = st.columns(2)
        w1.metric(
            "ME at semester end",
            fmt(wi.me_end, digits=1),
            f"runs out {wi.me_runout.isoformat()}" if wi.me_runout else "lasts to end",
        )
        w2.metric(
            "DD at semester end",
            fmt(wi.dd_end, digits=2, prefix="$"),
            f"runs out {wi.dd_runout.isoformat()}" if wi.dd_runout else "lasts to end",
        )

    # --- Data tab -----------------------------------------------------------
    with tab_data:
        st.markdown("#### Ingest report")
        if report.files:
            files_df = pd.DataFrame(report.files)
            st.dataframe(files_df[["file", "account", "pot", "rows", "new", "duplicate"]], width="stretch", hide_index=True)
        else:
            st.caption("No files ingested yet.")
        if report.warnings:
            st.markdown("**Warnings**")
            for w in report.warnings:
                st.write(f"- {w}")

        st.markdown("#### Chain breaks")
        if report.chain_breaks:
            st.dataframe(pd.DataFrame(report.chain_breaks), width="stretch", hide_index=True)
        else:
            st.caption("None detected.")

        st.markdown("#### Reconciliation")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "pot": "ME",
                        "left (computed)": bal.me_left,
                        "derived from ledger": bal.me_derived_balance,
                        "deposits in window": bal.me_deposits,
                        "warning": bal.me_warning or "",
                    },
                    {
                        "pot": "DD",
                        "left (computed)": bal.dd_left,
                        "derived from ledger": bal.dd_derived_balance,
                        "deposits in window": bal.dd_deposits,
                        "warning": bal.dd_warning or "",
                    },
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(f"Ledger path: `{cfg.ledger_path}`")

        st.markdown("#### DD meal vs. snack breakdown")
        bd = metrics.dd_breakdown
        st.dataframe(
            pd.DataFrame(
                [
                    {"class": "meal", "count": bd.meal_count, "amount": bd.meal_amount},
                    {"class": "snack", "count": bd.snack_count, "amount": bd.snack_amount},
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        if bd.by_location:
            st.dataframe(
                pd.DataFrame([{"location": loc, **vals} for loc, vals in bd.by_location.items()]),
                width="stretch",
                hide_index=True,
            )


def run() -> None:
    # Sidebar and ingest live in the entrypoint so both pages share the same settings and data.
    base_cfg = load_config(_CONFIG_PATH, root=_CONFIG_ROOT)
    cfg = sidebar_config(base_cfg)
    st.sidebar.button("Reload data", help="Re-scan raw-data/ (a rerun already re-ingests automatically).")
    df, report = load_transactions(cfg)

    def dashboard() -> None:
        main(cfg, df, report)

    def tiles() -> None:
        tiles_page(cfg, df)

    pages = [
        st.Page(dashboard, title="Dashboard", icon=":material/monitoring:", default=True),
        st.Page(tiles, title="Tiles", icon=":material/grid_view:", url_path="tiles"),
    ]
    st.navigation(pages).run()


run()
