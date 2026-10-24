# BeyondCandles

An educational stock analysis app with company search, interactive price charts,
and annual financial statements from SEC EDGAR.

- **Coverage:** 200 companies, 502,000 daily prices, 2 Jan 2015 to 20 Dec 2024.
- **Financials:** revenue, net income, EPS, equity, and operating cash flow.
- **Interface:** responsive layout, light and dark themes, preset chart periods,
  and selection of a custom date range.
- **Stack:** Angular, TypeScript, Chart.js, FastAPI, SQLAlchemy, Alembic,
  and PostgreSQL.

## Run locally

Requires Python 3.13, Node.js 24 (24.15+), and Docker with Compose.
Commands below use Windows PowerShell and start from the repository root.

Create `.env` from `.env.example` if it does not exist, then set your database
credentials. Keep an existing `.env` and database volume.

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env -ErrorAction Stop
}
docker compose up -d postgres
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

In a second terminal, from the repository root:

```powershell
cd frontend
npm.cmd ci
npm.cmd start
```

App: <http://localhost:4200> | API docs: <http://127.0.0.1:8000/docs>.
On macOS/Linux, use `.venv/bin/python` and `npm` instead.

**Data is not bundled.** Migrations create the tables; the app needs imported
prices and SEC facts to show companies. The pipeline downloads, validates,
and imports the pinned sources. From `backend`, list its commands with:

```powershell
.\.venv\Scripts\python.exe -m app.data_pipeline --help
```

SEC downloads require `SEC_USER_AGENT` with an application name and your contact
email in `.env`. Reproducing the reviewed snapshot also requires the archived
correction evidence, including `data/raw/benjaminpo-v81/evidence/`; downloading
the price CSVs alone is insufficient. Raw data, caches, backups, and generated
files under `data/raw/` and `data/processed/` stay outside Git.

## Data sources and corrections

Prices come from [Benjaminpo's Finance Dataset, v81](https://www.kaggle.com/datasets/benjaminpo/finance-dataset/versions/81),
which uses Yahoo Finance data. Company metadata was retained from Andrew v1022;
its prices are no longer used. The fixed selection does not reconstruct historical
S&P 500 membership. [The manifest](data/manifest.json) records source files,
SHA-256 checksums, and correction evidence; [the company list](data/companies-200.json)
is versioned separately.

Explicit price corrections retained in the pipeline:

- **AVGO:** 39 verified dividend adjustments, excluding duplicate 2018 payments.
- **DHR/Fortive:** retain the provider's 1319:1000 price conversion and remove
  an additional USD 24.56 adjustment.
- **JCI/Adient:** value the distribution at USD 4.551 per JCI share, using Adient's
  first closing price and the 1:10 distribution ratio.
- **AMD, 2 Jan 2015:** fill one missing session from a pinned DoltHub revision,
  with the closing price also checked against a Scotiabank document.

Financials come from [SEC EDGAR](https://www.sec.gov/edgar).
Versioned rules in `data/` select reported values and preserve their sources.
EPS is normalized for splits to a common share basis dated 13 Oct 2026;
basic EPS and cash flow from continuing operations remain explicitly labeled.

**Limitations:** annual results use the latest imported report versions, so they
are not yet suitable for point-in-time backtests. Adjusted Close includes dividend
and spin-off conventions and must not be used directly for P/E. Price corrections
do not certify every corporate action in the dataset.

The Kaggle publisher declares GPL 3; the AMD supplement records DoltHub's
CC BY-SA 4.0 declaration. Rights to publicly display the underlying Yahoo data
remain unconfirmed. Publishing this code does not establish permission to
redistribute the datasets or display their data publicly.

## Checks

From `backend`, after installing `requirements-dev.txt`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Set `BC_RUN_DB_TESTS=1` to include integration tests on separate temporary databases.
From `frontend`:

```powershell
npm.cmd run format:check
npm.cmd run typecheck
npm.cmd test -- --watch=false
npm.cmd run build
```
