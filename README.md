# BeyondCandles

An educational stock analysis app with company search, interactive price charts,
and annual financial statements from SEC EDGAR.

- **Coverage:** 200 companies, 553,200 daily prices, 2 Jan 2015 to 31 Dec 2025.
- **Financials:** revenue, net income, EPS, equity, and operating cash flow for
  fiscal years ending 1 Jan 2015 to 31 Dec 2025.
- **Interface:** responsive layout, light and dark themes, preset chart periods,
  and selection of a custom date range.
- **Stack:** Angular, TypeScript, Chart.js, FastAPI, SQLAlchemy, Alembic,
  and PostgreSQL.

## Run locally

Requires Python 3.13, Node.js 24 (24.15+), and Docker Compose. Configure `.env`
using `.env.example`; keep any existing credentials and database volume.
The commands below assume dependencies are installed and the database is migrated
and populated. Run each service in a separate terminal (Windows PowerShell):

```powershell
# From the repository root
docker compose up -d postgres
```

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

```powershell
cd frontend
npm.cmd start
```

App: <http://localhost:4200> | API docs: <http://127.0.0.1:8000/docs>.
On macOS/Linux, use `.venv/bin/python` and `npm` instead.

Data must be downloaded and imported separately. For pipeline commands, run
`.\.venv\Scripts\python.exe -m app.data_pipeline --help` from `backend`.
SEC downloads require `SEC_USER_AGENT` with your contact email in `.env`.
Reproducing the reviewed snapshot also requires archived correction evidence,
including `data/raw/benjaminpo-v81/evidence/`. Raw data, caches, backups, and
generated files under `data/raw/` and `data/processed/` stay outside Git.

## Data sources and corrections

Prices come from [Benjaminpo's Finance Dataset, v81](https://www.kaggle.com/datasets/benjaminpo/finance-dataset/versions/81),
which uses Yahoo Finance data. Company metadata was retained from Andrew v1022;
its prices are no longer used. The fixed selection does not reconstruct historical
S&P 500 membership. [The manifest](data/manifest.json) records source files,
SHA-256 checksums, and correction evidence; [the company list](data/companies-200.json)
is versioned separately.

Explicit price corrections retained in the pipeline:

- **AVGO:** 44 verified dividend adjustments, excluding duplicate 2018 payments.
- **DHR/Fortive:** retain the provider's 1319:1000 price conversion and remove
  an additional USD 24.56 adjustment.
- **JCI/Adient:** value the distribution at USD 4.551 per JCI share, using Adient's
  first closing price and the 1:10 distribution ratio.
- **Dividends missing from the provider's adjustments:** add 10 regular dividends
  (ETN and MDT in 2018; ACN, AON, CB, JCI and TT in 2025) from a pinned DoltHub
  revision, with amounts confirmed in SEC filings.
- **Dividends the provider applied wrongly:** remove CoreSite's 12 dividends from COR
  (same ticker, another company), rescale 29 T dividends by the provider's 1.324
  conversion of 2022, change ETN 13 May 2020 from 0.073 to 0.73, and remove duplicates
  or events ruled out by filings (AON 2018, NSC 2015, HCA 2020, JCI 2016).
- **OXY, 3 Aug 2020:** add the warrant distribution the provider omits, at USD 0.61875
  per share (1/8 warrant at the USD 4.95 the company reports).
- **AMD, 2 Jan 2015:** fill one missing session from a pinned DoltHub revision,
  with the closing price also checked against a Scotiabank document.
- **FI, 12 Nov 2025:** insert one session absent from the provider's file, the day
  after Fiserv moved from the NYSE (FI) to Nasdaq (FISV); prices and volume from a
  pinned Nasdaq response, with the same values in Cboe's published history.

Financials come from [SEC EDGAR](https://www.sec.gov/edgar).
Versioned rules in `data/` select reported values and preserve their sources.
EPS is normalized for splits to a common share basis dated 13 Oct 2026;
basic EPS and cash flow from continuing operations remain explicitly labeled.

**Limitations:** the app shows annual results from the latest report versions. The
API can also return them as known on a given date (`as_of`), but the interface has no
such view yet, and the data stays annual and limited to a fixed list of today's
companies (see [METHODOLOGY.md](METHODOLOGY.md)). Neither Close nor Adjusted Close
can be divided directly by EPS: the vendor also scales prices for some spin-offs,
mergers and conversions, and Adjusted Close adds dividend conventions (see
[METHODOLOGY.md](METHODOLOGY.md)). Price corrections do not certify every corporate
action in the dataset.

## What the numbers mean

The app shows five annual metrics per company. [METHODOLOGY.md](METHODOLOGY.md) explains
each choice, the exceptions and how the data was checked. In short:

- **Revenue:** total consolidated revenue as reported; banks use net interest income
  plus noninterest income.
- **Net income:** attributable to the parent's shareholders, before preferred dividends.
- **EPS:** diluted (basic only where nothing else is reported, and labelled), restated
  to a common share count so years are comparable.
- **Equity:** the parent's equity at period end, including preferred and excluding
  non-controlling interests.
- **Operating cash flow:** whole-company total; continuing operations only where
  that is all a company reports, and labelled.

Values come from the latest filed report versions, are annual only, and are not
comparable across sectors without care.

The Kaggle publisher declares GPL 3. The AMD session and the ten added dividends
come from DoltHub `post-no-preference/stocks`, declared as CC BY-SA 4.0. The FI supplement uses one session published by Nasdaq
and Cboe, whose terms of use were not reviewed for redistribution. Rights to publicly display the underlying Yahoo data
remain unconfirmed. Publishing this code does not establish permission to
redistribute the datasets or display their data publicly.
