import csv
import hashlib
import json
from decimal import Decimal

from app.data_pipeline.prices import validate_prices
from app.data_pipeline.source_files import ROOT
from app.data_pipeline.validation import FIELDS


def fingerprint(values):
    ticker, day, *numbers = values
    canonical = [ticker, str(day)]
    for number in numbers:
        # Decimal.normalize() rounds to the current context precision (28 by default).
        # Formatting first preserves all source digits; remove only decimal padding.
        formatted = format(Decimal(str(number)), "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        canonical.append(formatted)
    return int.from_bytes(hashlib.sha256("|".join(canonical).encode()).digest(), "big")


def inspect_db():
    from sqlalchemy import inspect, text

    from app.database import engine

    with engine.connect() as connection:
        tables = inspect(connection).get_table_names()
        versions = []
        if "alembic_version" in tables:
            versions = (
                connection.execute(text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )
        return {"tables": tables, "migration_versions": versions}


def verify_db():
    from sqlalchemy import text

    from app.database import engine

    path, _, validated = validate_prices()
    expected = 0
    observed = 0
    count = 0
    modulus = 2**256
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            expected = (expected + fingerprint([row[f] for f in FIELDS])) % modulus
    with engine.connect() as connection:
        for row in (
            connection.execution_options(stream_results=True)
            .execute(
                text("""
            SELECT c.ticker,p.date,p.open,p.high,p.low,p.close,p.adjusted_close,p.volume
            FROM daily_prices p JOIN companies c ON c.id=p.company_id
        """)
            )
            .yield_per(10000)
        ):
            observed = (observed + fingerprint(row)) % modulus
            count += 1
        if count != validated["prices"] or observed != expected:
            raise ValueError("Stored price data does not match validated source")
        db_companies = connection.execute(
            text("SELECT count(DISTINCT company_id) FROM daily_prices")
        ).scalar_one()
        if db_companies != 200:
            raise ValueError("Stored company count differs from 200")
        financial = connection.execute(
            text("SELECT count(*) FROM financial_facts")
        ).scalar_one()
    result = {
        "status": "verified",
        "companies": db_companies,
        "prices": count,
        "price_fingerprint": f"{observed:064x}",
        "financial_facts": financial,
        "period_start": validated["period_start"],
        "period_end": validated["period_end"],
    }
    (ROOT / "data/processed/database-verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result
