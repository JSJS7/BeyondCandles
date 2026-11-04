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
  **survivorship bias**: these are companies that were large at the end of 2024. The
  bias concerns mainly 2015–2024; 2025 follows the selection date, although requiring a
  complete price history still leaves out companies that stopped trading later.
- **Prices:** daily, 2 Jan 2015 to 31 Dec 2025 (2,766 sessions per company), from
  Benjaminpo's Finance Dataset v81 (Yahoo Finance data). Open, High, Low and Close are
  as provided by the vendor. Their history is adjusted for stock splits (except two
  splits of 2026, see below) and, for some companies, also scaled by vendor factors for
  spin-offs, mergers and share conversions (see *Prices and per-share values* below). *Adjusted Close* additionally includes
  dividend conventions. A few documented corrections are applied (see the README).
- **Financials:** annual values from SEC EDGAR (XBRL company facts and original
  filings) for fiscal periods ending 1 Jan 2015 to 31 Dec 2025, in USD. Fiscal-year
  labels follow each company's own reports, so a fiscal year can end in the next
  calendar year. FY2025 of HD, LOW, TGT, ROST, LULU and KR ends in 2026 and is outside
  the range; WMT, NVDA, CRM, TJX and ADSK, which name a year by its end, also stop at a
  period ending in early 2025. The last period of 24 companies ends by 30 June 2025, so
  a cross-section by "FY2025" joins periods up to 11 months apart.
- **Company changes:** where an issuer changed through a reorganisation or merger
  (JCI, BLK, AVGO, DIS, LIN, CI), earlier periods follow the reviewed accounting
  predecessor. We never add up separate companies. Prices follow the listed share
  instead, so for JCI through 2 Sep 2016 they are the prices of Tyco International (whose
  shares became JCI shares), while the financials are those of Johnson Controls Inc.
- **External inputs:** all financial values come from SEC filings. Besides the price
  dataset and SEC, the only outside inputs are: one AMD price session (2 Jan 2015) from
  DoltHub; one FI price session (12 Nov 2025), absent from the dataset after Fiserv's
  move to Nasdaq, from Nasdaq and confirmed by Cboe; two Eaton dividends missing from the
  vendor's adjustments, from DoltHub (amounts confirmed in SEC filings); company
  names, sectors, exchanges and the market-capitalisation ranking used to choose the
  200 companies, from an archived Kaggle S&P 500 dataset
  (`andrewmvd/sp-500-stocks`, version 1022); and corporate-action evidence (company
  press releases and filings, vendor split data) behind the price adjustments and the
  EPS split restatement.

## The five metrics

Every value keeps its source filing, tag, period and filing date. If a value cannot be
tied to a reviewed definition the app shows *Needs review* instead of guessing, and
*Not available* when nothing was reported. We never fill gaps with zeros. At the time
of writing all 11,005 values (2,201 periods × 5 metrics) have a reviewed value.

### Revenue
- **Shown:** total consolidated revenue as the company reports it.
- **Why:** companies use different tags (`Revenues`, `RevenueFromContract…`,
  industry or company-specific ones). Picking the largest number or the first tag
  gives wrong totals, so each company and period is mapped to a reviewed line.
- **Not used:** taxable-equivalent, adjusted or "managed" revenue.
- **Totals read from a note:** where an old income statement shows only segments, the
  consolidated total is taken from the same filing's segment reconciliation or selected
  financial data (PCAR and DHI 2015, AXON 2021, NDAQ 2019), after checking the figure
  against the original filing.
- **Banks:** AXP, BAC, BK, C, COF, GS, JPM, MS, PNC, SCHW, TFC, USB and WFC show net
  revenue: their own net revenue line, or *net interest income + noninterest income*
  from the same filing (WFC, USB, COF and TFC, marked *Calculated*; Wells Fargo's later
  reports print the same sum as *Total revenue*). A few issuers use their own tags (e.g.
  BAC, BK, AMP).
- **One line per group:** a group of similar companies uses one line when they show it,
  and every other company shows its own revenue total. A line a company does not show
  is not calculated only to compare it with others. Differences between lines up to 5%
  are accepted rather than corrected. Nasdaq therefore shows total revenues, like ICE
  and CME, not its revenues less transaction-based expenses. Chevron, ConocoPhillips,
  Phillips 66 and Occidental show sales. ExxonMobil shows its total including equity
  income and other income in all years, a few percent above its sales line (within the
  5% tolerance). EOG's only total includes results on commodity derivatives, which can
  move it by more (about 13% in 2022). Altria's revenue includes excise taxes, while
  Philip Morris's does not.
- **Recalculated to the company's later definition:** Philip Morris reports revenue
  excluding excise taxes from its 10-K filed on 7 Feb 2019, which restated 2016 and
  2017; 2015 appears only in earlier filings that still showed revenue including them.
  Those values are *net revenues minus excise taxes on products* from the same
  statement (marked *Calculated*): 2015 in every version, and 2016 and 2017 as known
  on a date (`as_of`) before that 10-K, so the series has one definition at any date.
  We recalculate only when the company itself changed the definition and the same
  calculation reproduces the figures the company later restated (here 2016 and 2017,
  to the million). Otherwise the reported value stays: Chevron includes excise taxes
  until 2017, and ExxonMobil includes sales-based taxes in its earliest years, so their
  series change basis by a few percent.
- **Watch out:** bank, insurer and industrial "revenue" are not comparable. Avoid
  revenue-based ratios across sectors. Within one company, years come from different
  filings: after a spin-off or an accounting change, consecutive years can be on
  different bases, so growth computed from the table can be wrong in such years.
  Examples: KKR's 2015 is "Fees and Other" (1,044 mln), while from 2016 it is total
  revenues including capital allocation income (2,040 mln, so the table shows +95%);
  Chevron and ExxonMobil change basis by a few percent (see above). A marker for
  breaks above 5% is planned before ratios.

### Net income
- **Shown:** net income **attributable to the parent company's shareholders**
  (`NetIncomeLoss`), before dividends on preferred stock.
- **Why:** this is the part of earnings that belongs to the company's own owners.
  Group net income also contains the share of minority holders.
- **Exceptions:** when the standard tag is missing or ambiguous, the value is
  calculated in one filing as group net income minus the amounts attributable to other
  owners (marked *Calculated*, with the formula in *Sources*). A rounded figure tagged
  only for an executive pay table is not used; the audited statements decide (CME's 10-K
  filed in 2026).
- **Watch out:** net income is before preferred dividends, while EPS is after them, so
  net income ÷ EPS is not always the share count (e.g. BDX 2018, KMI 2017; at Wells Fargo
  in 2020 net income is 3,377 mln and EPS × diluted shares about 1,778 mln). Use EPS for
  per-share ratios and net income for company-level ratios, without mixing them.

### EPS (earnings per share)
- **Shown:** **diluted** EPS, restated for later stock splits.
- **Why diluted, not basic:** diluted EPS counts options, restricted stock and
  convertibles that could add shares, so it is never higher than basic EPS and is the
  usual figure behind valuation ratios such as P/E.
- **Exceptions:** basic EPS is used only where no diluted figure is reported
  (Berkshire Hathaway Class B, all periods) and is labelled *Basic*. Where a company has
  several share classes the reviewed class is shown (e.g. Visa Class A, Alphabet
  Class A, Berkshire Class B equivalent). Some companies tag their only diluted
  net-income row with a continuing-operations concept (no discontinued operations that
  year); that row is used after review. A continuing-operations figure never replaces a
  separately reported total.
- **Splits:** all values are restated to the share count of 13 Oct 2026, so that
  years are comparable. A split is applied only if it happened after the filing, and
  only physical changes in share count count (spin-off price factors do not). The
  originally reported value and every applied split are shown under *Sources*.
  Where a company's reports before a one-for-one exchange of shares come from a
  predecessor issuer (AVGO, BLK, CI, DIS, LIN), the splits of the successor apply to
  the predecessor's reports too.
- **Watch out:** this is GAAP EPS. Impairments and one-off gains are included, so
  single years can look extreme.

### Equity
- **Shown:** equity attributable to the parent's owners **at the end of the period**,
  including preferred equity and excluding non-controlling interests. It is a balance
  at one date, not a yearly flow.
- **Why:** it is the equity that belongs to the company's own shareholders.
- **Source:** the latest report that states the balance, which can be a quarterly (10-Q)
  report filed years later when it repeats the year-end balance (310 values); the source
  filing is shown for each value.
- **Exceptions:** often calculated as total equity minus non-controlling interests
  (marked *Calculated*). Negative equity is valid (e.g. McDonald's, TransDigm). Two small
  differences stay below the correction threshold: Lilly's 2024 and 2025 equity includes
  non-controlling interests, and Southern's 2015 and 2016 equity includes preferred stock
  of subsidiaries.
- **Watch out:** because preferred equity is included, this value is not *common* equity,
  so price-to-book and ROE of common shareholders must not be read from it directly. This
  goes beyond banks: preferred stock reached about 53% of equity at Occidental (2020), 47%
  at Public Storage (2025), 27% at Schwab and 20% at Digital Realty. The ratios planned for
  the app deduct it. Reported balance-sheet lines are not always usable: Boeing and GE show
  only par value, Morgan Stanley's line has no standard tag, and American Express shows none.
  So the deduction uses the first plausible value among the standard tags, ending with
  the liquidation preference, and rejects a value when annual preferred dividends exceed
  25% of it. Without a value it is zero only if there are no preferred dividends or they
  are at most 0.2% of equity; otherwise common-share P/B and ROE stay empty.

### Operating cash flow
- **Shown:** net cash from operating activities of the **whole company**, including
  discontinued operations.
- **Exceptions:** where a company reports only a continuing-operations total (C and GD,
  every period) that figure is shown and labelled *Continuing operations*. If a company
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
price files. The vendor restated its earlier prices for every ordinary split except two:
the 2-for-1 splits of Monster Beverage (MNST, August 2026) and Amphenol (APH, September
2026). There it halved only the last few sessions before the split, so all earlier Close
values keep the pre-split share count. The EPS split review checks the vendor's prices
around every split and finds a jump in the split's proportion only on these two
recorded days; it cannot decide ratios close to 1 (Honeywell's 2026 event), which are
checked against closing prices stated in SEC filings instead. Returns within the price
window are unaffected, as a constant factor cancels out. The vendor also treats some
spin-offs, mergers and share conversions as if they were splits and scales the whole
earlier price history by their factors. EPS does not include these
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
  the vendor ratio divided by the reviewed share-count ratio (1 for conversions), and
  divided by the split ratio before the vendor's own jump for the MNST and APH splits
  of 2026. It uses the same reviewed event list as the EPS restatement and is accurate
  to about 0.1%, because vendor ratios are rounded. JCI prices through 2 Sep 2016 have no comparable
  price and are left out. Blackstone's Close before 1 Oct 2015 is also scaled by about
  1.9% for the PJT Partners spin-off, a factor missing from the vendor's event list and
  therefore from this conversion. The app does not show such ratios yet.

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
session). That first report may come from a predecessor issuer (for example Praxair
for Linde's 2016 and 2017), or from the original XBRL of a filing that SEC's fact data
omits (S&P Global's 10-K for 2024); such reports date a value but never define today's
one, and the period shows what that report said. An earlier version is served only when
it is confirmed:

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

## What we correct

A source value is changed only when the error is large and a reliable value is known,
under one rule for all companies:

- **Adjusted Close:** the largest difference from the right value anywhere in the window,
  per company, exceeds 1%.
- **Artificial one-session jump:** over 5%.
- **Financial value:** the difference exceeds the larger of 5% of the value and 0.5% of the
  company's annual revenue; for EPS, the larger of 5% and 2 cents. For data added later,
  the revenue part for every USD value is capped at 25% of the value, so a small item
  cannot have a tolerance larger than the value itself, and share counts use 5% alone.
- **Always:** wrong company, scale, sign, period, or data used before it was published.

A reliable value comes from an SEC filing, then a company or exchange statement, then two
independent sources. A large difference without one stays and is described here. A missing
price session is filled, because all companies share one calendar, even from a single source
when a document confirms the closing price (AMD, 2 Jan 2015: the document confirms only the
close, so open, high, low and volume come from DoltHub alone). A missing financial value
stays empty and is never estimated.

Smaller differences are neither searched for nor corrected, so the data still contains
known small errors from the vendor (see *Known limitations*). The thresholds are a judgement
suited to an educational project, not a measured standard.

## How the data was checked

- Prices were compared with the source files row by row (all 553,200), and the
  calendar, OHLC consistency and large price jumps were screened. Close changes only
  for one session each of AMD and FI; Adjusted Close is corrected for AVGO (dividends
  rebuilt, including duplicates of 2018), DHR (a false cash factor), ETN (two omitted
  dividends and a wrong decimal place), COR (another company's dividends under the same
  ticker), T (dividends not rescaled with its 2022 price conversion) and OXY (the warrant
  distribution of 2020, which the vendor omits).
- Every change of the vendor's adjustment factor is compared with DoltHub's dividend
  table, and every fiscal year with the dividends per share reported to the SEC, which
  decides when the two disagree. Each remaining difference has a reviewed explanation
  in `data/dividend-audit-reviews.json`, including those left below the threshold. Limits: a few regular payments have no second
  confirmation, and differences between declaration and ex-date are accepted when a
  shifted run of payments matches the SEC total, so a small error there could pass.
  DoltHub is not fully independent of the vendor. A few ex-dates differ by up to a week
  between the two sources without being changed.
- Non-cash distributions are outside this comparison, because neither source lists them.
  The one the vendor omits, Occidental's warrants of 2020, is added at the value the
  company reports. The value of spin-offs follows the vendor's ratios. Against the first
  closing prices of the spun-off companies they differ by up to about 1.8 percentage
  points on the event day in the cases checked (largest for GE and GE HealthCare in 2023);
  not every spin-off could be checked. RTX is the exception: its first days after the
  Carrier and Otis separation of April 2020 were strongly dislocated. The company's own
  figures put the vendor's level before that date about 4–7% too low (6–7% in its 10-K
  return tables, about 4% in its proxy statements), while a calculation from the first
  closing prices lies on the other side. No single value is reliable, so the vendor's
  stays, and RTX returns across that date carry this uncertainty.
- The SEC import is faithful and complete: every stored fact matches the downloaded SEC
  data, and every fact in that data that meets the import criteria (the five metrics,
  annual and quarterly filings, periods ending 2015–2025) is stored. The database can be
  rebuilt from scratch with identical content.
- **Every served value was compared with the original SEC statements** (11,005 values):
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

Two commands repeat the essential part of these checks on the stored data:

- `check-documents` looks for every served version in the tables of its own filing.
  A value must stand in a row labelled as that metric, in the column or row of its
  period. A value the check cannot read in its own filing passes only if another
  filing shows the same number for the same period, and the few that remain are
  reviewed by hand in `data/document-check-reviews.json`. `control-documents` plants
  wrong years, values and rows to measure how many still pass. This finds a wrong
  period, value or row, not a choice between two real rows, so it does not confirm
  that a definition is the right one.
- `check-identities` tests EPS against shares and income, the cash flow statement
  against the change in cash, and the balance sheet against equity. Its tolerances
  catch gross errors only, and the balance-sheet test depends little on the served equity,
  because it also accepts the group total. Not every period can be tested, because companies do not
  tag every number, and a value in a later filing's equity statement may be compared
  with an earlier balance sheet. Explained differences are listed in
  `data/identity-reviews.json`.

They show that the numbers match the filings and the stated definitions. They are not
an audit and do not make companies comparable with each other.

## Known limitations

- Survivorship bias: the 200 companies were large at the end of 2024, and only 4 of them
  lost money over 2015–2024. Backtests on this fixed universe overstate absolute returns,
  and relative comparisons are affected too: a signal favouring cheap or beaten-down
  companies only meets those that later recovered. Compare results with an equal-weight
  basket of the same companies, and do not read them as beating the market. Only 200
  companies; US-listed, USD only.
- Backtest window: financials start with periods ending in 2015, so all 200 companies have
  a known annual value only from 7 Mar 2016, and two values, needed for growth, from
  6 Mar 2017, so a backtest starts on 31 Mar of those years. The FY2025 reports of 150 companies were filed after prices end
  (31 Dec 2025), so they cannot drive a trade in the window.
- Annual data only. The point-in-time view (`as_of`) shows an earlier version only
  when it is confirmed; one earlier version (BK revenue for 2015 as first reported)
  has no value, because that filing tags only a segment total.
- A series joins values restated in different years. Later reports recast earlier years
  after spin-offs and new accounting standards, so the change between two adjacent years
  in the table can mix definitions; a marker for these breaks is planned before ratios.
- For JCI until 2 Sep 2016 the point-in-time financials are those of Johnson Controls
  and the prices those of Tyco, so price ratios must not combine them.
- Sector differences (banks, insurers, utilities) make cross-sector ratios unreliable.
- Known vendor errors left below the correction threshold: a few dividends are missing,
  repeated or were never paid (ACN, AON, CB, HCA, JCI, MDT, NSC, TT; each company's level differs
  by under 1%); the Adient distribution is valued at the vendor's estimate; two sessions
  repeat the previous close (CB on 15 Jan 2016, S&P Global on 28 Apr 2016); and Berkshire's
  volume is 100 times too small on 16 and 17 Mar 2021 (volume is not used in calculations).
- Occidental's warrant distribution of 2020 has a value the company reports, but no filing
  states its ex-date; the session is the most likely one.
- GAAP figures without adjustments; preferred equity and dividends affect per-share
  and return ratios.
- Vendor prices and EPS are not on the same share basis for 20 companies before
  certain corporate events (see *Prices and per-share values*).
- Price data comes from a third-party vendor and its rights for public display are
  unconfirmed (see the README). Publishing this repository does not grant any right to
  redistribute the datasets.
