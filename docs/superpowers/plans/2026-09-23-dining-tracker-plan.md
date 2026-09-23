# DineMaster Implementation Plan

Spec: `docs/superpowers/specs/2026-09-23-dining-tracker-design.md`. Done already: `pyproject.toml` (uv), `config.toml`, `dinemaster/config.py` (`load_config()`, `Config.round_meals`, `Config.is_away`).

Rules for every task: test-first with pytest (`uv run pytest`); synthetic data only in tests (never copy rows from `raw-data/`); match the formulas in the spec exactly; no network; don't edit files owned by another task.

## Task A — ingest (`dinemaster/ingest.py`, `tests/test_ingest.py`)
Implement spec §Ingest: `load_transactions(cfg) -> (DataFrame, IngestReport)`. Tests build temp dirs with CSVs and a `Config` via `dataclasses.replace(load_config(), raw_dir=..., ledger_path=...)`. Cover: overlapping files → no double count; identical rows within one file kept; ledger survives raw file deletion; second run adds 0 rows; split-tender DD purchase classed once as meal/snack by summed amount; snack patterns; meal threshold; reversal/load/adjustment kinds; bad-schema file skipped with warning; unknown account skipped; chain break detected; same-timestamp tie ordering doesn't false-flag; empty/missing raw dir → empty frame with correct columns.

## Task B — metrics (`dinemaster/metrics.py`, `tests/test_metrics.py`)
Pure functions over the ingest DataFrame (columns in spec §Ingest public API) + `Config`. Implement spec §Day counting, §Usage & balances, §Metrics 1–7, plus series for charts:
- `day_counts(cfg)` → dataclass (T, De, Dr, away_tot/el/rem, usable variants, clamped as_of, notice).
- `balances(df, cfg)` → used/left per pot, derived balances, deposits, reconciliation warnings.
- `daily(df, cfg)` → DataFrame indexed by every date S..A: `me_swipes, dd_meals, dd_snacks, dd_spend, meals, me_bal, dd_bal` (end-of-day balances starting from configured starting amounts).
- `ideal_series(cfg, start_amount, apply_away)` (S..E), `projection_series(cfg, left, rate, apply_away)` (A..min(run-out, E)).
- `run_out_two_per_day(...)`, `burn_projection(...)`, `day_mix(M, R)`, `what_if(cfg, me_left, dd_left, meals_per_day, apply_away)`.
- `compute_metrics(df, cfg)` → one nested dict/dataclass holding metrics 1–7 with `plain` and `away` variants, documented in a docstring so the UI author can consume it without reading internals.
Test with a hand-built DataFrame. Include the spec's smoke-test scenario rebuilt synthetically (as_of 2026-09-23; 43 ME net used; $54.83 DD used) and check the approximate values listed there; plus edge cases (zero usage, as_of before S / after E, Dr=0, M>2R, M<R).

## Task C — charts + app (`dinemaster/charts.py`, `app.py`) — after A and B
Spec §Charts, §Parameters (sidebar overrides via `dataclasses.replace`), §Edge cases, data-health panel, what-if slider, day-convention note in UI. Ingest runs on every rerun (data is tiny; page reload = fresh data). Verify by running the app headless and checking there are no exceptions.

## Task D — verify & ship (me)
Run all tests, drive the app in the browser, compare against the smoke numbers, README, commit, create private GitHub repo.
