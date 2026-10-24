"""Prepare the active price snapshot and import it transactionally into PostgreSQL."""

import csv

from app.data_pipeline.source_files import MANIFEST, read_json
from app.data_pipeline.validation import FIELDS


def validate_prices():
    """Prepare only the active, fully reviewed Benjaminpo snapshot."""
    from .benjaminpo import CANDIDATE, prepare_prices

    manifest = read_json(MANIFEST)
    if manifest.get("id") != "kaggle-benjaminpo-v81":
        raise ValueError("Only the pinned Benjaminpo price source is supported")
    if manifest != read_json(CANDIDATE):
        raise ValueError("Active source differs from the pinned Benjaminpo candidate")
    return prepare_prices(require_reviewed=True)


def import_prices():
    """Insert verified prices atomically; conflicting stored values require explicit replacement."""
    output, companies, summary = validate_prices()
    from sqlalchemy import select, text
    from sqlalchemy.dialects.postgresql import insert

    from app.database import engine
    from app.models import Company

    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(204812, 1)"))
        for ticker, row in companies.items():
            metadata = {
                "ticker": ticker,
                "name": row["Longname"],
                "exchange": row["Exchange"],
                "sector": row["Sector"] or None,
                "currency": "USD",
            }
            old = (
                connection.execute(
                    select(Company.__table__).where(Company.ticker == ticker)
                )
                .mappings()
                .first()
            )
            if old and any(old[k] != v for k, v in metadata.items()):
                raise ValueError(f"Conflicting company metadata: {ticker}")
            connection.execute(
                insert(Company)
                .values(**metadata)
                .on_conflict_do_nothing(index_elements=["ticker"])
            )
        ids = dict(connection.execute(select(Company.ticker, Company.id)).all())
        connection.execute(
            text(
                "CREATE TEMP TABLE price_stage (LIKE daily_prices INCLUDING CONSTRAINTS) ON COMMIT DROP"
            )
        )
        raw = connection.connection.driver_connection
        assert raw is not None  # engine.begin() holds an open driver connection.
        with (
            raw.cursor() as cursor,
            output.open(newline="", encoding="utf-8") as handle,
        ):
            with cursor.copy(
                "COPY price_stage (company_id,date,open,high,low,close,adjusted_close,volume) FROM STDIN"
            ) as copy:
                for row in csv.DictReader(handle):
                    copy.write_row(
                        (ids[row["ticker"]], row["date"], *(row[f] for f in FIELDS[2:]))
                    )
        conflict = connection.execute(
            text("""
            SELECT 1 FROM price_stage s JOIN daily_prices p USING (company_id,date)
            WHERE (s.open,s.high,s.low,s.close,s.adjusted_close,s.volume)
              IS DISTINCT FROM (p.open,p.high,p.low,p.close,p.adjusted_close,p.volume) LIMIT 1
        """)
        ).first()
        if conflict:
            raise ValueError("Existing prices differ in value; no data overwritten")
        inserted = connection.execute(
            text(
                "INSERT INTO daily_prices SELECT * FROM price_stage ON CONFLICT (company_id,date) DO NOTHING"
            )
        ).rowcount
        result = {
            **summary,
            "inserted": inserted,
            "unchanged": summary["prices"] - inserted,
            "status": "completed",
        }
    return result
