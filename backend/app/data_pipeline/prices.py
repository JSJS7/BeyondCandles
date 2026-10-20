import csv
import hashlib
import io
import json
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "data/manifest.json"
SELECTION = ROOT / "data/companies-200.json"
FIELDS = ["ticker", "date", "open", "high", "low", "close", "adjusted_close", "volume"]
PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Adj Close")
TOLERANCE = Decimal("0.000002")


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def number(value, label):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite {label}")
    return result


def validate_price(row):
    values = [number(row[column], column) for column in PRICE_COLUMNS]
    with localcontext() as context:
        context.prec = 50
        for value in values:
            if value <= 0 or value >= Decimal("1e14"):
                raise ValueError("Prices must be positive and fit NUMERIC(32,18)")
            if value != value.quantize(Decimal("1e-18")):
                raise ValueError("Prices must be positive and fit NUMERIC(32,18)")

    open_price, high, low, close, adjusted_close = values
    highest_raw_price = max(open_price, low, close)
    lowest_raw_price = min(open_price, high, close)
    if high + TOLERANCE < highest_raw_price or low - TOLERANCE > lowest_raw_price:
        raise ValueError("Invalid OHLC envelope")
    volume = number(row["Volume"], "Volume")
    if volume < 0 or volume != volume.to_integral_value() or volume > 9223372036854775807:
        raise ValueError("Volume must be a nonnegative BIGINT")
    return [str(v) for v in values] + [str(int(volume))]


def validate_prices(archive=None):
    manifest = read_json(MANIFEST)
    selection = read_json(SELECTION)
    if sha256(SELECTION) != manifest["selection_sha256"]:
        raise ValueError("Company selection checksum mismatch")
    archive = Path(archive) if archive else ROOT / manifest["archive"]
    if sha256(archive) != manifest["sha256"]:
        raise ValueError("Source archive checksum mismatch")
    tickers = {c["Symbol"] for c in selection["companies"]}
    if len(tickers) != 200 or selection["company_count"] != 200:
        raise ValueError("Expected exactly 200 selected tickers")
    dates = {ticker: set() for ticker in tickers}
    companies = {}
    output = ROOT / "data/processed/prices-200.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    count = 0
    try:
        with ZipFile(archive) as z, NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=output.parent, delete=False) as out:
            temporary = Path(out.name)
            writer = csv.writer(out)
            writer.writerow(FIELDS)
            with z.open("sp500_companies.csv") as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8")):
                    if row["Symbol"] in tickers:
                        if row["Symbol"] in companies or not row["Longname"] or not row["Exchange"]:
                            raise ValueError("Duplicate or incomplete company metadata")
                        companies[row["Symbol"]] = row
            if companies.keys() != tickers:
                raise ValueError("Missing company metadata")
            with z.open("sp500_stocks.csv") as raw:
                for line, row in enumerate(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8")), 2):
                    ticker = row["Symbol"]
                    day = row["Date"]
                    if ticker not in tickers:
                        continue
                    date.fromisoformat(day)
                    if not manifest["period_start"] <= day <= manifest["period_end"]:
                        continue
                    if day in dates[ticker]:
                        raise ValueError(f"Duplicate price: {ticker} {day}")
                    try:
                        values = validate_price(row)
                    except ValueError as exc:
                        raise ValueError(f"{ticker} {day} at source line {line}: {exc}") from exc
                    dates[ticker].add(day)
                    writer.writerow([ticker, day, *values])
                    count += 1
            reference = dates["AAPL"]
            calendar_hash = hashlib.sha256("\n".join(sorted(reference)).encode()).hexdigest()
            if calendar_hash != manifest["calendar_sha256"] or len(reference) != manifest["trading_dates"]:
                raise ValueError("Frozen source calendar mismatch")
            for ticker, observed in dates.items():
                if observed != reference:
                    raise ValueError(f"Incomplete calendar: {ticker}")
            if count != manifest["price_rows"]:
                raise ValueError(f"Expected {manifest['price_rows']} prices, found {count}")
        temporary.replace(output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    summary = {
        "companies": len(companies),
        "prices": count,
        "period_start": min(reference),
        "period_end": max(reference),
        "sha256": manifest["sha256"],
        "processed_sha256": sha256(output),
    }
    (output.parent / "prices-validation.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return output, companies, summary


def import_prices(archive=None):
    output, companies, summary = validate_prices(archive)
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
            old = connection.execute(select(Company.__table__).where(Company.ticker == ticker)).mappings().first()
            if old and any(old[k] != v for k, v in metadata.items()):
                raise ValueError(f"Conflicting company metadata: {ticker}")
            connection.execute(insert(Company).values(**metadata).on_conflict_do_nothing(index_elements=["ticker"]))
        ids = dict(connection.execute(select(Company.ticker, Company.id)).all())
        connection.execute(text("CREATE TEMP TABLE price_stage (LIKE daily_prices INCLUDING CONSTRAINTS) ON COMMIT DROP"))
        raw = connection.connection.driver_connection
        with raw.cursor() as cursor, output.open(newline="", encoding="utf-8") as handle:
            with cursor.copy("COPY price_stage (company_id,date,open,high,low,close,adjusted_close,volume) FROM STDIN") as copy:
                for row in csv.DictReader(handle):
                    copy.write_row((ids[row["ticker"]], row["date"], *(row[f] for f in FIELDS[2:])))
        conflict = connection.execute(text("""
            SELECT 1 FROM price_stage s JOIN daily_prices p USING (company_id,date)
            WHERE (s.open,s.high,s.low,s.close,s.adjusted_close,s.volume)
              IS DISTINCT FROM (p.open,p.high,p.low,p.close,p.adjusted_close,p.volume) LIMIT 1
        """)).first()
        if conflict:
            raise ValueError("Existing prices differ in value; no data overwritten")
        inserted = connection.execute(text("INSERT INTO daily_prices SELECT * FROM price_stage ON CONFLICT (company_id,date) DO NOTHING")).rowcount
        result = {**summary, "inserted": inserted, "unchanged": summary["prices"] - inserted, "status": "completed"}
    return result
