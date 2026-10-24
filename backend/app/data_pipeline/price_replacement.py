"""Replace a complete frozen price snapshot with a checked backup and rollback path."""

import csv
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import text

from app.data_pipeline.source_files import MANIFEST, ROOT, read_json, sha256
from app.data_pipeline.validation import FIELDS

from .benjaminpo import CANDIDATE, atomic_json, prepare_prices
from .verification import fingerprint

BACKUPS = ROOT / "data/raw/backups"
MODULUS = 2**256
PRICE_QUERY = """
    SELECT c.ticker,p.date,p.open,p.high,p.low,p.close,p.adjusted_close,p.volume
    FROM daily_prices p JOIN companies c ON c.id=p.company_id
    ORDER BY c.ticker,p.date
"""


def rows_fingerprint(rows):
    digest = 0
    count = 0
    for row in rows:
        digest = (digest + fingerprint(row)) % MODULUS
        count += 1
    return count, f"{digest:064x}"


def file_fingerprint(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return rows_fingerprint(
            [row[field] for field in FIELDS] for row in csv.DictReader(handle)
        )


def database_fingerprint(connection):
    rows = connection.execute(text(PRICE_QUERY).execution_options(stream_results=True))
    return rows_fingerprint(rows.yield_per(10000))


def protect_tables(connection):
    """Serialize price imports and hold other data stable while replacing prices."""
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(204812, 1)"))
    connection.execute(text("LOCK TABLE companies, financial_facts IN SHARE MODE"))
    connection.execute(text("LOCK TABLE daily_prices IN SHARE ROW EXCLUSIVE MODE"))


def other_data_fingerprint(connection):
    result = {}
    for table in ("companies", "financial_facts"):
        digest = hashlib.sha256()
        count = 0
        # Fixed table names only. JSON preserves every column, including identity
        # and SEC history flags, rather than checking only the number of rows.
        query = f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"
        for row in connection.execute(
            text(query).execution_options(stream_results=True)
        ).yield_per(10000):
            digest.update(row[0].encode("utf-8") + b"\n")
            count += 1
        result[table] = {"count": count, "sha256": digest.hexdigest()}
    return result


def stage_prices(connection, path):
    connection.execute(
        text(
            "CREATE TEMP TABLE replacement_stage "
            "(LIKE daily_prices INCLUDING ALL) ON COMMIT DROP"
        )
    )
    company_ids = dict(
        connection.execute(text("SELECT ticker,id FROM companies")).all()
    )
    copy_query = (
        "COPY replacement_stage "
        "(company_id,date,open,high,low,close,adjusted_close,volume) FROM STDIN"
    )
    with connection.connection.driver_connection.cursor() as cursor:
        with cursor.copy(copy_query) as copy:
            with Path(path).open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    company_id = company_ids.get(row["ticker"])
                    if company_id is None:
                        raise ValueError(
                            f"Replacement contains an unknown company: {row['ticker']}"
                        )
                    copy.write_row(
                        (company_id, row["date"], *(row[field] for field in FIELDS[2:]))
                    )
    mismatch = connection.execute(
        text("""
        SELECT 1 FROM replacement_stage s FULL JOIN daily_prices p USING(company_id,date)
        WHERE s.company_id IS NULL OR p.company_id IS NULL LIMIT 1
    """)
    ).first()
    if mismatch:
        raise ValueError("Replacement must preserve the exact stored company/date keys")


def update_staged_prices(connection):
    return connection.execute(
        text("""
        UPDATE daily_prices p
        SET open=s.open,high=s.high,low=s.low,close=s.close,
            adjusted_close=s.adjusted_close,volume=s.volume
        FROM replacement_stage s
        WHERE p.company_id=s.company_id AND p.date=s.date
          AND (p.open,p.high,p.low,p.close,p.adjusted_close,p.volume)
          IS DISTINCT FROM (s.open,s.high,s.low,s.close,s.adjusted_close,s.volume)
    """)
    ).rowcount


def save_backup(connection, observed, manifest):
    """Finish and verify the full old CSV before the first UPDATE is issued."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    path = BACKUPS / f"prices-{observed[1]}.csv"
    info_path = path.with_suffix(".json")
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=BACKUPS, delete=False
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.writer(handle)
            writer.writerow(FIELDS)
            query = text(PRICE_QUERY).execution_options(stream_results=True)
            for row in connection.execute(query).yield_per(10000):
                writer.writerow(row)
        if file_fingerprint(temporary) != observed:
            raise ValueError("Price backup does not match the stored snapshot")
        if path.exists():
            if sha256(path) != sha256(temporary):
                raise ValueError("An existing backup has different bytes")
        else:
            temporary.replace(path)
        company_ids = connection.execute(
            text("SELECT ticker,id FROM companies ORDER BY ticker")
        )
        info = {
            "file": path.name,
            "sha256": sha256(path),
            "rows": observed[0],
            "price_fingerprint": observed[1],
            "active_manifest": manifest,
            "company_ids": dict(company_ids.all()),
        }
        if info_path.exists():
            if read_json(info_path) != info:
                raise ValueError(
                    "Existing backup metadata differs; preserve it for review"
                )
        else:
            atomic_json(info_path, info)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return path


def require_expected_fingerprint(value):
    if (
        value is None
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(
            "Pass --expected-price-fingerprint with the "
            "64-character verified stored fingerprint"
        )


def activate_manifest(document):
    atomic_json(MANIFEST, document)


def replace_prices(expected_fingerprint):
    """Only a fully reviewed candidate may replace the exact expected snapshot."""
    require_expected_fingerprint(expected_fingerprint)
    path, _, summary = prepare_prices(require_reviewed=True)
    candidate = read_json(CANDIDATE)
    candidate_hash = sha256(CANDIDATE)
    if (
        candidate_hash != summary["manifest_sha256"]
        or sha256(path) != summary["processed_sha256"]
    ):
        raise ValueError("Prepared candidate changed since validation")
    target = file_fingerprint(path)
    from app.database import engine

    with engine.begin() as connection:
        protect_tables(connection)
        observed = database_fingerprint(connection)
        if observed == target:
            # Recover an interruption between the database COMMIT and manifest
            # activation; do not rewrite prices or discard the original backup.
            result = {
                "status": "unchanged",
                "updated": 0,
                "price_fingerprint": target[1],
            }
        else:
            if observed[1] != expected_fingerprint or observed[0] != summary["prices"]:
                raise ValueError(
                    "Stored prices changed since verification; replacement cancelled"
                )
            unchanged = other_data_fingerprint(connection)
            stage_prices(connection, path)
            backup = save_backup(connection, observed, read_json(MANIFEST))
            updated = update_staged_prices(connection)
            if database_fingerprint(connection) != target:
                raise ValueError("Stored prices do not match the prepared replacement")
            if other_data_fingerprint(connection) != unchanged:
                raise ValueError(
                    "Companies or SEC facts changed during price replacement"
                )
            result = {
                "status": "replaced",
                "updated": updated,
                "backup": str(backup.relative_to(ROOT)),
                "price_fingerprint": target[1],
            }
        if sha256(CANDIDATE) != candidate_hash:
            raise ValueError("Candidate manifest changed during replacement")
    activate_manifest(candidate)
    return {**result, "prices": target[0]}


def restore_prices(backup_path, expected_fingerprint):
    """Restore exact prior prices and manifest without changing any company IDs."""
    require_expected_fingerprint(expected_fingerprint)
    if not backup_path:
        raise ValueError("Pass --backup with a price replacement CSV backup")
    path = Path(backup_path).resolve()
    if (
        path.parent != BACKUPS.resolve()
        or not path.name.startswith("prices-")
        or path.suffix != ".csv"
    ):
        raise ValueError("Expected a price replacement backup in data/raw/backups")
    info = read_json(path.with_suffix(".json"))
    if info["file"] != path.name or sha256(path) != info["sha256"]:
        raise ValueError("Price backup checksum mismatch")
    target = file_fingerprint(path)
    if target != (info["rows"], info["price_fingerprint"]):
        raise ValueError("Price backup fingerprint mismatch")
    from app.database import engine

    with engine.begin() as connection:
        protect_tables(connection)
        ids = dict(
            connection.execute(
                text("SELECT ticker,id FROM companies ORDER BY ticker")
            ).all()
        )
        if ids != info["company_ids"]:
            raise ValueError("Company identities differ from the backup")
        observed = database_fingerprint(connection)
        if observed == target:
            updated = 0
        else:
            if observed[1] != expected_fingerprint:
                raise ValueError(
                    "Stored prices changed since verification; restore cancelled"
                )
            unchanged = other_data_fingerprint(connection)
            stage_prices(connection, path)
            updated = update_staged_prices(connection)
            if database_fingerprint(connection) != target:
                raise ValueError("Restored prices differ from the backup")
            if other_data_fingerprint(connection) != unchanged:
                raise ValueError("Companies or SEC facts changed during price restore")
    activate_manifest(info["active_manifest"])
    return {
        "status": "restored",
        "updated": updated,
        "prices": target[0],
        "price_fingerprint": target[1],
    }
