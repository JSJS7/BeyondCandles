# Methodology: what the numbers mean

BeyondCandles is an educational project. Nothing here is investment advice. This page explains
what each number means, how it was chosen and what to keep in mind before using it. Every value
in the app keeps its source filing, tag, period and filing date. The rules apply to all companies;
companies with a rule of their own or a known issue are listed under
[Companies handled differently](#companies-handled-differently).

## In short

- **Companies:** a fixed list of 200 large US-listed companies from an archived S&P 500 snapshot
  of late December 2024. The list does not follow index membership.
- **Prices:** daily, 2 Jan 2015 to 31 Dec 2025, from Benjaminpo's Finance Dataset v81 (Yahoo
  Finance data).
- **Financials:** annual values from SEC EDGAR for fiscal periods ending 1 Jan 2015 to 31 Dec
  2025, in USD. Fiscal-year labels follow each company's reports, so a year can end in the next
  calendar year, and some companies' FY2025 is outside the range.
- **Gaps:** a missing value stays empty. We never fill it with zero, an average, an earlier year
  or an estimate.
- **Comparability:** definitions differ between industries. Compare companies with care.

Other outside inputs and the price corrections are listed in the [README](README.md).

## Measures

| Measure | What it is | How it is chosen |
|---|---|---|
| Revenue | Total consolidated revenue as reported; net revenue for banks and exchanges | Reviewed |
| Net income | Attributable to the parent's shareholders, before preferred dividends | Reviewed |
| EPS | Diluted, restated for later stock splits | Reviewed |
| Equity | Attributable to the parent's owners at period end, including preferred equity | Reviewed |
| Operating cash flow | Net cash from operating activities of the whole company | Reviewed |
| Share count | Weighted average diluted shares, on the same split basis as EPS | Automatic |
| Shares outstanding | The count on the annual report's cover, on the same split basis as EPS | Automatic |
| Assets | Total assets at period end | Automatic |
| Capital expenditure | Purchase of property, plant and equipment, then a wider productive-assets line | Automatic |
| Operating income | The `OperatingIncomeLoss` tag | Automatic |
| Preferred stock | First plausible value among the standard tags, ending with liquidation preference | Automatic |
| Preferred dividends | First value among the standard tags | Automatic |
| Discontinued operations | Their part of net income in the newest report, the larger of the parent's share and the whole amount; only labels ratios | Automatic |

**Reviewed** values were each compared with the company's original filing. **Automatic** values
come from a fixed tag order and are screened in bulk, so they are less certain. The app labels
values that need care: *Calculated* (from items of one filing, such as a share count from net income
and EPS), *Basic* (no diluted EPS is reported), *Continuing ops* (no whole-company cash flow is
reported), *Split-adjusted*, *Series break* (see Growth marker below) and, in the screener,
*Spin-off* and *Discontinued ops* (see Known limitations) and *Exchangeable units* (see Ratios).

### Reviewed measures

- **Revenue:** picking the largest number or the first tag gives wrong totals, so each company and
  period is mapped to a reviewed line. Banks and exchanges use net revenue, as data services do, and
  fuel companies their sales line. Differences between similar lines up to 5% are accepted. A line a
  company does not show is not calculated to resemble others, and we do not analyse what a company
  puts in its line. Bank and industrial revenue are not comparable.
- **A changed definition:** we recalculate only when a company itself changed its definition and
  the same calculation reproduces its later restated figures. Otherwise the reported value stays,
  even if the series changes basis.
- **Net income and EPS:** net income is before preferred dividends and EPS is after them, so net
  income divided by EPS is not always the share count.
- **EPS:** diluted, or basic where a company reports no diluted figure. Only real changes in share
  count restate it, up to 13 Oct 2026, the last date in the vendor's files. It is GAAP, so one-off
  items are included, also the result of discontinued operations (see Known limitations).
- **Equity:** because it includes preferred equity, it is not common equity. Ratios deduct
  preferred stock (see below). Negative equity is valid.
- **Operating cash flow:** where a company reports only continuing operations, that value is shown
  and labelled. For banks and asset managers it is dominated by balance-sheet movements and says
  little about earnings quality.

### Automatic measures

- **Selection:** a backup tag counts only while the main one is unknown. A value counts from the
  first report that contains it, and joins an annual period of the reviewed measures.
- **Share count:** diluted, then a combined basic and diluted row, then basic where nothing else is
  reported. Counts by share class are not summed, so some companies have none. A count far below the
  company's largest is dropped as a scale error of the tag. Where the first report's table shows the
  same digits in a stated unit, the unit is corrected.
- **Shares outstanding:** the count on the cover of the annual report, on a day shortly before the
  filing, as a value of the year the report is for. A cover tagged with several days counts with its
  latest day up to the filing. Companies with several share classes often tag it only by class, so
  they have none.
- **Capital expenditure:** banks and most real estate companies have none. A line tagged as "other"
  property or productive assets counts only when the same report shows no separate line for a kind of
  asset, so a part is never taken for the whole. Two lines without a total (additions to oil and gas
  properties and to other equipment) are added up. Where a tag holds only part of the cash spent, the
  value comes from the cash flow statement. Companies that report capital expenditure only under their
  own tags stay empty.
- **Operating income:** many companies have no such row, including banks. It stays empty where the
  tag holds a sum of segments or an adjusted measure. That list comes from a screen of row labels, so
  a sum of segments under a plain label can remain.

## Ratios

The app's Screener page shows them, from the `/screener` API; company pages do not show them yet. The
API returns plain ratios (0.25 means 25%), rounded to six decimals. A ratio is empty when an input is
missing or the result has no meaning, never zero. Price ratios are also empty without a comparable price.

| Ratio | Formula | Also empty when |
|---|---|---|
| P/E | Price ÷ EPS | EPS not positive |
| P/B | Price × listed shares ÷ common equity | Common equity not positive |
| P/S | Price × (shares + exchangeable units) ÷ revenue | Revenue not positive |
| ROE | (Net income − preferred dividends) ÷ average common equity | Either year's common equity not positive |
| ROA | Net income ÷ average assets | |
| Net margin | Net income ÷ revenue | Revenue not positive |
| Operating margin | Operating income ÷ revenue | Revenue not positive |
| Free cash flow margin | (Operating cash flow − capital expenditure) ÷ revenue; with *Continuing ops* the cash flow and revenue cover continuing operations, capital expenditure as reported | Revenue not positive |
| Operating cash flow ÷ net income | As named | Net income not positive, or continuing-only cash flow |
| Equity ÷ assets | Reported equity ÷ assets; not a measure of debt | |
| Revenue growth, EPS growth | Later ÷ earlier − 1, both years from one report | No report states both years, or a value is not positive |

- **Valuation date:** from 31 Mar 2016 to 31 Dec 2025 (default). Each company is valued at that day's
  close, or the last close within 7 days before it, with its latest annual report filed before that
  session. A filing date has no time of day and most annual reports arrive after the market closes, so
  a report filed on the session's own day counts only from the next session.
- **Values as annual reports state them:** a quarterly report can restate the previous year's balance
  sheet months before the next annual report restates the income statement. Ratios therefore keep each
  value as the latest annual report states it, so one ratio never joins a restated balance sheet with an
  income statement not yet restated. A value known only from a quarterly report is used until an annual
  report states it.
- **Earlier year:** the period that ended 350 to 380 days before; fiscal-year labels are not compared.
  Averages use the two year-end balances.
- **Share count:** P/B and P/S value the shares with the shares outstanding on the latest annual
  report's cover, as analysis sites count shares outstanding rather than the year's average. The
  weighted average of the year stands in where the cover has no total, where the cover is below half or
  above 1.75 times the average (a likely error of the cover), and in periods with exchangeable units. Without a tagged
  average, the count is (net income − preferred dividends) ÷ EPS, marked as calculated. A cover count is
  a few months old by the valuation date and misses later buybacks and issues; the average lags far
  more after an acquisition.
- **Units exchangeable into shares:** in some companies part of the owners hold units of a subsidiary
  partnership exchangeable one for one into the listed shares. Revenue belongs to the whole group, so
  P/S counts the market value of shares and units together, as data services do; equity and net income
  belong to the listed shares, so P/B counts only those and P/E is unchanged. Unit counts are the
  weighted averages each annual report states, for companies whose units exceed 5% of the share count;
  the ratios are marked *Exchangeable units*. Ordinary minority stakes in subsidiaries follow the usual
  convention of market value over consolidated revenue.
- **Common equity:** equity minus preferred stock, as analysis sites deduct it. The value comes from the
  preferred stock tags, or else from the preferred stock column of the statement of changes in equity
  in the period's first annual report. A zero balance is not a value. When annual preferred dividends
  exceed 25% of a value, it may be only par value or a scale error, and it counts only if it is at least
  0.5% of equity. Without a usable value, the deduction is zero only if preferred dividends are absent or
  at most 0.2% of equity; otherwise common-share ratios stay empty. Where the period's first annual
  report counts no preferred shares of the company at the period end, the stock was converted or
  redeemed during the year, or belongs only to a subsidiary outside the parent's equity: nothing is
  deducted from equity, while the year's preferred dividends are still deducted from net income.
  Companies whose diluted
  count already holds preferred stock as converted do not deduct it. Preferred stock of subsidiaries is
  not deducted either, and neither are its dividends: the parent's net income is already after them and
  its common equity excludes that stock.
- **Growth marker:** when the table's series implies a change more than 5% away from the report's own,
  or a value changes sign between the sources, the row is marked as a break in the series. The company
  page uses the same rule for all five reviewed measures and shows the change as one report states it;
  at a break it shows both years as the table has them and as that report states them.
  Equity is read from the two year-end balances of one report. A value built from two positions under
  one tag (parent equity as group equity minus a noncontrolling part) cannot be rebuilt from a report's
  columns, so it has no growth and no marker.

## Prices

- **Returns use Adjusted Close.** It includes dividends and keeps the series free of artificial drops
  on event days. Its levels differ from the prices quoted on each day.
- **Valuation ratios use Close converted to the share basis of EPS.** The vendor scales earlier prices
  for some spin-offs, mergers and conversions as if they were splits, while EPS changes only with the
  real share count. The conversion uses the same reviewed event list as EPS and is accurate to about
  0.1%, because vendor ratios are rounded. Splits the vendor applied only to the last sessions are
  handled too.
- **A share exchange the vendor did not record** is handled like a split, with the ratio from the
  company's annual report: earlier Adjusted Close is divided by it, as analysis sites restate it, and
  earlier EPS and share counts are put on the new shares. Close keeps the traded price.
- **Neither Close nor Adjusted Close can be divided directly by EPS.**
- **Prices of another company:** where a ticker's early prices belong to a different company than its
  financials, those price ratios stay empty.

## Time

The app shows the latest filed version of every value, and companies restate earlier years. So two
adjacent years in the table can come from different reports and definitions, and growth computed from
the table can be wrong. Such years are marked as a series break (see Growth marker above).

The API can return the table as known on a date (`as_of`). A year appears from the filing date of its
first annual report. Where a company registered anew with the SEC in a merger or reorganization, the
earlier registrant's reports count from their filing dates for every measure and for growth, and the
screener keeps a value that only they state, such as the earliest balance sheet. A value is known from
the day its report was filed, and filings carry no time of day, so a backtest should act on the next
session. An earlier version is served only when it is confirmed against its own filing. Otherwise it
has no value, or a quarterly version is ignored. Part of the history therefore stays unavailable rather
than possibly wrong. Financials are annual only. The table as of a date also shows a value a quarterly
report restated; the screener's ratios use it only from the next annual report (see Ratios).

## What we correct

A source value changes only when the error is large and a reliable value is known, under one rule for
all companies. These thresholds are a judgement suited to an educational project, not a measured
standard.

| Error | Large when |
|---|---|
| Adjusted Close | Largest difference in the window, per company, over 1% |
| Artificial one-session jump | Over 5% |
| Financial value | Over the larger of 5% of the value and 0.5% of annual revenue |
| Financial value added since Nov 2026 | Same, with the revenue part capped at 25% of the value |
| EPS | Over the larger of 5% and 2 cents |
| Share count | Over 5% |
| Any value | Always: wrong company, scale, sign, period, or used before it was published |

- **Reliable value:** an SEC filing, then a company or exchange statement, then two independent sources.
- **Below the thresholds** the source value stays, so the data contains known small vendor errors.
- **Missing values:** a missing price session is filled, because all companies share one calendar. A
  missing financial value stays empty.
- **Automatic measures** are not reviewed value by value. A value found wrong above these thresholds
  is replaced by the figure its report states, listed with its evidence in
  `data/automatic-value-corrections.json`. Where the company reports no such figure, the cell stays empty
  (as for operating income). More such cases may remain, because we do not search for them one company
  at a time.

## How the data was checked

- Prices were compared with the source files row by row, and the calendar, price consistency and large
  jumps were screened. Dividends were compared with a second source and with amounts reported to the SEC.
- The SEC import stores every fact that meets its criteria, and the database rebuilds from scratch with
  identical content.
- Every reviewed value and every earlier version served by `as_of` was compared with the tables of its
  own filing, automatically where possible and by hand otherwise, with no discrepancies found.
- Accounting identities (EPS against shares and income, cash flow, balance sheet) catch gross errors.
- Automatic values are screened against their reports, which shows that a number stands in the report,
  not that it means what its tag says.

These checks show that numbers match the filings and the stated definitions. They are not an audit and
do not make companies comparable.

## Known limitations

- **Survivorship bias:** the companies were large at the end of 2024, and very few lost money over the
  period. Backtests overstate absolute returns, and a signal favouring beaten-down companies only meets
  those that later recovered. Compare with an equal-weight basket of the same companies, and do not read
  results as beating the market.
- **Backtest window:** every company has a known annual value only from March 2016, and two values, needed
  for growth, from March 2017. Reports for FY2025 filed after 31 Dec 2025 cannot drive a trade in the window.
- **End of the window:** some companies' last period ends by mid-2025, so a cross-section by "FY2025" joins
  periods up to 11 months apart. Compare by period end or `as_of`, not by label.
- **Price ratios after a spin-off:** the price no longer includes the separated business, while the latest
  annual report does until the next one, so P/E, P/B and P/S read too low for up to a year (RTX in 2020,
  AT&T in 2022). The values stay and are marked *Spin-off*: a vendor event that removed more than 3% of
  the price beyond any share split, after the period end and by the price date. The mark ends when a
  report for a period ending after the event is known; in that year net income can still include the
  separated business until the event, as discontinued operations.
- **Price ratios after a large acquisition:** the reverse case is not marked. Between the deal and the next
  annual report, the price is already that of the larger company while the financials describe the smaller
  one, so price ratios join two different companies.
- **Discontinued operations:** net income and EPS include the result of a business sold, spun off or
  held for sale, as on analysis sites that use GAAP EPS (StockAnalysis's P/E of Johnson & Johnson for
  2023 includes Kenvue). Where that result is larger than 5% of net income, P/E, ROE, ROA, net margin,
  OCF / net income and EPS growth are marked *Discontinued ops* in the screener; the values stay. EPS
  growth is also marked when the year before holds such a result, because it is the base of growth. A
  one-off gain then makes a company look cheap or very profitable, so a backtest should leave marked
  values out. The amount comes from the standard tags; a company that tags it otherwise is not marked.
  Where a report gives both the parent's share and the whole amount, the larger one counts, because a
  report can tag only part of the result as the parent's share; the whole amount can include
  noncontrolling interests, so a period may be marked slightly sooner.
- **Spin-off values** follow the vendor's ratios, even where a company's own figures suggest another level
  and no single reliable value exists.
- **Scope:** 200 companies, US-listed, USD only, GAAP figures without adjustments.
- **Data rights:** price data comes from a third-party vendor and its rights for public display are
  unconfirmed. Publishing this repository grants no right to redistribute the datasets.

## Companies handled differently

Companies with a rule of their own, or with a known issue that the general rules leave in place.
Classes the app marks automatically (*Calculated*, *Spin-off*, *Discontinued ops*, *Series break*) are not listed
company by company. Years are fiscal years as each company reports them.

### Revenue

| Company | What differs |
|---|---|
| Banks: American Express, Bank of America, BNY, Citigroup, Capital One, Goldman Sachs, JPMorgan Chase, Morgan Stanley, PNC, Charles Schwab, Truist, U.S. Bancorp, Wells Fargo | Net revenue: the bank's own net line, or net interest income plus noninterest income |
| Exchanges: Intercontinental Exchange, Nasdaq | Revenues less transaction-based expenses (rebates and fees passed on to regulators and for routing) |
| Chevron 2015–2017 | Revenue includes excise taxes, as the company then reported it |
| Exxon Mobil from 2020 | Revenue includes other income, as in the line the company reports |
| Altria, Philip Morris | Altria's revenue includes excise taxes, Philip Morris's excludes them; Philip Morris 2015 is recalculated net of excise, as its later reports restate it |
| EOG Resources | Total revenue as reported, which includes derivative results |
| GE 2015, KKR 2015 | The year keeps an earlier definition from the company's own reports, so the series changes basis |

### Share count and units

| Company | What differs |
|---|---|
| Blackstone 2015, 2016, 2018 | Basic common units, because the diluted count of those years includes units whose holders are outside equity; its diluted count in other years excludes them |
| Mastercard, Alphabet, Meta, Comcast, Accenture, CME Group, Nike from 2016, Regeneron, UPS, Charles Schwab 2020–2023 | No cover total for all classes; P/B and P/S use the weighted average |
| Quanta Services 2017–2018 | No tagged cover count; P/B and P/S use the weighted average |
| Kroger 2015 | Its cover also counted treasury shares; P/B and P/S use the weighted average |
| ConocoPhillips 2015–2018, McDonald's 2023–2025 | The share count's unit is corrected from the first report's table |
| Exchangeable units: Blackstone, KKR until 2022, Charter from 2016, Simon Property, Broadcom fiscal 2017 | P/S counts units with shares, P/B only listed shares; marked *Exchangeable units* |

### Preferred stock and common equity

| Company | What differs |
|---|---|
| Bank of America 2015–2017, Digital Realty 2015–2016, Duke Energy 2020–2024, Morgan Stanley, PNC, Procter & Gamble, Sempra 2018–2021 | Preferred stock read from the statement of changes in equity, at least until a later report tags it |
| Procter & Gamble | Employee preferred shares are deducted, as analysis sites do |
| Visa | Not deducted: its share count already holds them as converted |
| Southern Company 2015–2024 | Preferred stock and dividends of its subsidiaries are not deducted: its net income is already after them and its common equity excludes that stock. Equity of 2015 and 2016 as reports from 2018 state it includes that stock (609 million, below the thresholds) |
| American Express, Becton Dickinson, GE, Boeing, American Tower 2015–2017, Boston Scientific 2020–2022, Kinder Morgan 2016–2017, AT&T 2022–2023 | P/B and ROE empty in affected years: preferred stock is held within paid-in capital |
| American Tower 2018 | P/B and ROE empty for the year, ROE also for the next: the preferred stock converted into common shares during the year, but the report counts shares only by series, so it does not show that none was left |
| KKR 2016, 2017, 2021 | P/B and ROE empty in affected years: its preferred units have no value under the standard tags or in a preferred stock column |
| General Motors 2018, Freeport-McMoRan 2015 | P/B and ROE empty: the preferred stock is a subsidiary's, and the report does not count the company's own preferred shares |
| General Motors 2019–2022 | Nothing deducted from equity, the subsidiary's preferred dividends deducted from net income, as in the company's own earnings per share: the report counts no preferred shares of its own |
| Alphabet 2015 | ROE deducts the adjustment payment to Class C holders, tagged with preferred dividends and other adjustments (about 3% of net income), because the share count is calculated with it from Class A earnings per share |
| Broadcom fiscal 2020, Sempra 2018–2019 | Preferred stock or dividends replaced by the figure the report states (a tag error above the thresholds) |

### Capital expenditure and operating income

| Company | What differs |
|---|---|
| American Electric Power, United Rentals | From the cash flow statement, because the tag holds only part of the cash spent (United Rentals: purchases of rental and other equipment) |
| Eli Lilly, ADP, Verizon from 2019, Roper from 2019 | The only purchase line is tagged as "other" assets |
| EOG Resources | Additions to oil and gas properties and to other equipment added up |
| Diamondback, Phillips 66, NextEra and some years of others | Capital expenditure empty: reported only under their own tags |
| Aflac, Disney, Elevance, Eaton, Honeywell, MetLife, Parker-Hannifin, Sherwin-Williams, SLB, TJX | Operating income empty: the tag holds a sum of segments or an adjusted measure |
| Linde 2017 | Assets replaced by the figure the report states (the tag held the balance sheet of the newly formed, still empty Linde plc) |

### Cash flow and growth

| Company | What differs |
|---|---|
| Citigroup, General Dynamics | Operating cash flow of continuing operations, marked *Continuing ops* |
| Berkshire Hathaway FY2016 (EPS), S&P Global FY2024, Intercontinental Exchange FY2016 (revenue) | Growth empty: no report states both years in a readable form |

### Corporate events and registrants

| Company | What differs |
|---|---|
| Charter, May 2016 | Old shares became 0.9042 new shares each in a merger the vendor did not record; handled like a split |
| Charter, May 2016 to Feb 2017 | Prices of the merged company with the old Charter's financials until its 2016 annual report (see Price ratios after a large acquisition) |
| Johnson Controls to 2 Sep 2016 | Prices of Tyco International, whose shares became JCI shares, with the financials of Johnson Controls Inc.; price ratios empty. The Adient spin-off, which the vendor recorded as a distribution, is marked *Spin-off* |
| RTX, April 2020 | The spin-off value follows the vendor, although the company's own figures put the vendor's earlier level about 4–7% too low; no single reliable value exists |
| Monster Beverage, Amphenol 2026 | Splits the vendor applied only to the last sessions are handled |
| Broadcom (Avago), BlackRock before 2024, Cigna before 2018, Disney before 2019, Johnson Controls (Johnson Controls Inc.), Linde (Praxair) | Reports of the earlier registrant count from their filing dates |
| S&P Global FY2024 | The annual report has no facts in SEC companyfacts; its filing date comes from the rule list |
