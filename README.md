# BeyondCandles

An educational, non-commercial app for the historical analysis of 200 large US-listed companies:
daily prices and annual results from SEC filings, a screener of valuation, profitability and growth
ratios, and an annual backtest of those ratios. The data is frozen at 31 Dec 2025. Nothing here is
investment advice.

## What it does

- **Company page:** search by name or ticker; an Adjusted Close chart with preset periods and a
  custom range; annual financials (revenue, net income, EPS, equity, operating cash flow) with the
  SEC sources of every value; 12 ratios for every fiscal year at the year-end price.
- **Screener:** the same 12 ratios for all companies as known on a chosen date from 31 Mar 2016,
  with a sector filter, min–max criteria, search, column choice, sorting and CSV export.
- **Backtest:** each year the companies are ranked by one ratio and split into five groups held for
  a year, compared with benchmarks, before and after costs, with a test of the ranking, risk
  figures, random portfolios and the companies in each group.
- Light and dark themes and a phone layout.

[METHODOLOGY.md](METHODOLOGY.md) explains what every number means, how it was chosen and checked,
its limits and the companies handled differently.

## Data

- **Companies:** a fixed list of 200 from an archived S&P 500 snapshot of late December 2024
  ([the list](data/companies-200.json)); index membership is not reconstructed.
- **Prices:** 553,200 daily prices, 2 Jan 2015 to 31 Dec 2025, from
  [Benjaminpo's Finance Dataset, v81](https://www.kaggle.com/datasets/benjaminpo/finance-dataset/versions/81),
  which uses Yahoo Finance data. Company names, exchanges and sectors come from
  [Andrew's S&P 500 dataset, v1022](https://www.kaggle.com/datasets/andrewmvd/sp-500-stocks/versions/1022).
- **Financials:** annual values for fiscal periods ending 1 Jan 2015 to 31 Dec 2025 from
  [SEC EDGAR](https://www.sec.gov/edgar), in USD.
- **In Git:** [the manifest](data/manifest.json) with source files and SHA-256 checksums, and the
  rules in `data/` that select reported values and keep their sources and evidence. Raw data,
  caches, backups and generated files under `data/raw/` and `data/processed/` stay outside Git.

## Data rights

The Kaggle publisher declares GPL 3. A few price corrections use a pinned DoltHub revision of
`post-no-preference/stocks`, declared as CC BY-SA 4.0, and one session published by Nasdaq and Cboe,
whose terms were not reviewed for redistribution. Rights to publicly display the underlying Yahoo
data remain unconfirmed, and publishing this code does not establish permission to redistribute the
datasets. Prices appear on the charts, and the screener's CSV export leaves them out. Price data
will be removed at a rights holder's request.

## Run locally

Requires Python 3.13, Node.js 24 (24.15+) and Docker Compose. The commands are for Windows
PowerShell; on macOS and Linux use `.venv/bin/python` and `npm`.

1. Copy `.env.example` to `.env` and set a local database password. `SEC_USER_AGENT` with your
   contact address is needed only to download SEC data.
2. Start the database from the repository root: `docker compose up -d postgres`.
3. Install the backend and create the tables, from `backend`:

   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   .\.venv\Scripts\python.exe -m alembic upgrade head
   ```

4. Install the frontend, from `frontend`: `npm.cmd ci`.
5. Load the data (next section).
6. Run the API (from `backend`) and the app (from `frontend`) in separate terminals:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

   ```powershell
   npm.cmd start
   ```

   App: <http://localhost:4200> | API docs: <http://127.0.0.1:8000/docs>.

   To open the app on a phone in the same network, run `npm.cmd run start:phone` instead of
   `npm.cmd start` and open the Network address it prints (port 4310). The dev server forwards
   `/api` to the API, which stays on 127.0.0.1; Windows may ask once to let Node.js through its
   firewall.

## Rebuild the data

From `backend`, on an empty database after `alembic upgrade head`:

```powershell
.\.venv\Scripts\python.exe -m app.data_pipeline fetch-prices   # pinned vendor files, checked by SHA-256
.\.venv\Scripts\python.exe -m app.data_pipeline prepare-prices # applies the reviewed price corrections
.\.venv\Scripts\python.exe -m app.data_pipeline import-prices
.\.venv\Scripts\python.exe -m app.data_pipeline fetch-sec      # needs SEC_USER_AGENT
.\.venv\Scripts\python.exe -m app.data_pipeline import-sec     # financials and their history by filing date
.\.venv\Scripts\python.exe -m app.data_pipeline verify-db
```

`--help` lists every command, including the checks against the filings and the `--check` mode
that compares the rules a review would write with those in `data/`. The exact reviewed snapshot
also needs the raw-data archive, which is not public: the price corrections need their evidence in
`data/raw/benjaminpo-v81/evidence/`, and a new SEC download includes filings made after the
snapshot, so it is not guaranteed to give the same values.

## Tests

- Backend, from `backend`: `.\.venv\Scripts\python.exe -m unittest discover tests`. Database tests
  run only with `$env:BC_RUN_DB_TESTS = '1'` and a running PostgreSQL; they create and drop their
  own database.
- Frontend, from `frontend`: `npm.cmd test -- --watch=false`, `npm.cmd run typecheck` and
  `npm.cmd run format:check`.
- GitHub Actions runs all of them on every push to `main`.

## Stack

Angular, TypeScript and Chart.js; Python, FastAPI, SQLAlchemy and Alembic; PostgreSQL in Docker
Compose.
