"""DineMaster Streamlit app.

Recomputes everything from raw-data/ on every rerun (ingest is not cached —
the data is tiny). Set the environment variable DINEMASTER_CONFIG to a path
to an alternate config.toml (used by tests); raw_dir/ledger_path in that file
are resolved relative to its own parent directory.

Calendar files (`[calendar]` in config.toml) are merged into the config before
the session's settings are applied, so away periods found there get switches on
the Configuration page like the configured ones, and markers show on the charts.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from dinemaster import budget, compare, compare_charts, forecast_charts, habits, habits_charts, recap
from dinemaster import charts as c
from dinemaster import forecast as fcast
from dinemaster import metrics as m
from dinemaster.calendar_sync import CalendarResult, apply_calendar
from dinemaster.config import ROOT, Config, load_config
from dinemaster.export_links import build_links, latest_by_account
from dinemaster.freshness import data_through
from dinemaster.ingest import load_transactions
from dinemaster.tiles import ago, build_page_html
from dinemaster.tiles import chance as chance_text

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


def short(d: date) -> str:
    """'Oct 1' — a date without the year or a zero-padded day."""
    return f"{d:%b} {d.day}"


def _calendar_signature(cfg: Config) -> tuple:
    """Changes whenever the config file or any local .ics file changes, so the cached calendar is redone."""
    folder = cfg.calendar.ics_dir
    files: tuple = ()
    if folder is not None and folder.is_dir():
        files = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(folder.glob("*.ics")))
    return (str(_CONFIG_PATH), _CONFIG_PATH.stat().st_mtime_ns, files)


@st.cache_data(ttl=600, show_spinner=False)
def _calendar_merge(_cfg: Config, signature: tuple):
    """`apply_calendar`, remembered for a few minutes so a slow or dead feed isn't retried on every rerun."""
    merged, result = apply_calendar(_cfg)
    return merged.away_periods, merged.markers, result


def with_calendar(base_cfg: Config) -> tuple[Config, CalendarResult]:
    """`base_cfg` plus the calendar's away periods and chart markers, and what the calendars offered."""
    away_periods, markers, result = _calendar_merge(base_cfg, _calendar_signature(base_cfg))
    return dataclasses.replace(base_cfg, away_periods=away_periods, markers=markers), result


def side_by_side(rows: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """Build a Plain/Away comparison table from {label: (plain, away)}."""
    return pd.DataFrame(rows, index=["Plain", "Away"]).T


# Settings widgets live on the Configuration page; their values are kept in session state under
# these prefixes so the other pages see them too.
_SETTING_PREFIXES = ("set_", "away_")
_SETTING_FIELDS = (
    "semester_start", "semester_end", "data_cutoff", "as_of", "starting_me", "starting_dd", "meal_price",
    "rounding_mode", "rounding_granularity", "meal_threshold", "dd_rollover", "dd_leftover_flag", "exclude_away",
)  # fmt: skip


def _away_key(i: int, period) -> str:
    # Calendar periods come and go as .ics files change, so they are keyed by their dates rather
    # than by position (a position key would hand one period another's edits).
    return f"cal_{period.start}_{period.end}" if period.source == "calendar" else str(i)


def keep_settings() -> None:
    """Streamlit forgets a widget's value when its page isn't showing; writing it back keeps it."""
    for key in list(st.session_state):
        if key.startswith(_SETTING_PREFIXES):
            st.session_state[key] = st.session_state[key]


def session_config(base_cfg: Config) -> Config:
    """`base_cfg` with whatever was changed on the Configuration page this session."""
    state = st.session_state
    away_periods = []
    for i, period in enumerate(base_cfg.away_periods):
        key = _away_key(i, period)
        away_periods.append(
            dataclasses.replace(
                period,
                enabled=state.get(f"away_enabled_{key}", period.enabled),
                start=state.get(f"away_start_{key}", period.start),
                end=state.get(f"away_end_{key}", period.end),
            )
        )
    changed = {f: state[f"set_{f}"] for f in _SETTING_FIELDS if f"set_{f}" in state}
    return dataclasses.replace(base_cfg, **changed, away_periods=tuple(away_periods))


def settings_section(base_cfg: Config) -> None:
    """The plan settings, as widgets. Changes last for this session; config.toml is never written."""
    state = st.session_state
    for f in _SETTING_FIELDS:
        value = getattr(base_cfg, f)
        state.setdefault(f"set_{f}", float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value)

    dates, plan = st.columns(2)
    with dates:
        st.markdown("**Semester**")
        st.date_input("Semester start", key="set_semester_start")
        st.date_input("Semester end", key="set_semester_end")
        st.date_input("Ignore data before", key="set_data_cutoff")
        st.date_input(
            "As of",
            key="set_as_of",
            help="Look at the plan as it stood on a past day. Start and end dates count as full days; "
            "days left are counted from the day after this one.",
        )
    with plan:
        st.markdown("**Plan**")
        st.number_input("Starting meal exchanges", step=1.0, key="set_starting_me")
        st.number_input("Starting dining dollars ($)", step=1.0, key="set_starting_dd")
        st.number_input("Meal price ($)", step=0.5, min_value=0.01, key="set_meal_price")
        st.checkbox("Leave out days away when counting days left", key="set_exclude_away")

    with st.expander("Days away"):
        if not base_cfg.away_periods:
            st.caption("None configured.")
        for i, period in enumerate(base_cfg.away_periods):
            key = _away_key(i, period)
            state.setdefault(f"away_enabled_{key}", period.enabled)
            state.setdefault(f"away_start_{key}", period.start)
            state.setdefault(f"away_end_{key}", period.end)
            from_calendar = " · from calendar" if period.source == "calendar" else ""
            on, start, end = st.columns([2, 1, 1], vertical_alignment="bottom")
            on.checkbox(esc(f"**{period.name}**{from_calendar}"), key=f"away_enabled_{key}")
            start.date_input("Start", key=f"away_start_{key}")
            end.date_input("End", key=f"away_end_{key}")

    with st.expander("Fine print"):
        st.number_input("A dining-dollar purchase counts as a meal from ($)", step=1.0, key="set_meal_threshold")
        st.selectbox("Rounding dining dollars into meals", ["floor", "round", "ceil"], key="set_rounding_mode")
        st.number_input("Round to the nearest (meals)", step=0.5, min_value=0.01, key="set_rounding_granularity")
        st.checkbox("Dining dollars roll over to spring", key="set_dd_rollover")
        st.number_input("Warn when leftover dining dollars pass ($)", step=1.0, key="set_dd_leftover_flag")


def fetch_data(cfg: Config) -> None:
    """A browser window opens for the login, then every account's CSV lands in raw_dir."""
    with st.spinner("A browser window is open. Log in there if it asks, then wait."):
        try:
            done = subprocess.run(
                [sys.executable, "-m", "dinemaster.fetch"],
                cwd=ROOT,
                env={**os.environ, "DINEMASTER_CONFIG": str(_CONFIG_PATH)},
                capture_output=True,
                text=True,
                timeout=420,
            )
        except subprocess.TimeoutExpired:
            st.error("Fetch failed: it took too long.")
            return
    if done.returncode:
        st.error(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "Fetch failed.")
        return
    st.session_state["fetched"] = done.stdout.strip().splitlines()
    st.rerun()


def data_section(cfg: Config, df: pd.DataFrame) -> None:
    """How current the data is, and the ways to bring it up to date."""
    latest = latest_by_account(df)
    today = date.today()
    through = data_through(cfg, df)
    if through:
        st.markdown(f"Your data runs through **{short(through)}** ({ago((today - through).days)}).")
    else:
        st.markdown("No transactions loaded yet.")

    can_fetch = bool(cfg.export.login_url and cfg.export.accounts)
    fetch_col, reload_col, _ = st.columns([1, 1, 2])
    if can_fetch and fetch_col.button(
        "Get new data", type="primary", width="stretch", help="Opens a browser window on the dining site. Log in if it asks."
    ):
        fetch_data(cfg)
    reload_col.button(
        "Re-read files",
        width="stretch",
        help=f"Read the files in {cfg.raw_dir.name}/ and your calendars again. Nothing is downloaded.",
        on_click=_calendar_merge.clear,
    )
    for line in st.session_state.pop("fetched", []):
        st.caption(f"Downloaded {line}")
    if latest:
        st.caption("Last transaction: " + " · ".join(f"{name} {short(d)}" for name, d in sorted(latest.items())))

    if not cfg.export.accounts:
        st.caption(f"To add data, put new CSV exports into `{cfg.raw_dir}`.")
        return
    with st.expander("Download by hand"):
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
                    f"Export each as CSV and put the files into `{cfg.raw_dir}`. "
                    "If a link says you're logged out, the session expired; paste a fresh URL."
                )


def configuration_page(base_cfg: Config, cfg: Config, df: pd.DataFrame) -> None:
    st.title("Configuration")
    st.subheader("Data")
    data_section(cfg, df)
    st.subheader("Settings")
    st.caption("Changes here last until you close the app. To keep them, edit `config.toml`.")
    settings_section(base_cfg)


def tiles_page(cfg: Config, df: pd.DataFrame) -> None:
    """Metro-style summary: the same metrics as the dashboard, as plain-sentence tiles."""
    if df.empty:
        st.info(f"No dining data found yet. Drop your statement CSV exports into `{cfg.raw_dir}` and reload.")
        return
    st.html(build_page_html(cfg, df, date.today()))


def forecast_tab(cfg: Config, daily: pd.DataFrame, fc: fcast.Forecast) -> None:
    """Day-of-week view plus where each pot is likely headed."""
    profile, o = fc.profile, fc.observed_through
    no_history = profile.attrs.get("reason")
    if no_history:
        st.info(f"No forecast yet: {no_history}.")

    st.markdown("#### Your week, day by day")
    st.plotly_chart(forecast_charts.weekday_profile_chart(profile), width="stretch")
    if not no_history:
        rates = profile.set_index("weekday")["meals_rate"]
        if rates.sum() > 0:
            heavy = [day for day, r in rates.items() if r >= rates.max() - 0.005]
            light = [day for day, r in rates.items() if r <= rates.min() + 0.005]
            if rates.max() - rates.min() > 0.01:
                st.markdown(
                    f"**{' and '.join(heavy)}** {'is' if len(heavy) == 1 else 'are'} your heaviest "
                    f"(about {rates.max():.2f} meals a day); **{' and '.join(light)}** "
                    f"{'is' if len(light) == 1 else 'are'} the lightest ({rates.min():.2f}). "
                    "The forecast below and the Daily plan tab both follow this pattern."
                )
        table = pd.DataFrame(
            {
                "Day": profile["weekday"],
                "Days observed": profile["days"],
                "Meals / day": profile["meals_avg"],
                "ME swipes / day": profile["me_avg"],
                "DD meals / day": profile["dd_meals_avg"],
                "DD $ / day": profile["dd_spend_avg"],
                "Snack $ / day": profile["dd_snack_avg"],
                "Forecast meals / day": profile["meals_rate"],
                "Forecast DD $ / day": profile["dd_spend_rate"],
                "Share of week": profile["share"] * 100,
            }
        )
        two, usd, pct = (st.column_config.NumberColumn(format=f) for f in ("%.2f", "$%.2f", "%.0f%%"))
        st.dataframe(
            table,
            width="stretch",
            hide_index=True,
            column_config={
                "Meals / day": two, "ME swipes / day": two, "DD meals / day": two, "Forecast meals / day": two,
                "DD $ / day": usd, "Snack $ / day": usd, "Forecast DD $ / day": usd, "Share of week": pct,
            },
        )  # fmt: skip
        away_note = " Away days are left out." if cfg.exclude_away else ""
        st.caption(
            f"Observed: {short(max(cfg.semester_start, cfg.data_cutoff))} – {short(o)} "
            f"({profile.attrs.get('observed_days', 0)} days).{away_note} The left columns are plain averages of "
            "those days. The forecast columns weigh recent weeks more (a day "
            f"{cfg.forecast.half_life_days:g} days old counts half) and pull a weekday with few "
            "observations toward your overall average."
        )

    st.markdown("#### Where each pot is headed")
    if o < cfg.semester_end:
        st.caption(
            f"Solid line: what the data shows, through {short(o)}. Dashed line and band: forecast for "
            f"{short(o + timedelta(days=1))} – {short(cfg.semester_end)}, where each future day behaves like past "
            f"days of the same weekday. The band is the middle {fc.band[1] - fc.band[0]:g}% of "
            f"{fc.simulations:,} simulated semesters."
        )
    seen = daily[daily.index <= o]
    pots = (
        (fc.me, seen["me_bal"], "Meal exchanges — forecast", "meals", "Meal exchanges", lambda x: f"{x:.0f}"),
        (fc.dd, seen["dd_bal"], "Dining dollars — forecast", "$", "Dining dollars", lambda x: f"${x:,.0f}"),
    )
    for pf, actual, title, unit, noun, amount in pots:
        st.plotly_chart(c.add_markers(forecast_charts.forecast_chart(pf, actual, cfg, title, unit), cfg), width="stretch")
        if not pf.available:
            st.caption(f"{noun}: no forecast — {pf.reason}.")
            continue
        chance, leftover, runout = st.columns(3)
        chance.metric(f"Chance {noun.lower()} last", chance_text(pf.prob_lasts))  # same wording as the tiles
        leftover.metric(
            f"Likely left on {short(cfg.semester_end)}",
            esc(f"{amount(pf.leftover_lo)} – {amount(pf.leftover_hi)}"),  # metric values are markdown: "$a – $b" would be math
            esc(f"middle estimate {amount(pf.leftover_p50)}"),
            delta_color="off",
        )
        if pf.runout_p50 is not None:
            runout.metric(
                "If they run out, likely",
                f"{short(pf.runout_lo)} – {short(pf.runout_hi)}",
                f"most often around {short(pf.runout_p50)}",
                delta_color="off",
            )
        else:
            runout.metric("Run-out before semester end", "Unlikely", "under 1 in 20 simulations", delta_color="off")
        if pf.reason:
            st.caption(f"Note: {pf.reason}.")


def plan_tab(cfg: Config, fc: fcast.Forecast) -> None:
    """A meal count for every remaining day, weighted by weekday, plus the spend-down pace."""
    o, profile = fc.observed_through, fc.profile
    start = o + timedelta(days=1)
    me_left, dd_left = fc.me.start_balance, fc.dd.start_balance
    today = cfg.as_of

    mode = st.radio("Plan with", ["Exchanges + dining dollars", "Exchanges only"], horizontal=True, key="plan_mode")
    plan = budget.daily_budget(cfg, me_left, dd_left, profile, start, include_dd=mode != "Exchanges only")
    info = plan.attrs

    st.subheader(budget.today_line(plan, today))
    if plan.empty or info["reason"]:
        st.caption(f"Nothing to plan: {info['reason']}.")
    else:
        parts = f"{info['me_meals']} exchanges"
        if info["include_dd"]:
            parts += f" + {info['dd_meals']} meals from dining dollars"
        pattern = "spread evenly (no weekday pattern yet)" if info["uniform"] else "more on your usual heavy days"
        st.caption(
            f"{info['total']} meals to use ({parts}) over {info['usable_days']} days, "
            f"{short(plan.index[0])} – {short(plan.index[-1])}: {pattern}, never more than {info['cap']} in a day."
        )
        if info["surplus"]:
            st.warning(
                f"Even at {info['cap']} meals every day, {info['surplus']} meals would go unused by semester end."
            )
        if (today - o).days > 1:
            st.caption(
                f"The data ends {short(o)}, so the plan starts {short(start)} and assumes nothing eaten since. "
                "Update the data for a sharper plan."
            )

        first = max(today, plan.index[0])
        fortnight = [d for d in (first + timedelta(days=i) for i in range(14)) if d <= plan.index[-1]]
        if fortnight:
            st.markdown("#### Next 14 days")
            grid: dict[str, dict[str, str]] = {}
            for d in fortnight:
                row = grid.setdefault(f"Week of {short(d - timedelta(days=d.weekday()))}", dict.fromkeys(habits.WEEKDAYS, ""))
                planned = "away" if plan.at[d, "away"] else str(int(plan.at[d, "meals"]))
                row[habits.WEEKDAYS[d.weekday()]] = f"{short(d)} · {planned}"
            st.table(pd.DataFrame.from_dict(grid, orient="index"))

        st.markdown("#### The plan by day of the week")
        usable = plan[~plan["away"]]
        per_weekday = usable.groupby("weekday")["meals"].agg(["mean", "count"]).reindex(list(budget.WEEKDAY_NAMES))
        usual = profile.set_index("weekday")["meals_rate"].reindex(list(budget.WEEKDAY_NAMES))
        st.table(
            pd.DataFrame(
                {
                    "Planned meals / day": [fmt(x, digits=1) if pd.notna(x) else "—" for x in per_weekday["mean"]],
                    "Your usual meals / day": [fmt(x, digits=2) for x in usual.fillna(0.0)],
                    "Days left": [str(int(n)) if pd.notna(n) else "0" for n in per_weekday["count"]],
                },
                index=habits.WEEKDAYS,
            ).T
        )
        st.caption("Each remaining day gets a share in proportion to how much you usually eat on that weekday.")

    st.markdown("#### Spend-down: both pots at zero on the last day")
    sd = budget.spend_down(cfg, me_left, dd_left, profile, start)
    st.markdown(esc(sd.sentence))
    if sd.reason is None:
        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Exchanges / week", fmt(sd.exchanges_per_week))
        s2.metric("DD meals / week", fmt(sd.dd_meals_per_week))
        s3.metric("Meals / day", fmt(sd.meals_per_day, digits=2))
        s4.metric("Set aside for snacks", fmt(sd.snack_reserve, digits=0, prefix="$"))
        s5.metric("DD left unplanned", fmt(sd.dd_unallocated, digits=2, prefix="$"))
        st.caption(
            esc(
                f"{sd.usable_days} usable days ({sd.weeks:.1f} weeks) through {short(sd.end)}. Of ${dd_left:,.2f} in "
                f"dining dollars, about ${sd.snack_reserve:,.0f} is expected to go to snacks at your usual weekday "
                f"rates, leaving ${sd.dd_for_meals:,.2f} for {sd.dd_meals} meals at ${cfg.meal_price:,.2f}. "
                "The daily plan above turns every dining dollar into meals, so it counts more dining-dollar meals "
                "than this does."
            )
        )


def habits_tab(cfg: Config, df: pd.DataFrame) -> None:
    """When and where meals happen, streaks, late nights, and a week-by-week recap."""
    matrix = habits.hour_weekday_matrix(df, cfg)
    st.plotly_chart(habits_charts.heatmap_chart(matrix), width="stretch")
    slot = habits.busiest_slot(matrix)
    if slot:
        st.caption(f"Busiest slot: {slot[0]} around {habits.format_hour(slot[1])} ({slot[2]} meals so far).")

    st.markdown("#### Streaks")
    sk = habits.streaks(df, cfg)
    if sk.reason:
        st.caption(f"Nothing to show: {sk.reason}.")
    else:

        def run_metric(col, label: str, run: habits.Run, fallback: str) -> None:
            col.metric(label, f"{run.length} day{'' if run.length == 1 else 's'}")
            if run.length:
                where = f"{run.place}, " if run.place else ""
                col.caption(esc(f"{where}{short(run.start)} – {short(run.end)}"))
            else:
                col.caption(fallback)

        c1, c2, c3, c4 = st.columns(4)
        run_metric(c1, "Current meal streak", sk.current, f"no meal on {short(sk.observed_through)}")
        run_metric(c2, "Longest meal streak", sk.meal_streak, "none yet")
        run_metric(c3, "Longest gap without a meal", sk.gap, "no day without a meal")
        run_metric(c4, "Longest run at one place", sk.same_place, "none yet")

    st.markdown("#### Late night")
    ln = habits.late_night(df, cfg)
    window = f"{habits.format_hour(ln.start_hour)} – {habits.format_hour(ln.end_hour)}"
    if ln.reason:
        st.caption(f"Nothing to show: {ln.reason}.")
    elif ln.count == 0:
        st.caption(f"Nothing bought late at night ({window}) so far.")
    else:
        l1, l2, l3 = st.columns(3)
        l1.metric("Late-night visits", f"{ln.count}", f"on {ln.nights} night{'' if ln.nights == 1 else 's'}", delta_color="off")
        l2.metric("Dining dollars spent late", fmt(ln.dd_dollars, digits=2, prefix="$"))
        l3.metric("Share of all DD spending", f"{ln.dd_share * 100:.0f}%" if ln.dd_share is not None else "n/a")
        st.caption(
            esc(
                f"Late night is {window}: {ln.me_swipes} exchange swipes and {ln.dd_purchases} dining-dollar "
                f"purchases, most often at {ln.top_place} ({ln.top_place_count})."
            )
        )

    st.markdown("#### Places")
    places = habits.place_stats(df, cfg)
    st.plotly_chart(habits_charts.place_chart(places), width="stretch")
    if not places.empty:
        st.dataframe(
            pd.DataFrame(
                {
                    "Place": places.index,
                    "Visits": places["visits"].to_numpy(),
                    "ME swipes": places["swipes"].to_numpy(),
                    "DD meals": places["dd_meals"].to_numpy(),
                    "DD snacks": places["dd_snacks"].to_numpy(),
                    "DD spent": [f"${x:,.2f}" for x in places["dd_spend"]],
                    "Usual time": [habits.format_hour(h) for h in places["typical_hour"]],
                    "Favorite day": places["favorite_weekday"].to_numpy(),
                }
            ),
            width="stretch",
            hide_index=True,
        )

    st.markdown("#### Weekly recap")
    weeks = recap.weeks_available(df, cfg)
    if not weeks:
        st.caption("No observed weeks yet.")
        return
    week = st.selectbox(
        "Week",
        weeks,
        index=len(weeks) - 1,
        format_func=lambda d: f"Week of {short(d)}" + (" (latest)" if d == weeks[-1] else ""),
        key="recap_week",
    )
    r = recap.weekly_recap(df, cfg, week)
    if r.reason:
        st.caption(f"Nothing to show: {r.reason}.")
        return
    st.markdown("\n".join(f"- {esc(line)}" for line in r.lines))
    st.caption(f"{short(r.start)} – {short(r.end)} ({r.days} of 7 days observed).")


def compare_tab(cfg: Config, df: pd.DataFrame) -> None:
    """This semester next to each past semester in the config's history."""
    summaries = compare.semester_summaries(df, cfg)
    if not cfg.history:
        st.caption("No past semesters configured — add `[[history.semesters]]` entries to config.toml to compare.")

    def per(usage: compare.PotUsage | None, field: str, money: bool) -> str:
        value = getattr(usage, field) if usage is not None else None
        if value is None:
            return "—"
        return f"${value:,.2f}" if money else (f"{value:.0f}" if field == "used" else f"{value:.2f}")

    rows = []
    for s in summaries:
        covered = f"{short(s.covered_start)} – {short(s.covered_end)}" if s.covered_start else "—"
        top = s.top_places[0] if s.top_places else None
        has_dd = s.dd is not None
        rows.append(
            {
                "Semester": s.name + (" (now)" if s.current else ""),
                "Data covers": covered,
                "Days covered": f"{s.covered_days} of {s.total_days}",
                "ME swipes": per(s.me, "used", False),
                "ME / day": per(s.me, "per_day", False),
                "ME / week": per(s.me, "per_week", False),
                "DD spent": per(s.dd, "used", True),
                "DD / day": per(s.dd, "per_day", True),
                "DD / week": per(s.dd, "per_week", True),
                "DD meals": f"{s.dd_meal_count} (${s.dd_meal_amount:,.0f})" if has_dd else "—",
                "DD snacks": f"{s.dd_snack_count} (${s.dd_snack_amount:,.0f})" if has_dd else "—",
                "Top place": f"{top.place} ({top.visits})" if top else "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    for s in summaries:
        if s.note:
            st.caption(esc(f"**{s.name}** — {s.note}"))

    st.plotly_chart(compare_charts.per_day_comparison_chart(summaries), width="stretch")
    weekly = compare.weekly_usage(df, cfg)
    for pot in compare.POTS:
        st.plotly_chart(compare_charts.weekly_overlay_chart(weekly, pot), width="stretch")
    st.caption("Hollow markers are part weeks (the edge of an export, or the week in progress), so they total fewer days.")


def calendar_section(cfg: Config, cal: CalendarResult) -> None:
    """Data tab: which calendars were read, what they contributed, and anything that went wrong."""
    st.markdown("#### Calendar")
    for w in cal.warnings:
        st.warning(esc(f"Calendar: {w}"))
    if not cal.sources:
        where = f"`{cfg.calendar.ics_dir}`" if cfg.calendar.ics_dir else "the folder named by `[calendar] ics_dir`"
        st.caption(
            f"No calendars read. Drop `.ics` files into {where} (or list feed URLs in config.toml): events named "
            "like a break or trip become away periods, and reading days and exams become chart markers."
        )
        return
    st.caption("Read: " + ", ".join(f"`{name}`" for name in cal.sources))
    merged = {(p.start, p.end) for p in cfg.away_periods if p.source == "calendar"}
    found = [
        {
            "what": "away period",
            "name": p.name,
            "start": p.start,
            "end": p.end,
            "status": "added (switch it off under Configuration)" if (p.start, p.end) in merged else "already covered by a configured period",
        }
        for p in cal.away
    ] + [{"what": "chart marker", "name": mk.name, "start": mk.start, "end": mk.end, "status": "shown on balance charts"} for mk in cal.markers]
    if found:
        st.dataframe(pd.DataFrame(found), width="stretch", hide_index=True)
    else:
        st.caption("No events matched the away or marker patterns.")


def main(cfg: Config, df: pd.DataFrame, report, cal: CalendarResult) -> None:
    dc = m.day_counts(cfg)
    metrics = m.compute_metrics(df, cfg)
    bal = metrics.balances
    variant = "away" if cfg.exclude_away else "plain"

    st.title("DineMaster")
    through = data_through(cfg, df)
    st.caption(f"As of {short(dc.a)}" + (f" · data through {short(through)}" if through else ""))

    # --- Alerts -----------------------------------------------------------
    raw_has_csvs = any(cfg.raw_dir.glob("*.csv")) if cfg.raw_dir.exists() else False
    if not raw_has_csvs:
        st.info(
            f"No dining data found yet. Drop your UVA statement CSV exports into `{cfg.raw_dir}` "
            "(one file per account) and reload this page."
        )
    if dc.notice:
        st.warning(dc.notice)
    if bal.me_warning:
        st.warning(esc(f"ME reconciliation: {bal.me_warning}"))
    if bal.dd_warning:
        st.warning(esc(f"DD reconciliation: {bal.dd_warning}"))
    for w in report.warnings:
        st.warning(esc(f"Ingest: {w}"))
    jumps = [b for b in report.chain_breaks if b["timestamp"].date() >= cfg.data_cutoff]
    if jumps:
        st.warning(
            f"The dining site's balance changed {len(jumps)} time(s) this semester with no transaction to explain it. "
            'See "Balance jumps" on the Data tab.'
        )
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
    fc = fcast.forecast(df, cfg)

    tab_balances, tab_forecast, tab_daily, tab_usage, tab_habits, tab_compare, tab_plan, tab_whatif, tab_data = st.tabs(
        ["Balances", "Forecast", "Daily plan", "Usage", "Habits", "Compare", "Plan math", "What-if", "Data"]
    )

    with tab_forecast:
        forecast_tab(cfg, daily, fc)
    with tab_daily:
        plan_tab(cfg, fc)
    with tab_habits:
        habits_tab(cfg, df)
    with tab_compare:
        compare_tab(cfg, df)

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

        calendar_section(cfg, cal)

        st.markdown("#### Balance jumps")
        st.caption(
            "Times when an account's balance on the dining site moved by more or less than the transaction "
            "listed, as if the site changed the balance without listing why. Usually a correction by the dining office."
        )
        if report.chain_breaks:
            jumps_df = pd.DataFrame(
                {
                    "When": f"{b['timestamp']:%b %d, %Y}",
                    "Account": b["account"],
                    "At this transaction": b["description"],
                    "Balance should be": f"{b['expected']:,.2f}",
                    "Site says": f"{b['actual']:,.2f}",
                    "Unexplained": f"{b['actual'] - b['expected']:+,.2f}",
                }
                for b in report.chain_breaks
            )
            st.dataframe(jumps_df, width="stretch", hide_index=True)
        else:
            st.caption("None found.")

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
    # Settings and ingest live in the entrypoint so every page shares the same settings and data.
    # The calendar is merged in first so its away periods get their own switches under Configuration.
    keep_settings()
    base_cfg, cal = with_calendar(load_config(_CONFIG_PATH, root=_CONFIG_ROOT))
    cfg = session_config(base_cfg)
    df, report = load_transactions(cfg)

    def dashboard() -> None:
        main(cfg, df, report, cal)

    def tiles() -> None:
        tiles_page(cfg, df)

    def configuration() -> None:
        configuration_page(base_cfg, cfg, df)

    pages = [
        st.Page(dashboard, title="Dashboard", icon=":material/monitoring:", default=True),
        st.Page(tiles, title="Tiles", icon=":material/grid_view:", url_path="tiles"),
        st.Page(configuration, title="Configuration", icon=":material/settings:", url_path="configuration"),
    ]
    st.navigation(pages).run()


run()
