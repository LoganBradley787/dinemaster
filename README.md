# DineMaster

A (very unnecessary) dashboard that runs on your own computer and answers one question: **will my dining plan last the semester?**

Download your transaction history from the dining account website. Put the files in a folder. DineMaster then shows:

- the meal exchanges and dining dollars that remain
- if you are ahead of or behind an even pace
- the number of meals you can eat today
- the date when each balance will be empty

I made it for a UVA meal plan (meal exchanges and dining dollars). You can change the plan size, the dates, and the file format in the settings.

![Tiles page](docs/screenshots/tiles.png)

*The screenshots show demo data that is not real.*

## What you get

**Tiles**: a summary in the style of Windows 8. Each tile gives one fact. Some tiles turn over and show a second fact.

**Dashboard**: charts and details in nine tabs.

| Tab | Contents |
|---|---|
| Balances | Your balances through time, compared with an even pace |
| Forecast | Your usual week, and a projection with a probable range |
| Daily plan | The number of meals to eat each day for the next two weeks |
| Usage | The meals for each day and each week |
| Habits | The times and places you eat, streaks, late-night purchases, and a weekly summary |
| Compare | This semester compared with earlier semesters |
| Plan math | The result of each formula, with and without your days away |
| What-if | The result of a meals-per-day rate that you select |
| Data | The files that the app imported, and possible problems |

![Dashboard](docs/screenshots/dashboard.png)

<details>
<summary>More screenshots</summary>

**Forecast**
![Forecast](docs/screenshots/forecast.png)

**Daily plan**
![Daily plan](docs/screenshots/daily-plan.png)

**Habits**
![Habits](docs/screenshots/habits.png)

</details>

## Try the demo

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). This tool installs Python and all other necessary software. Then do these commands in a terminal:

```bash
git clone https://github.com/LoganBradley787/dinemaster.git
cd dinemaster
uv run python demo/make_demo.py
DINEMASTER_CONFIG=demo/config.toml uv run streamlit run app.py
```

A browser tab opens at `http://localhost:8501`. It shows a semester of demo data. To stop the app, press `Ctrl+C` in the terminal.

## Use your own data

1. **Set your plan.** Open `config.toml` in a text editor. The file contains my plan as an example. Change these values:
   - `start` and `end`: your first day and last day on campus this semester
   - `starting_me` and `starting_dd`: the meal exchanges and dining dollars at the start of the plan
   - `meal_price`: the approximate price of a meal when you pay with dining dollars
   - `[[away.periods]]`: the days when you are away. Delete this entry if it does not apply.
2. **Download your transactions.** Go to the dining account website. Open the statement for each account. Export each statement as a CSV file. You can use any date range.
3. **Put the files in the project.** Make a folder with the name `raw-data` in the project folder. Put the CSV files in it. Do not change the file names. DineMaster uses the names to identify the accounts.
4. **Start the app.**

   ```bash
   uv run streamlit run app.py
   ```

### Update your data

Download new exports at any time. Put them in `raw-data` with the old files. Then reload the page.

DineMaster counts each transaction only one time, even if it is in more than one file. Old data stays in the app. The top of the dashboard shows the last date that your data includes. The Data tab shows the number of new transactions from each file.

### Optional: add your calendar

Make a folder with the name `calendar`. Put `.ics` calendar files in it. Most calendar apps can export this file type.

DineMaster uses events with names such as "break", "recess", "home", or "trip" as days away. It shows reading days and exams as marks on the charts. You can set each event to off in the sidebar.

## Your data stays on your computer

DineMaster runs on your computer and sends no data to other computers. Git ignores the folders `raw-data/`, `data/`, and `calendar/`. Thus you cannot commit or push your personal data by accident.

## Settings

All settings are in `config.toml`. Each line has a comment. In the sidebar, you can try different values, and the file does not change. You can also set an "as of" date to see a past day.

If your school uses a different export format, change the `[files]` section. It sets the column names and the file names. The `[classification]` section sets the difference between a snack and a meal.

## For developers

```bash
uv run pytest -q
```

The code is in `dinemaster/`:

- `ingest.py` merges the exports into one ledger.
- `metrics.py` calculates the pace.
- `forecast.py` and `budget.py` calculate the projections and the daily plan.
- `tiles.py` makes the Tiles page.

## License

MIT. See [LICENSE](LICENSE).
