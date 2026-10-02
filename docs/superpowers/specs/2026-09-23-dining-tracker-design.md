# DineMaster — UVA Dining Plan Tracker: Design

Local Streamlit app tracking meal exchanges (ME) and dining dollars (DD) from CSV exports. No transaction data or balances in source; everything recomputes from `raw-data/` on each page load.

## Stack
Python 3.12, `uv`, Streamlit, pandas, Plotly, pytest. Run: `uv run streamlit run app.py`.

## Layout
```
raw-data/               user drops exports here; never modified; gitignored
data/ledger.csv         merged history, grows over time (generated; gitignored)
config.toml             all parameters, file/column mapping, meal/snack rules
app.py                  Streamlit UI
dinemaster/config.py    load config.toml -> Config dataclass
dinemaster/ingest.py    parse, map file->account->pot, merge into ledger, classify, validate chains
dinemaster/metrics.py   pure functions over a transactions DataFrame + Config
dinemaster/charts.py    Plotly figure builders
tests/                  pytest, synthetic data only
```

## Export format (observed)
File name `<Account>_statement_<from>_to_<to>.csv` (date range may be invalid, e.g. `2026-09-31` — never parse it).
Columns `Date,Description,Amount,Balance`. Date `YYYY-MM-DD HH:MM:SS` (minute precision). Amount signed (debit negative). Balance = running balance of that account after the row. Accounts seen: `Block 160 Meals` (ME), `Dining Dollars`, `Promotional Dining Dollars`, `Additional Dining Dollars Fall` (all DD). Column names and file→account/pot regexes live in config.

## Ingest
1. For each `*.csv` in `raw-data/` (sorted by name): account = filename regex group `account`; pot = first `[[pots]]` rule whose regex matches account. Unknown account, missing columns, or unparseable rows → skip file (or row) and record a warning in the report; never crash.
2. Row key = `(account, timestamp, description, amount rounded to cents)`.
3. **Max-count merge into ledger**: for each key, `add = count_in_this_file − count_in_ledger`; append `max(0, add)` copies. Net effect: a key's final count = max count across any single file. Overlapping re-exports never double-count; genuine same-minute duplicates within one file are kept.
4. Ledger columns: `account, pot, timestamp, description, amount, balance, source_file, first_seen`. Written back only if rows were added. Ledger is never pruned (deleting raw files loses nothing).
5. **Classify** each ledger row (`kind`), rules in config, first match wins:
   - description matches `load_patterns` (default `^Deposit$`) → `load`
   - description matches `adjustment_patterns` (default `(?i)revoke`) → `adjustment`
   - amount > 0 → `reversal` (refund of a swipe/purchase; nets against usage)
   - else → `usage`
6. **DD purchase grouping & meal/snack**: DD `usage` rows with identical `(timestamp, description)` across accounts are one purchase (split tender) — sum amounts. Purchase is `meal` iff description matches none of `snack_patterns` (default Gastons, Supply 1819, Vending, Bodega) AND |amount| ≥ `meal_threshold` (default $8); otherwise `snack`. Every DD purchase is exactly one of the two. DD reversals reduce DD $ usage but don't change meal counts.
7. **Balance-chain check** per account: order by timestamp (resolve ties by trying orderings of the tie group so chains hold); flag every row where `prev.balance + amount ≠ balance` (tol 0.005). First row of an account has no predecessor.
8. Report: per file {rows, new, duplicate, warnings}; chain breaks; ledger path.

Public API:
```python
load_transactions(cfg: Config) -> tuple[pd.DataFrame, IngestReport]
```
Returned DataFrame (whole ledger, all dates): `account, pot ('ME'|'DD'), timestamp (datetime64), date (datetime.date), description, amount (float, signed), balance (float), kind, dd_class ('meal'|'snack'|None — set on DD usage rows; for split purchases, on every row of the group)`.

## Parameters (config.toml defaults; every one overridable in the sidebar for the session)
semester_start 2026-08-21, semester_end 2026-12-18, data_cutoff 2026-08-01, starting_me 160, starting_dd 360.0, meal_price 15.0, dd_rounding {mode "floor", granularity 1.0}, away periods (list of {name, start, end, enabled}; default Thanksgiving 2026-11-25..2026-11-29 enabled), exclude_away (global toggle, default true), as_of (default today), dd_rollover false, dd_leftover_flag 15.0, meal_threshold 8.0, snack_patterns, load/adjustment patterns, file/column mapping.

`round_meals(x) = floor(x / g) * g` (mode floor; ceil/round supported).

## Day counting (stated in UI)
Start and end inclusive. `S`,`E` = semester start/end. `A` = as-of clamped to `[S−1, E]` (if outside semester, show notice; before S ⇒ nothing elapsed).
- `T = (E−S)+1` total days (120). `De = (A−S)+1` elapsed (34 on 9/23). `Dr = E−A` remaining = day after A through E (86).
- Away days: `away_rem` = enabled away days in `(A, E]`; `away_el` in `[S, A]`; `away_tot` in `[S, E]`.
- "Usable" variants when away is applied: `Dr' = Dr − away_rem`, `De' = De − away_el`, `T' = T − away_tot`.
- Fraction remaining `f = Dr/T`, `f' = Dr'/T'`. Progress = `De/T`.

## Usage & balances (window: `data_cutoff ≤ date ≤ A`)
- `me_used` = −sum(amount) of ME rows with kind in {usage, reversal}. `dd_used` likewise for DD ($).
- `me_left = starting_me − me_used`, `dd_left = starting_dd − dd_used` (these drive all metrics and charts).
- Reconciliation (shown, never silently resolved): derived balance = sum over pot's accounts of the last `balance` on/before A (accounts with no row in window contribute their last balance anyway); deposits in window = sum of `load` amounts. Warn when |derived − configured-left| > 0.005 or deposits ≠ starting amount.
- Daily meals = ME net swipes that day + DD `meal` purchases that day. Snacks counted separately.

## Metrics (each computed with away NOT applied and applied; UI shows both)
1. Progress `De/T`.
2. Targets. Strict: `target = starting_me × f`; `delta = me_left − target` (+ ahead). Pooled: `P = starting_me + round_meals(starting_dd/price)`; `target = P × f − round_meals(dd_left/price)`; delta vs `me_left`.
3. Allowed pace: ME-only `me_left / Dr`; with DD `(me_left + round_meals(dd_left/price)) / Dr`. (Away variant uses Dr'.)
4. Day mix for meals `M`, days `R`: `one = clamp(2R − M, 0, R)`, `two = R − one`, per week = count × 7/R. If `M > 2R` note surplus (3-meal days needed); if `M < R` note shortfall. Both ME-only and with-DD.
5. Run-out at 2/day: walk days from A+1, consuming 2 per day (skip away days in away variant) while ≥2 remain; run-out date = last fully covered day. `spare = runout − E` in days (negative = short). ME-only and with-DD (M as in #3).
6. Burn rate per pot: `rate = used / De` (away variant `/ De'`). If used == 0 or De ≤ 0 → "not enough data". Projected run-out: walk usable days from A+1 subtracting rate until balance ≤ 0 (fractional; report date the balance crosses 0, or "lasts past end"). Spare days = run-out − E. Leftover at E = `left − rate × Dr` (away: `Dr'`), floored at 0 for display, also show shortfall. If DD leftover ≥ `dd_leftover_flag` and rollover off → warning "≈$X unspent will be lost"; rollover on → "≈$X carries to spring".
7. DD breakdown: meal vs snack $ and counts, by location (description).

Smoke test: a synthetic scenario in `tests/test_metrics.py` checks each formula end to end against hand-calculated values.

## Charts (Plotly; daily granularity; x from S to E)
1. ME balance: actual = `starting_me − cumulative net usage` by end of each day from S (starts full at S; no deposit spike) through A; ideal = straight line from starting_me before S to 0 at end of E (flat across away days when away applied); dashed projection from A at burn rate. Markers: today (A), semester end, shaded away periods.
2. DD balance in $, toggle to meal-equivalents (÷ price, unrounded).
3. Pooled meal-equivalents (`ME + DD/price`) vs ideal from `P`.
4. Daily meals: stacked bars (ME swipes, DD meals), 7-day rolling average line, horizontal lines for allowed pace (ME-only and with-DD, per current away toggle).
5. Weekly day mix (Mon-start weeks): stacked 0/1/2/3+-meal day counts vs target one-/two-meal days per week (with-DD case).
Extras: what-if slider (meals/day from tomorrow → end balances + run-out, ME first then DD); usage by location bars (ME swipes, DD $); data-health panel (files ingested, new/dup rows, chain breaks, reconciliation).

## Edge cases
Empty/missing `raw-data/` → instructions, no metrics. Bad file → skipped with warning. As-of outside semester → clamped, notice. Zero usage → burn metrics show "not enough data". Dr = 0 → pace/mix show "semester over".

## Testing
pytest: ingest (overlap across files, same-minute duplicates kept, split tender grouping, reversals, bad schema skipped, chain breaks detected, ledger persists after raw file deleted) and metrics (day counting, each formula, edge cases) using synthetic in-test data; no real transactions in the repo.
