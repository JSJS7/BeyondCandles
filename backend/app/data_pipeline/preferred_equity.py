"""Read preferred stock from the equity statement of each report's original XBRL instance.

companyfacts holds no facts with dimensions and no company tags, so a preferred value
reported only as a column of the statement of changes in equity never reaches it
(M49: MS, PNC, DUK, SRE). This review reads that column from the instance of the first
annual report of every period whose preferred dividends would otherwise leave P/B and
ROE empty, and publishes the values with their source for the history build.
"""

from decimal import Decimal

from sqlalchemy import bindparam, text

from app.annual_financials import ANNUAL_REPORT_FORMS
from app.automatic_metrics import (
    IMMATERIAL_DIVIDENDS_OF_EQUITY,
    LARGEST_PREFERRED_DIVIDEND_RATE,
    PREFERRED_EQUITY_VALUES_PATH,
)
from app.data_pipeline.eps import parse_with_scopes
from app.data_pipeline.revenue_facts import cache_instance
from app.data_pipeline.sec import XBRLDI, XBRLI, XSI, usd_unit_ids
from app.data_pipeline.sec_cache import RequestGate
from app.data_pipeline.sec_catalogue import verify_reference
from app.data_pipeline.source_files import read_json, write_json

US_GAAP = "http://fasb.org/us-gaap/"
EQUITY_COMPONENTS_AXIS = "StatementEquityComponentsAxis"
# The balance of a column is parent equity, or equity with noncontrolling interests,
# which many companies use for every column of the statement (MS, PG, PNC from 2019).
COLUMN_BALANCE_CONCEPTS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
# A column of preferred stock together with its paid-in capital may state its balance
# under the paid-in capital concept, which is then the whole column (PNC 2015–2018).
PAID_IN_CAPITAL_COLUMN = "PreferredStockIncludingAdditionalPaidInCapitalMember"
PAID_IN_CAPITAL_CONCEPT = "AdditionalPaidInCapitalPreferredStock"
FETCH_INTERVAL = 0.5
METHOD = (
    "Preferred stock of periods whose preferred dividends exceed 0.2% of equity while "
    "companyfacts has no positive preferred value of which the dividends are at most "
    "25%. Each value is the balance at the "
    "period end of the one column of the statement of changes in equity whose "
    "StatementEquityComponentsAxis member names preferred stock, in the original XBRL "
    "instance of the first annual report of the period, with no other dimension. "
    "Periods without such a positive balance are listed with the reason."
)

# Periods in scope: preferred dividends tagged, no tagged value that the dividends leave
# plausible (PNC tags 0.5 million of par value), and equity from the latest report.
PERIODS = text("""
    WITH equity AS (
        SELECT DISTINCT ON (company_id, period_end) company_id, period_end, value
        FROM annual_metric_versions
        WHERE metric = 'equity'
        ORDER BY company_id, period_end, known_from DESC, accession DESC
    ), first_annual AS (
        SELECT DISTINCT ON (company_id, period_end)
               company_id, period_end, accession, known_from, form
        FROM annual_metric_versions
        WHERE metric = 'equity' AND form IN :forms
        ORDER BY company_id, period_end, known_from, accession
    ), dividends AS (
        SELECT company_id, period_end, MAX(value) AS value
        FROM financial_facts
        WHERE metric = 'preferred_dividends' AND period_kind = 'annual' AND value > 0
        GROUP BY company_id, period_end
    )
    SELECT c.ticker, c.cik, e.period_end, e.value AS equity, d.value AS dividends,
           f.accession, f.known_from, f.form
    FROM equity e
    JOIN companies c ON c.id = e.company_id
    JOIN dividends d ON d.company_id = e.company_id AND d.period_end = e.period_end
    JOIN first_annual f ON f.company_id = e.company_id AND f.period_end = e.period_end
    WHERE NOT EXISTS (
        SELECT 1 FROM financial_facts p
        WHERE p.company_id = e.company_id AND p.metric = 'preferred_value'
          AND p.period_end = e.period_end AND p.value > 0
          AND d.value <= p.value * :largest_rate
    )
    ORDER BY c.ticker, e.period_end
""").bindparams(bindparam("forms", expanding=True), bindparam("largest_rate"))


def needs_preferred_value(equity, dividends):
    """Whether, without a value, the dividends leave common equity unknown."""
    if equity is None or equity <= 0:
        return True
    return dividends > equity * IMMATERIAL_DIVIDENDS_OF_EQUITY


def resolve_name(qualified_name, scope):
    """The namespace and local name of a prefixed name, as declared where it is written."""
    prefix, _, local_name = qualified_name.strip().rpartition(":")
    return scope.get(prefix, ""), local_name


def preferred_column_contexts(root, scopes, cik, period_end):
    """Contexts of this company at the period end with only a preferred column member."""
    contexts = {}
    for context in root.findall(XBRLI + "context"):
        identifier = context.findtext(XBRLI + "entity/" + XBRLI + "identifier", "")
        if identifier.strip().zfill(10) != cik:
            continue
        instant = context.findtext(XBRLI + "period/" + XBRLI + "instant", "")
        if instant.strip() != str(period_end):
            continue
        if context.find(".//" + XBRLDI + "typedMember") is not None:
            continue
        members = context.findall(".//" + XBRLDI + "explicitMember")
        if len(members) != 1:
            continue
        member = members[0]
        scope = scopes[member]
        axis_namespace, axis = resolve_name(member.get("dimension", ""), scope)
        _, member_name = resolve_name(member.text or "", scope)
        if not axis_namespace.startswith(US_GAAP) or axis != EQUITY_COMPONENTS_AXIS:
            continue
        if "Preferred" not in member_name:
            continue
        contexts[context.get("id")] = member_name
    return contexts


def preferred_column(body, cik, period_end):
    """The preferred column balance as (value, concept, member), or a reason for none."""
    root, scopes = parse_with_scopes(body)
    usd_units = usd_unit_ids(root, scopes[root])
    contexts = preferred_column_contexts(root, scopes, cik, period_end)
    balances = {}
    for element in root:
        member = contexts.get(element.get("contextRef"))
        if member is None or element.get("unitRef") not in usd_units:
            continue
        if element.get(XSI + "nil") == "true" or not (element.text or "").strip():
            continue
        namespace, _, concept = element.tag[1:].partition("}")
        if not namespace.startswith(US_GAAP):
            continue
        is_balance = concept in COLUMN_BALANCE_CONCEPTS
        is_paid_in_column = (
            concept == PAID_IN_CAPITAL_CONCEPT and member == PAID_IN_CAPITAL_COLUMN
        )
        if not is_balance and not is_paid_in_column:
            continue
        value = Decimal(element.text.strip())
        balances.setdefault(value, (concept, member))
    if not balances:
        return {"reason": "no preferred column"}
    if len(balances) > 1:
        return {"reason": "columns disagree"}
    value, (concept, member) = next(iter(balances.items()))
    if value <= 0:
        return {"reason": "zero" if value == 0 else "negative"}
    return {"value": value, "concept": concept, "member": member}


def previous_instances():
    """Instance references of the published file, so a rerun needs no download."""
    if not PREFERRED_EQUITY_VALUES_PATH.is_file():
        return {}
    document = read_json(PREFERRED_EQUITY_VALUES_PATH)
    references = {}
    for entry in document["values"] + document["without_value"]:
        key = (entry["ticker"], entry["period_end"], entry["accession"])
        references[key] = entry["instance"]
    return references


def fetch_instance(gate, cik, accession):
    """Download one report's instance to the SEC cache and return its reference."""
    evidence = {}

    def download():
        cache_instance(cik, accession, evidence)

    gate.run_alone(download, interval=FETCH_INTERVAL)
    return evidence["instance"]


def review_preferred_equity(fetch_missing=False):
    """Publish the preferred column of every period in scope; --fetch downloads instances."""
    from app.database import engine

    with engine.connect() as connection:
        parameters = {
            "forms": list(ANNUAL_REPORT_FORMS),
            "largest_rate": LARGEST_PREFERRED_DIVIDEND_RATE,
        }
        rows = connection.execute(PERIODS, parameters).all()
    previous = previous_instances()
    gate = RequestGate("SEC denied access; preferred equity review stopped")
    values = []
    without_value = []
    not_cached = []
    for row in rows:
        if not needs_preferred_value(row.equity, row.dividends):
            continue
        period_end = str(row.period_end)
        instance = previous.get((row.ticker, period_end, row.accession))
        if instance is None:
            if not fetch_missing:
                not_cached.append(f"{row.ticker} {period_end} {row.accession}")
                continue
            instance = fetch_instance(gate, row.cik, row.accession)
        body = verify_reference(instance).read_bytes()
        column = preferred_column(body, row.cik, row.period_end)
        entry = {
            "ticker": row.ticker,
            "cik": row.cik,
            "period_end": period_end,
            "accession": row.accession,
            "filed": str(row.known_from),
            "form": row.form,
        }
        if "value" in column:
            entry["value"] = format(column["value"], "f")
            entry["concept"] = column["concept"]
            entry["member"] = column["member"]
            entry["instance"] = instance
            values.append(entry)
        else:
            entry["reason"] = column["reason"]
            entry["instance"] = instance
            without_value.append(entry)
    if not_cached:
        # Publishing without them would silently drop periods from the file.
        return {"status": "needs_review", "not_cached": not_cached}
    write_json(
        PREFERRED_EQUITY_VALUES_PATH,
        {
            "version": 1,
            "method": METHOD,
            "values": values,
            "without_value": without_value,
        },
    )
    return {
        "status": "completed",
        "values": len(values),
        "without_value": len(without_value),
        "file": "data/preferred-equity-values.json",
    }
