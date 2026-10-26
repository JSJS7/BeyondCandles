# Methodology: what the numbers mean

BeyondCandles is an educational project. Nothing here is investment advice.
This document explains which values the app shows, why they were chosen, and what to
keep in mind before using them in an analysis. It describes definitions and decisions;
the engineering details of the import are kept with the code.

## Scope

- **Companies:** a fixed set of 200 large US-listed companies taken from an archived
  S&P 500 snapshot, chosen by market capitalisation among companies with a complete
  price history, one share class per company. The list is frozen; it does not follow
  index membership over time, so it has **survivorship bias**.
- **Prices:** daily, 2 Jan 2015 to 20 Dec 2024 (2,510 sessions per company), from
  Benjaminpo's Finance Dataset v81 (Yahoo Finance data). Open, High, Low and Close are
  as provided by the vendor, whose history is adjusted for splits. *Adjusted Close*
  also includes dividend and spin-off conventions. It must not be used to compute P/E.
  A few documented corrections are applied (see the README).
- **Financials:** annual values from SEC EDGAR (XBRL company facts and original
  filings) for fiscal periods ending 2015–2024, in USD. Fiscal-year labels follow each
  company's own reports, so a fiscal year can end in the next calendar year. FY2024 of
  HD, LOW, TGT, ROST, LULU and KR ends in 2025 and is outside the range.
- **Company changes:** where an issuer changed through a reorganisation or merger
  (JCI, BLK, AVGO, DIS, LIN, CI), earlier periods follow the reviewed accounting
  predecessor. We never add up separate companies.

## The five metrics

Every value keeps its source filing, tag, period and filing date. If a value cannot be
tied to a reviewed definition the app shows *Needs review* instead of guessing, and
*Not available* when nothing was reported. We never fill gaps with zeros. At the time
of writing all 10,005 values (2,001 periods × 5 metrics) have a reviewed value.

### Revenue
- **Shown:** total consolidated revenue as the company reports it.
- **Why:** companies use different tags (`Revenues`, `RevenueFromContract…`,
  industry or company-specific ones). Picking the largest number or the first tag
  gives wrong totals, so each company and period is mapped to a reviewed line.
- **Not used:** taxable-equivalent, adjusted or "managed" revenue.
- **Exceptions:** for WFC, USB, COF and TFC revenue is *net interest income +
  noninterest income* from the same filing (marked *Calculated*). A few issuers use
  their own tags (e.g. BAC, BK, XOM, AMP) or a company-specific revenue line (Nasdaq).
- **Watch out:** bank, insurer and industrial "revenue" are not comparable. Avoid
  revenue-based ratios across sectors.

### Net income
- **Shown:** net income **attributable to the parent company's shareholders**
  (`NetIncomeLoss`), before dividends on preferred stock.
- **Why:** this is the part of earnings that belongs to the company's own owners.
  Group net income also contains the share of minority holders.
- **Exceptions:** when the standard tag is missing or ambiguous, the value is
  calculated in one filing as group net income minus the amounts attributable to other
  owners (marked *Calculated*, with the formula in *Sources*).
- **Watch out:** net income is before preferred dividends, while EPS is after them, so
  net income ÷ EPS is not always the share count (e.g. BDX 2018, KMI 2017).

### EPS (earnings per share)
- **Shown:** **diluted** EPS, restated for later stock splits.
- **Why diluted, not basic:** diluted EPS counts options, restricted stock and
  convertibles that could add shares, so it is never higher than basic EPS and is the
  usual figure behind valuation ratios such as P/E.
- **Exceptions:** basic EPS is used only where no diluted figure is reported
  (Berkshire Hathaway Class B, 10 periods) and is labelled *Basic*. Where a company has
  several share classes the reviewed class is shown (e.g. Visa Class A, Alphabet
  Class A, Berkshire Class B equivalent). Some companies tag their only diluted
  net-income row with a continuing-operations concept (no discontinued operations that
  year); that row is used after review. A continuing-operations figure never replaces a
  separately reported total.
- **Splits:** all values are restated to the share count of 13 Oct 2026, so that
  years are comparable. A split is applied only if it happened after the filing, and
  only physical changes in share count count (spin-off price factors do not). The
  originally reported value and every applied split are shown under *Sources*.
- **Watch out:** this is GAAP EPS. Impairments and one-off gains are included, so
  single years can look extreme.

### Equity
- **Shown:** equity attributable to the parent's owners **at the end of the period**,
  including preferred equity and excluding non-controlling interests. It is a balance
  at one date, not a yearly flow.
- **Why:** it is the equity that belongs to the company's own shareholders.
- **Exceptions:** often calculated as total equity minus non-controlling interests
  (marked *Calculated*). Negative equity is valid (e.g. McDonald's, TransDigm).
- **Watch out:** because preferred equity is included, ROE and price-to-book for
  companies with large preferred stock (banks) are not based on *common* equity.

### Operating cash flow
- **Shown:** net cash from operating activities of the **whole company**, including
  discontinued operations.
- **Exceptions:** where a company reports only a continuing-operations total (C and GD,
  20 periods) that figure is shown and labelled *Continuing operations*. If a company
  reports continuing and discontinued parts separately, they are added up from the
  same filing.
- **Watch out:** for banks and asset managers operating cash flow is dominated by
  client and balance-sheet movements and says little about earnings quality. Free cash
  flow cannot be computed yet (capital expenditure is not included).

## Versions of reports and time

The app shows the **latest filed version** of every value. Companies restate earlier
years (new accounting standards, divestitures, corrected errors), so a value may differ
from what was known when the year was first reported. All versions are stored, but the
data is **not point-in-time**: it is suitable for looking at companies today, not yet for
backtests. First publication of an annual figure is typically about seven weeks after
the period ends. Financials are annual only; there are no quarterly or trailing values.

## How the data was checked

- Prices were compared with the source files row by row (all 502,000), and the
  calendar, OHLC consistency and large price jumps were screened. Corrections apply
  only to AVGO, DHR, JCI and one AMD session.
- The SEC import is faithful and complete: every stored fact matches the downloaded SEC
  data, and every fact in that data that meets the import criteria (the five metrics,
  annual and quarterly filings, periods ending 2015–2024) is stored. The database can be
  rebuilt from scratch with identical content.
- **Every served value was compared with the original SEC statements** (10,005 values):
  most automatically, the rest by hand, with no discrepancies found. Calculated values
  are arithmetically exact.
- Cross-checks: EPS against weighted-average share counts from the same filings, equity
  and cash flow against their standard tags, and split completeness across issuers.

These checks were run once, on the snapshot used by the app (October 2026), with
throwaway scripts that are not part of the repository. They show that the numbers
match the filings and the stated definitions. They are not an audit and do not make
companies comparable with each other.

## Known limitations

- Survivorship bias; only 200 companies; US-listed, USD only.
- Annual data only; latest-version values; no point-in-time view yet.
- Sector differences (banks, insurers, utilities) make cross-sector ratios unreliable.
- GAAP figures without adjustments; preferred equity and dividends affect per-share
  and return ratios.
- Price data comes from a third-party vendor and its rights for public display are
  unconfirmed (see the README). Publishing this repository does not grant any right to
  redistribute the datasets.
