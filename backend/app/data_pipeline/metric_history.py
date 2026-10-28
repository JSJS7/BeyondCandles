"""Rebuild the stored point-in-time history of annual metrics from facts and rules."""

import hashlib

from sqlalchemy import delete, insert, select, text

from app import eps_splits
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
    ORDER BY c.ticker, v.period_start, v.period_end, v.metric, v.known_from
""")


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


def history_fingerprint(connection):
    """SHA-256 of the whole table in a fixed order; equal content gives equal output."""
    digest = hashlib.sha256()
    rows = 0
    for row in connection.execute(CANONICAL_ROWS):
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
    companies = connection.execute(
        select(Company.id, Company.ticker).where(Company.cik.is_not(None))
    ).all()
    connection.execute(delete(AnnualMetricVersion))
    statuses = {}
    confirmations = {}
    for company_id, ticker in sorted(companies, key=lambda company: company.ticker):
        facts = (
            connection.execute(
                select(FinancialFact.__table__).where(
                    FinancialFact.company_id == company_id
                )
            )
            .mappings()
            .all()
        )
        versions = company_versions(ticker, facts, split_policy, period_rules, reviews)
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
    return {
        "companies": len(companies),
        "versions": count,
        "statuses": statuses,
        "confirmations": confirmations,
        "fingerprint": fingerprint,
    }


def build_history():
    """Standalone rebuild, e.g. after reviewed rules change without new SEC facts."""
    from app.database import engine, lock_data_imports

    with engine.begin() as connection:
        # The same lock as import-sec: a rebuild never interleaves with an import.
        lock_data_imports(connection)
        result = rebuild_history(connection)
    return {"status": "completed", **result}
