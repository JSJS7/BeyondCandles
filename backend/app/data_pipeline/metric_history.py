"""Rebuild the stored point-in-time history of annual metrics from facts and rules."""

import hashlib

from sqlalchemy import bindparam, delete, insert, select, text

from app import eps_splits
from app.annual_financials import METRIC_NAMES, reviewed_facts_query
from app.automatic_metrics import (
    AUTOMATIC_METRICS,
    automatic_versions,
    operating_income_exclusions,
    share_scale_corrections,
)
from app.fiscal_periods import fiscal_rules
from app.metric_history import company_versions, version_reviews
from app.models import AnnualMetricVersion, Company, FinancialFact

# details keeps every AnnualMetric field except variants (attached from facts at
# request time). The value is also kept there in its served form: the numeric column
# pads it to ten decimal places, which would change the API text of the same number.
EXCLUDED_FIELDS = {"variants"}
CANONICAL_ROWS = text("""
    SELECT c.ticker, v.period_start, v.period_end, v.metric, v.known_from,
           v.accession, v.form, v.status, v.confirmation, v.value,
           CAST(v.details AS TEXT)
    FROM annual_metric_versions v JOIN companies c ON c.id = v.company_id
    WHERE v.metric IN :metrics
    ORDER BY c.ticker, v.period_start, v.period_end, v.metric, v.known_from
""").bindparams(bindparam("metrics", expanding=True))
ALL_METRICS = (*METRIC_NAMES, *AUTOMATIC_METRICS)


def version_row(company_id, version):
    metric = version.value
    return {
        "company_id": company_id,
        "period_start": version.period_start,
        "period_end": version.period_end,
        "metric": version.metric,
        "known_from": version.known_from,
        "accession": version.accession,
        "form": version.form,
        "status": metric.status,
        "confirmation": version.confirmation,
        "value": metric.value,
        "details": metric.model_dump(mode="json", exclude=EXCLUDED_FIELDS),
    }


def history_fingerprint(connection, metrics=ALL_METRICS):
    """SHA-256 of the chosen metrics in a fixed order; equal content gives equal output.

    With METRIC_NAMES it proves that the automatic measures left the reviewed ones
    untouched: the result stays equal to the fingerprint from before they existed.
    """
    digest = hashlib.sha256()
    rows = 0
    for row in connection.execute(CANONICAL_ROWS, {"metrics": list(metrics)}):
        fields = []
        for field in row:
            if field is None:
                fields.append("")
            elif hasattr(field, "as_tuple"):
                fields.append(format(field, "f"))
            else:
                fields.append(str(field))
        digest.update(("|".join(fields) + "\n").encode("utf-8"))
        rows += 1
    return rows, digest.hexdigest()


def rebuild_history(connection):
    """Replace every stored version within the caller's transaction."""
    period_rules = fiscal_rules()
    # Read through the module so the API and the build always use the same policy.
    split_policy = eps_splits.split_rules()
    reviews = version_reviews()
    scale_corrections = share_scale_corrections()
    income_exclusions = operating_income_exclusions()
    companies = connection.execute(
        select(Company.id, Company.ticker).where(Company.cik.is_not(None))
    ).all()
    connection.execute(delete(AnnualMetricVersion))
    statuses = {}
    confirmations = {}
    for company_id, ticker in sorted(companies, key=lambda company: company.ticker):
        facts = connection.execute(reviewed_facts_query(company_id)).mappings().all()
        versions = company_versions(ticker, facts, split_policy, period_rules, reviews)
        automatic_facts = (
            connection.execute(
                select(FinancialFact.__table__)
                .where(
                    FinancialFact.company_id == company_id,
                    FinancialFact.metric.in_(AUTOMATIC_METRICS),
                )
                .order_by(FinancialFact.id)
            )
            .mappings()
            .all()
        )
        periods = {(version.period_start, version.period_end) for version in versions}
        versions += automatic_versions(
            ticker,
            automatic_facts,
            periods,
            split_policy,
            period_rules,
            scale_corrections,
            income_exclusions,
        )
        rows = [version_row(company_id, version) for version in versions]
        for row in rows:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
            confirmation = row["confirmation"] or "none"
            confirmations[confirmation] = confirmations.get(confirmation, 0) + 1
        for offset in range(0, len(rows), 1000):
            connection.execute(
                insert(AnnualMetricVersion), rows[offset : offset + 1000]
            )
    count, fingerprint = history_fingerprint(connection)
    reviewed_count, reviewed_fingerprint = history_fingerprint(connection, METRIC_NAMES)
    return {
        "companies": len(companies),
        "versions": count,
        "statuses": statuses,
        "confirmations": confirmations,
        "fingerprint": fingerprint,
        "reviewed_versions": reviewed_count,
        "reviewed_fingerprint": reviewed_fingerprint,
    }


def verify_history(engine):
    """Compare the stored history with a rebuild from current facts, code and rules.

    The rebuild runs in a transaction that is always rolled back, so the stored table
    and the API stay unchanged. A difference means build-history was not run after a
    change, e.g. of a rules file or of the served number format.
    """
    from app.database import lock_data_imports

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            # The same lock as import-sec: no import may change the facts meanwhile.
            lock_data_imports(connection)
            stored = history_fingerprint(connection)
            rebuilt = rebuild_history(connection)
        finally:
            transaction.rollback()
    if (rebuilt["versions"], rebuilt["fingerprint"]) != stored:
        raise ValueError(
            "Stored metric history differs from a rebuild with the current code and "
            "rules; run build-history"
        )
    return stored


def current_cell_statuses(connection):
    """Return the status of each cell's latest version, by ticker, period and metric.

    Earlier versions may stay unconfirmed: BK's first 2015 revenue is one, and the API
    leaves it out of history rather than serving it. The latest version is what the
    current view serves, so it must be ready.
    """
    rows = connection.execute(
        select(
            Company.ticker,
            AnnualMetricVersion.period_start,
            AnnualMetricVersion.period_end,
            AnnualMetricVersion.metric,
            AnnualMetricVersion.known_from,
            AnnualMetricVersion.status,
        )
        .join(Company, Company.id == AnnualMetricVersion.company_id)
        .order_by(AnnualMetricVersion.known_from)
    )
    latest = {}
    for row in rows:
        latest[(row.ticker, row.period_start, row.period_end, row.metric)] = row.status
    return latest


def build_history():
    """Standalone rebuild, e.g. after reviewed rules change without new SEC facts."""
    from app.database import engine, lock_data_imports

    with engine.begin() as connection:
        # The same lock as import-sec: a rebuild never interleaves with an import.
        lock_data_imports(connection)
        result = rebuild_history(connection)
    return {"status": "completed", **result}
