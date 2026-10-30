# Methodology: what the numbers mean

BeyondCandles is an educational project. Nothing here is investment advice.
This document explains which values the app shows, why they were chosen, and what to
keep in mind before using them in an analysis. It describes definitions and decisions;
the engineering details of the import are kept with the code.

## Scope

- **Companies:** a fixed set of 200 large US-listed companies taken from an archived
  S&P 500 snapshot (late December 2024), chosen by market capitalisation at that
  snapshot among companies with a complete price history, one share class per company.
  The list is frozen; it does not follow index membership over time, so it has
  **survivorship bias**: these are companies that were large at the end of the period.
- **Prices:** daily, 2 Jan 2015 to 20 Dec 2024 (2,510 sessions per company), from
  Benjaminpo's Finance Dataset v81 (Yahoo Finance data). Open, High, Low and Close are
  as provided by the vendor. Their history is adjusted for stock splits and, for some
  companies, also scaled by vendor factors for spin-offs, mergers and share conversions
  (see *Prices and per-share values* below). *Adjusted Close* additionally includes
  dividend conventions. A few documented corrections are applied (see the README).
- **Financials:** annual values from SEC EDGAR (XBRL company facts and original
  filings) for fiscal periods ending 2015–2024, in USD. Fiscal-year labels follow each
  company's own reports, so a fiscal year can end in the next calendar year. FY2024 of
  HD, LOW, TGT, ROST, LULU and KR ends in 2025 and is outside the range.
- **Company changes:** where an issuer changed through a reorganisation or merger
  (JCI, BLK, AVGO, DIS, LIN, CI), earlier periods follow the reviewed accounting
  predecessor. We never add up separate companies. Prices follow the listed share
  instead, so for JCI through 2 Sep 2016 they are the prices of Tyco International (whose
  shares became JCI shares), while the financials are those of Johnson Controls Inc.
- **External inputs:** all financial values come from SEC filings. Besides the price
  dataset and SEC, the only outside inputs are: one AMD price session (2 Jan 2015) from
  DoltHub; company names, sectors, exchanges and the market-capitalisation ranking used
  to choose the 200 companies, from an archived Kaggle S&P 500 dataset
  (`andrewmvd/sp-500-stocks`, version 1022); and corporate-action evidence (company
  press releases and filings, vendor split data) behind the price adjustments and the
  EPS split restatement.

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
- **Recalculated to the company's later definition:** Philip Morris reports revenue
  excluding excise taxes from its 10-K filed on 7 Feb 2019, which restated 2016 and
  2017; 2015 appears only in earlier filings that still showed revenue including them.
  Those values are *net revenues minus excise taxes on products* from the same
  statement (marked *Calculated*): 2015 in every version, and 2016 and 2017 as known
  on a date (`as_of`) before that 10-K, so the series has one definition at any date.
  We recalculate only when the company itself changed the definition and the same
  calculation reproduces the figures the company later restated (here 2016 and 2017,
  to the million). Otherwise the reported value stays: Chevron includes excise taxes
  until 2017, and ExxonMobil's total from 2020 includes equity-affiliate and other
  income, so their series change basis by a few percent.
- **Watch out:** bank, insurer and industrial "revenue" are not comparable. Avoid
  revenue-based ratios across sectors. Within one company, years come from different
  filings: after a spin-off or an accounting change, consecutive years can be on
  different bases, so growth computed from the table can be wrong in such years.

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
  same filing. Earlier report versions (`as_of`) follow the same rules: C and GD
  versions keep the *Continuing operations* label, and another filing's continuing
  subtotal counts as the whole-company figure only where that filing shows no
  discontinued cash flow for the year (an explicit dash) or no discontinued operations.
- **Watch out:** for banks and asset managers operating cash flow is dominated by
  client and balance-sheet movements and says little about earnings quality. Free cash
  flow cannot be computed yet (capital expenditure is not included).

## Prices and per-share values

EPS is restated for later stock splits up to 13 Oct 2026, the last date in the vendor's
price files, so ordinary splits affect Close and EPS in the same way. The vendor,
however, treats some spin-offs, mergers and share conversions as if they were splits and
scales the whole earlier price history by their factors. EPS does not include these
factors, because the number of shares did not change in that way.

- **Affected companies:** APD, BDX, CMCSA, DHR, FDX, GE, HLT, HON, IBM, JCI, MET, MMM,
  MRK, O, OXY, PFE, RTX, SPGI, T and TT. Before the event, Close divided by EPS is not
  the P/E investors saw at the time; the difference ranges from negligible (OXY) to
  about half (HON). For JCI through 2 Sep 2016 the price belongs to another company
  (see *Company changes*).
- **Returns:** use Adjusted Close. Its spin-off and dividend conventions keep the series
  free of artificial drops on event days.
- **Valuation ratios** (P/E and any later per-share ratio): use the price converted to
  the share basis of EPS: Close multiplied, for every vendor event after that day, by
  the vendor ratio divided by the reviewed share-count ratio (1 for conversions). It uses
  the same reviewed event list as the EPS restatement and is accurate to about 0.1%,
  because vendor ratios are rounded. JCI prices through 2 Sep 2016 have no comparable
  price and are left out. The app does not show such ratios yet.

## Versions of reports and time

The app shows the **latest filed version** of every value. Companies restate earlier
years (new accounting standards, divestitures, corrected errors), so a value may differ
from what was known when the year was first reported. First publication of an annual
figure is typically about seven weeks after the period ends. Financials are annual
only; there are no quarterly or trailing values.

The API can also return the table **as known on a date D** (`as_of`), for analyses of
the past. A year appears from the filing date of its first annual report, and each
value is the latest version filed on or before D (the SEC filing date counts as known
on that day; filings carry no time of day, so a backtest should act on the next
session). An earlier version is served only when it is confirmed:

- **identical:** every source of today's verified value has the same value in the same
  concept, unit, issuer and period in that earlier filing;
- **rounding:** a single reported amount differs only by coarser rounding (e.g. reported
  in thousands, later in millions); two values of equal precision must be equal;
- **split:** EPS differs only by a later stock split (and cent rounding);
- **reviewed:** the earlier annual report was checked against its own face statement
  with the same rules as the latest one, as if its filing date were today. The value
  must appear in that filing's annual column (within displayed rounding) and match the
  filing's tagged facts; a value read from the table itself (e.g. noncontrolling
  interests deducted from group equity), an EPS figure or a company-specific revenue
  total read from the filing's original XBRL when it was not among the imported facts,
  or a net-income figure taken from the same filing's SEC data when the import kept
  another concept (the noncontrolling share deducted from group net income, or a net
  income row tagged with the common-shareholder or, after review, a
  continuing-operations concept in a year without discontinued operations), is pinned
  to that source. Berkshire Hathaway's 10-Ks for 2015 and 2016 tag EPS only per Class A
  share; for Class B the amount stated in the statement's footnote is used, after
  checking that it equals Class A EPS / 1,500 rounded to cents (later filings tag the
  same figure). Such a version may differ from today's value: it is what the company
  reported at the time.

Any other earlier annual-report version is marked *needs review* and has no value: for
example, the statement could not be matched unambiguously, a fact needed to explain
it (such as the noncontrolling interests in equity) is missing, or the filing's only
tagged figure has another definition (e.g. a segment total instead of GAAP revenue). A quarterly (10-Q) version that is not confirmed is ignored, so the
previous annual version stays in force until the next annual report. EPS keeps the same
share basis (13 Oct 2026) as the latest view. With D = today the result is identical to
the latest view. Part of the history therefore remains unavailable rather than possibly
wrong.

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
- **Every earlier version served by `as_of`** (identical, rounding, split and reviewed)
  was compared with the primary document of its own filing, not with the XBRL data
  used by the import: most automatically (the value in the row of the metric and the
  column of its period, EPS before split restatement, calculations by component), the
  rest by hand, with no discrepancies found. A few amendments (10-K/A) that only
  re-filed XBRL or corrected the cover page repeat the value of the original report
  filed days earlier.

These checks were run once, on the snapshot used by the app (October 2026), with
throwaway scripts that are not part of the repository. They show that the numbers
match the filings and the stated definitions. They are not an audit and do not make
companies comparable with each other.

## Known limitations

- Survivorship bias: the 200 companies were large at the end of the period, so
  backtests on this fixed universe overstate absolute returns and are best used for
  relative comparisons. Only 200 companies; US-listed, USD only.
- Annual data only. The point-in-time view (`as_of`) shows an earlier version only
  when it is confirmed; one earlier version (BK revenue for 2015 as first reported)
  has no value, because that filing tags only a segment total.
- Sector differences (banks, insurers, utilities) make cross-sector ratios unreliable.
- GAAP figures without adjustments; preferred equity and dividends affect per-share
  and return ratios.
- Vendor prices and EPS are not on the same share basis for 20 companies before
  certain corporate events (see *Prices and per-share values*).
- Price data comes from a third-party vendor and its rights for public display are
  unconfirmed (see the README). Publishing this repository does not grant any right to
  redistribute the datasets.
