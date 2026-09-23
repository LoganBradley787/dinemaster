# DineMaster

A local dashboard that tracks a UVA dining plan (meal exchanges + dining dollars) and shows whether you're on pace to last the semester.

```bash
uv run streamlit run app.py
```

## Updating data
1. Export each plan account's statement as CSV (any date range; overlap is fine).
2. Drop the files into `raw-data/`.
3. Reload the page.

New rows are merged into `data/ledger.csv` using a max-count dedup on (account, timestamp, description, amount). Re-exports that overlap earlier ones never double-count, and deleting old exports never loses history. The Data tab shows how many rows each file added and flags any breaks in the running-balance chain.

`raw-data/` and `data/` are gitignored, so transaction history never gets committed.

## Configuration
`config.toml` holds every default: semester dates, data cutoff, starting balances, meal price, DD rounding, away periods, the meal/snack rules, and the file/column mapping for the export format. The sidebar overrides any of these for the current session, including an "as of" date for replaying the past.

## Tests
```bash
uv run pytest -q
```

Design: `docs/superpowers/specs/2026-09-23-dining-tracker-design.md`
