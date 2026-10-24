"""Prepare a pinned Benjaminpo snapshot without changing the active price source."""

import csv
import hashlib
import io
import json
import time
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zipfile import ZipFile

from app.data_pipeline.source_files import ROOT, SELECTION, read_json, sha256
from app.data_pipeline.validation import FIELDS, PRICE_COLUMNS, validate_price

CANDIDATE = ROOT / "data/benjaminpo-v81.json"
RAW = ROOT / "data/raw/benjaminpo-v81"
OUTPUT = ROOT / "data/processed/benjaminpo-v81/prices-200.csv"
ALIASES = {"FI": "FISV", "MMC": "MRSH", "BK": "BNY"}
SOURCE = "https://www.kaggle.com/api/v1/datasets/download/benjaminpo/finance-dataset/"
MAX_FILE_BYTES = 5_000_000


def atomic_json(path, document):
    """Publish complete JSON only; an interrupted write leaves the old file intact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def candidate_manifest():
    manifest = read_json(CANDIDATE)
    if manifest.get("id") != "kaggle-benjaminpo-v81" or manifest.get("version") != 81:
        raise ValueError("Expected the pinned Benjaminpo dataset version 81")
    return manifest


def selected_tickers(manifest):
    if sha256(SELECTION) != manifest["selection_sha256"]:
        raise ValueError("Company selection checksum mismatch")
    tickers = sorted(row["Symbol"] for row in read_json(SELECTION)["companies"])
    if (
        len(set(tickers)) != manifest["company_count"]
        or len(tickers) != manifest["company_count"]
    ):
        raise ValueError("Unexpected company selection")
    return tickers


def source_name(ticker):
    return f"stocks_us/1d/{ALIASES.get(ticker, ticker)}.csv"


def download_url(ticker):
    return SOURCE + quote(source_name(ticker), safe="") + "?datasetVersionNumber=81"


def files_digest(files):
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def fetch_prices():
    """Cache only the selected daily files; resume without overwriting pinned bytes."""
    manifest = candidate_manifest()
    tickers = selected_tickers(manifest)
    RAW.mkdir(parents=True, exist_ok=True)
    progress_path = RAW / "download-index.json"
    progress = read_json(progress_path) if progress_path.exists() else {}
    pinned = manifest["files"]
    downloaded = 0

    for index, ticker in enumerate(tickers, 1):
        path = RAW / f"{ticker}.csv"
        entry = pinned.get(ticker) or progress.get(ticker)
        if path.exists():
            if not entry or sha256(path) != entry["sha256"]:
                raise ValueError(f"Cached source checksum mismatch: {ticker}")
        else:
            body = download_file(ticker)
            digest = hashlib.sha256(body).hexdigest()
            if entry and digest != entry["sha256"]:
                raise ValueError(f"Downloaded source checksum mismatch: {ticker}")
            entry = {
                "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                "source_name": source_name(ticker),
                "url": download_url(ticker),
                "sha256": digest,
                "bytes": len(body),
            }
            temporary = None
            try:
                with NamedTemporaryFile(dir=RAW, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(body)
                temporary.replace(path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
            downloaded += 1
        if progress.get(ticker) != entry:
            progress[ticker] = entry
            atomic_json(progress_path, progress)
        if index % 20 == 0:
            print(f"Cached {index}/{len(tickers)} Benjaminpo daily files", flush=True)

    files = {ticker: progress[ticker] for ticker in tickers}
    digest = files_digest(files)
    if manifest.get("sha256") and manifest["sha256"] != digest:
        raise ValueError(
            "Pinned Benjaminpo file catalogue differs from downloaded files"
        )
    manifest["files"] = files
    manifest["sha256"] = digest
    atomic_json(CANDIDATE, manifest)
    return {
        "status": "cached",
        "files": len(files),
        "downloaded": downloaded,
        "bytes": sum(entry["bytes"] for entry in files.values()),
        "sha256": digest,
    }


def download_file(ticker) -> bytes:
    for attempt in range(3):
        try:
            request = Request(
                download_url(ticker),
                headers={"User-Agent": "BeyondCandles historical data preparation"},
            )
            with urlopen(request, timeout=45) as response:
                body = response.read(MAX_FILE_BYTES + 1)
            if len(body) > MAX_FILE_BYTES:
                raise ValueError(f"Source file exceeds 5 MB: {ticker}")
            if body.startswith(b"PK"):
                with ZipFile(io.BytesIO(body)) as archive:
                    members = archive.infolist()
                    if len(members) != 1 or members[0].file_size > MAX_FILE_BYTES:
                        raise ValueError(f"Unexpected download archive: {ticker}")
                    body = archive.read(members[0])
            if not body.startswith(b"Datetime,Open,High,Low,Close,Adj Close,Volume"):
                raise ValueError(f"Unexpected CSV response: {ticker}")
            return body
        except (HTTPError, URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("Price download exhausted all attempts")


def load_company_rows(manifest):
    """Read the frozen catalogue snapshot without retaining the old price archive."""
    source = manifest["company_metadata_source"]
    path = ROOT / source["file"]
    if sha256(path) != source["sha256"]:
        raise ValueError("Company metadata checksum mismatch")
    selected = set(selected_tickers(manifest))
    frozen = {row["Symbol"]: row for row in read_json(SELECTION)["companies"]}
    companies = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["Symbol", "Longname", "Exchange", "Sector"]:
            raise ValueError("Unexpected company metadata columns")
        for row in reader:
            ticker = row["Symbol"]
            if ticker not in selected or ticker in companies:
                raise ValueError(f"Unexpected or duplicate company metadata: {ticker}")
            if not row["Longname"] or not row["Exchange"]:
                raise ValueError(f"Incomplete company metadata: {ticker}")
            if (
                row["Longname"] != frozen[ticker]["Longname"]
                or row["Sector"] != frozen[ticker]["Sector"]
            ):
                raise ValueError(
                    f"Company metadata differs from the frozen selection: {ticker}"
                )
            companies[ticker] = row
    if set(companies) != selected:
        raise ValueError("Incomplete company metadata catalogue")
    return companies


def read_history(ticker, manifest):
    entry = manifest["files"][ticker]
    path = ROOT / entry["file"]
    if path.resolve() != (RAW / f"{ticker}.csv").resolve():
        raise ValueError(f"Unexpected cached price path: {ticker}")
    if entry["source_name"] != source_name(ticker) or entry["url"] != download_url(
        ticker
    ):
        raise ValueError(f"Unexpected source identity: {ticker}")
    if sha256(path) != entry["sha256"]:
        raise ValueError(f"Source file checksum mismatch: {ticker}")
    rows = []
    dates = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["Datetime", *PRICE_COLUMNS, "Volume"]:
            raise ValueError(f"Unexpected source columns: {ticker}")
        for line, source in enumerate(reader, 2):
            # These daily timestamps are session-date labels at UTC midnight,
            # not instants to convert into the previous New York calendar day.
            stamp = datetime.strptime(source["Datetime"], "%Y-%m-%dT%H:%M:%S%z")
            if (
                stamp.utcoffset() != timedelta(0)
                or stamp.time().isoformat() != "00:00:00"
            ):
                raise ValueError(
                    f"Unexpected daily session timestamp: {ticker} line {line}"
                )
            day = stamp.date().isoformat()
            if not manifest["period_start"] <= day <= manifest["period_end"]:
                continue
            if day in dates:
                raise ValueError(f"Duplicate price: {ticker} {day}")
            try:
                values = validate_price(source)
            except ValueError as exc:
                raise ValueError(f"{ticker} {day} line {line}: {exc}") from exc
            dates.add(day)
            rows.append(dict(zip(FIELDS, [ticker, day, *values])))
    rows.sort(key=lambda row: row["date"])
    calendar = "\n".join(row["date"] for row in rows).encode("utf-8")
    calendar_matches = (
        hashlib.sha256(calendar).hexdigest() == manifest["calendar_sha256"]
    )
    if len(rows) != manifest["trading_dates"] or not calendar_matches:
        raise ValueError(f"Frozen calendar mismatch: {ticker}")
    return rows


def correct_amd(rows, manifest):
    """Replace one missing vendor bar using its reviewed, pinned OHLCV evidence."""
    review = manifest["reviews"]["AMD"]
    if (
        review["status"] != "reviewed"
        or review["method"] != "verified_quote_replacement"
        or review["date"] != "2015-01-02"
    ):
        raise ValueError("Unapproved AMD quote correction")
    matches = [row for row in rows if row["date"] == review["date"]]
    if len(matches) != 1 or any(row["ticker"] != "AMD" for row in rows):
        raise ValueError("AMD correction requires exactly one approved session")
    row = matches[0]
    expected = review["expected"]
    replacement = review["replacement"]
    if set(expected) != set(FIELDS[2:]) or set(replacement) != set(FIELDS[2:]):
        raise ValueError("AMD correction must specify all six numeric fields")
    if any(Decimal(row[field]) != Decimal(expected[field]) for field in expected):
        raise ValueError("AMD source differs from the reviewed missing quote")

    evidence = {reference["role"]: reference for reference in review["evidence"]}
    if set(evidence) != {"ohlcv", "independent_close"}:
        raise ValueError(
            "AMD correction requires quote and independent closing evidence"
        )
    for reference in evidence.values():
        if sha256(ROOT / reference["file"]) != reference["sha256"]:
            raise ValueError("AMD correction evidence checksum mismatch")
    response = read_json(ROOT / evidence["ohlcv"]["file"])
    quotes = response["rows"]
    if (
        response["query_execution_status"] != "Success"
        or len(quotes) != 1
        or quotes[0]["act_symbol"] != "AMD"
        or quotes[0]["date"] != review["date"]
    ):
        raise ValueError("AMD quote evidence has an unexpected issuer or session")
    quote = quotes[0]
    for field in ("open", "high", "low", "close", "volume"):
        if Decimal(quote[field]) != Decimal(replacement[field]):
            raise ValueError("AMD replacement differs from the pinned OHLCV quote")
    # The frozen AMD source has no dividend/split factor: every Adj Close equals Close.
    # A changed basis must be reviewed instead of applying this equality universally.
    if any(
        Decimal(item["adjusted_close"]) != Decimal(item["close"]) for item in rows
    ) or Decimal(replacement["adjusted_close"]) != Decimal(replacement["close"]):
        raise ValueError("AMD adjustment basis differs from the reviewed snapshot")
    numeric_prices = dict(
        zip(PRICE_COLUMNS, [replacement[field] for field in FIELDS[2:7]])
    )
    validate_price({**numeric_prices, "Volume": replacement["volume"]})
    row.update(replacement)
    return 1


def correct_avgo(rows, manifest):
    """Rebuild verified cash adjustments while retaining the vendor's final anchor."""
    from .price_adjustments import apply_adjustments

    review = manifest["reviews"]["AVGO"]
    policy_path = ROOT / review["rules_file"]
    if sha256(policy_path) != review["rules_sha256"]:
        raise ValueError("AVGO evidence policy checksum mismatch")
    policy = read_json(policy_path)
    if (
        policy["version"] != 1
        or policy["source_sha256"] != manifest["sha256"]
        or policy["selection_sha256"] != manifest["selection_sha256"]
        or policy["period_end"] != manifest["period_end"]
    ):
        raise ValueError("AVGO policy differs from the reviewed price snapshot")
    rule = policy["companies"]["AVGO"]
    if (
        review["status"] != "reviewed"
        or review["method"] != "cash_dividends_preserve_vendor_anchor"
        or rule["method"] != "cash_dividends"
    ):
        raise ValueError("Unapproved AVGO dividend correction")
    with localcontext() as context:
        context.prec = 50
        anchor = Decimal(rows[-1]["adjusted_close"]) / Decimal(rows[-1]["close"])
        corrected = apply_adjustments(rows, rule)
        changed = 0
        for row in rows:
            value = (corrected[row["date"]] * anchor).quantize(Decimal("1e-18"))
            changed += value != Decimal(row["adjusted_close"])
            row["adjusted_close"] = str(value)
    return changed


def prepare_prices(*, require_reviewed=False):
    """Validate all candidate bytes first; unresolved economic cases block replacement."""
    manifest = candidate_manifest()
    tickers = selected_tickers(manifest)
    if (
        set(manifest["files"]) != set(tickers)
        or files_digest(manifest["files"]) != manifest["sha256"]
    ):
        raise ValueError("Incomplete or changed Benjaminpo source catalogue")
    output = OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    changed = {}
    events = {}
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=output.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for ticker in tickers:
                rows = read_history(ticker, manifest)
                if ticker == "AMD":
                    changed[ticker] = correct_amd(rows, manifest)
                if ticker == "AVGO":
                    changed[ticker] = correct_avgo(rows, manifest)
                if ticker in ("DHR", "JCI"):
                    from .corporate_actions import correct_spin_off

                    review = manifest["reviews"][ticker]
                    before = event_summary(rows, review["ex_date"])
                    if review["status"] == "reviewed":
                        changed[ticker] = correct_spin_off(rows, review)
                    events[ticker] = {
                        "source": before,
                        "prepared": event_summary(rows, review["ex_date"]),
                    }
                for row in rows:
                    prices = dict(
                        zip(PRICE_COLUMNS, [row[field] for field in FIELDS[2:7]])
                    )
                    validate_price({**prices, "Volume": row["volume"]})
                writer.writerows(rows)
                count += len(rows)
            if count != manifest["price_rows"]:
                raise ValueError("Unexpected prepared price count")
        companies = load_company_rows(manifest)
        temporary.replace(output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    unresolved = {
        ticker: review
        for ticker, review in manifest["reviews"].items()
        if review["status"] != "reviewed"
    }
    result = {
        "status": "needs_review" if unresolved else "validated",
        "companies": len(tickers),
        "prices": count,
        "period_start": manifest["period_start"],
        "period_end": manifest["period_end"],
        "sha256": manifest["sha256"],
        "manifest_sha256": sha256(CANDIDATE),
        "processed_sha256": sha256(output),
        "adjustment_rules_sha256": sha256(CANDIDATE),
        "adjusted_prices_changed": changed,
        "unresolved": unresolved,
        "events": events,
    }
    atomic_json(output.parent / "validation.json", result)
    if require_reviewed and unresolved:
        raise ValueError(
            "Price replacement blocked by unresolved corporate actions: "
            + ", ".join(unresolved)
        )
    return output, companies, result


def event_summary(rows, ex_date):
    index = next(index for index, row in enumerate(rows) if row["date"] == ex_date)
    if index == 0:
        raise ValueError("Missing pre-distribution session")
    previous, current = rows[index - 1 : index + 1]
    with localcontext() as context:
        context.prec = 50
        close_return = Decimal(current["close"]) / Decimal(previous["close"]) - 1
        adjusted_return = (
            Decimal(current["adjusted_close"]) / Decimal(previous["adjusted_close"]) - 1
        )
        implied_cash = Decimal(previous["close"]) * (
            1
            - (Decimal(previous["adjusted_close"]) / Decimal(previous["close"]))
            / (Decimal(current["adjusted_close"]) / Decimal(current["close"]))
        )
    return {
        "previous": dict(previous),
        "current": dict(current),
        "close_return": str(close_return),
        "adjusted_return": str(adjusted_return),
        "implied_cash_on_source_basis": str(implied_cash),
    }
