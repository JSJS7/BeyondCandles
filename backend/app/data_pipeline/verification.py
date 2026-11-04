"""Inspect the database and verify stored prices against the prepared snapshot."""

import csv
import hashlib
import json
from decimal import Decimal

from app.data_pipeline.prices import validate_prices
from app.data_pipeline.source_files import ROOT
from app.data_pipeline.validation import FIELDS

FINGERPRINT_MODULUS = 2**256


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


def rows_fingerprint(rows):
    """Order-independent sum of row fingerprints; returns (row count, 64 hex digits)."""
    digest = 0
    count = 0
    for row in rows:
        digest = (digest + fingerprint(row)) % FINGERPRINT_MODULUS
        count += 1
    return count, f"{digest:064x}"


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

    from app.annual_financials import METRIC_NAMES
    from app.data_pipeline.metric_history import (
        current_cell_statuses,
        history_fingerprint,
        verify_history,
    )
    from app.data_pipeline.sec import verify_stored_facts
    from app.database import engine

    path, _, validated = validate_prices()
    with path.open(encoding="utf-8", newline="") as handle:
        rows = ([row[f] for f in FIELDS] for row in csv.DictReader(handle))
        _, expected = rows_fingerprint(rows)
    with engine.connect() as connection:
        stored = connection.execution_options(stream_results=True).execute(
            text("""
            SELECT c.ticker,p.date,p.open,p.high,p.low,p.close,p.adjusted_close,p.volume
            FROM daily_prices p JOIN companies c ON c.id=p.company_id
        """)
        )
        count, observed = rows_fingerprint(stored.yield_per(10000))
        if count != validated["prices"] or observed != expected:
            raise ValueError("Stored price data does not match validated source")
        db_companies = connection.execute(
            text("SELECT count(DISTINCT company_id) FROM daily_prices")
        ).scalar_one()
        if db_companies != 200:
            raise ValueError("Stored company count differs from 200")
        financial = verify_stored_facts(connection)
        total_facts = connection.execute(
            text("SELECT count(*) FROM financial_facts")
        ).scalar_one()
        if total_facts != financial:
            raise ValueError("Stored facts belong to unknown companies")
    versions, history = verify_history(engine)
    with engine.connect() as connection:
        statuses = current_cell_statuses(connection)
        reviewed_versions, reviewed_history = history_fingerprint(
            connection, METRIC_NAMES
        )
    not_ready = [cell for cell, status in statuses.items() if status != "ready"]
    if not_ready:
        raise ValueError(
            f"{len(not_ready)} current metric cells are not ready, first {not_ready[0]}"
        )
    result = {
        "status": "verified",
        "companies": db_companies,
        "prices": count,
        "price_fingerprint": observed,
        "financial_facts": financial,
        "metric_versions": versions,
        "current_metric_cells": len(statuses),
        "metric_history_fingerprint": history,
        "reviewed_metric_versions": reviewed_versions,
        "reviewed_metric_history_fingerprint": reviewed_history,
        "period_start": validated["period_start"],
        "period_end": validated["period_end"],
    }
    (ROOT / "data/processed/database-verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result
