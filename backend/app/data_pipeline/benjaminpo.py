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

from app.data_pipeline.source_files import (
    ROOT,
    SELECTION,
    read_json,
    sha256,
    write_json,
)
from app.data_pipeline.validation import FIELDS, PRICE_COLUMNS, validate_price

CANDIDATE = ROOT / "data/benjaminpo-v81.json"
RAW = ROOT / "data/raw/benjaminpo-v81"
OUTPUT = ROOT / "data/processed/benjaminpo-v81/prices-200.csv"
ALIASES = {"FI": "FISV", "MMC": "MRSH", "BK": "BNY"}
SOURCE = "https://www.kaggle.com/api/v1/datasets/download/benjaminpo/finance-dataset/"
MAX_FILE_BYTES = 5_000_000


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
            write_json(progress_path, progress, ensure_ascii=False)
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
    write_json(CANDIDATE, manifest, ensure_ascii=False)
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
            rows.append(dict(zip(FIELDS, [ticker, day, *values], strict=True)))
    rows.sort(key=lambda row: row["date"])
    source_dates = {row["date"] for row in rows}
    missing_dates = reviewed_missing_sessions(ticker, manifest)
    if missing_dates & source_dates:
        raise ValueError(f"Source now contains a reviewed missing session: {ticker}")
    # A reviewed quote insertion supplies its session later; the calendar must
    # match with exactly those sessions added.
    calendar_dates = sorted(source_dates | missing_dates)
    calendar = "\n".join(calendar_dates).encode("utf-8")
    calendar_matches = (
        hashlib.sha256(calendar).hexdigest() == manifest["calendar_sha256"]
    )
    if len(calendar_dates) != manifest["trading_dates"] or not calendar_matches:
        raise ValueError(f"Frozen calendar mismatch: {ticker}")
    return rows


# Vendor sessions corrected from pinned OHLCV evidence: a defective bar is replaced,
# a session absent from the vendor file is inserted. Each needs a reviewed manifest entry.
QUOTE_METHODS = ("verified_quote_replacement", "verified_quote_insertion")
QUOTE_FIELDS = ("open", "high", "low", "close", "volume")
# Spin-off reviews carry no method; corporate_actions.correct_spin_off applies them.
SPIN_OFF_REVIEWS = ("DHR",)


def review_corrections(manifest):
    """Name the correction that applies each manifest review.

    A review no correction applies would otherwise be skipped without a trace, so a
    reviewed quote for another company or an unknown kind of review stops preparation.
    """
    corrections = {}
    for ticker, review in manifest["reviews"].items():
        if review.get("method") in QUOTE_METHODS:
            corrections[ticker] = "quote"
        elif ticker == "AVGO":
            corrections[ticker] = "avgo_dividends"
        elif ticker in SPIN_OFF_REVIEWS:
            corrections[ticker] = "spin_off"
        else:
            raise ValueError(f"No price correction applies the review of {ticker}")
    return corrections


def reviewed_missing_sessions(ticker, manifest):
    """Sessions absent from the vendor file that a reviewed quote insertion supplies."""
    review = manifest["reviews"].get(ticker)
    if review and review.get("method") == "verified_quote_insertion":
        return {review["date"]}
    return set()


def quoted_session(reference, symbol, day):
    """Read one session's OHLCV from a pinned evidence response, by its format."""
    document = read_json(ROOT / reference["file"])
    evidence_format = reference["format"]
    if evidence_format == "dolthub_sql":
        if document["query_execution_status"] != "Success":
            raise ValueError("Quote evidence query did not succeed")
        sessions = [
            {field: quote[field] for field in QUOTE_FIELDS}
            for quote in document["rows"]
            if quote["act_symbol"] == symbol and quote["date"] == day
        ]
    elif evidence_format == "nasdaq_historical":
        data = document["data"]
        if data["symbol"] != symbol:
            raise ValueError(f"Quote evidence is for {data['symbol']}, not {symbol}")
        # Nasdaq labels sessions MM/DD/YYYY and prints prices as "$64.38".
        nasdaq_day = f"{day[5:7]}/{day[8:10]}/{day[:4]}"
        sessions = []
        for quote in data["tradesTable"]["rows"]:
            if quote["date"] != nasdaq_day:
                continue
            session = {}
            for field in QUOTE_FIELDS:
                session[field] = quote[field].replace("$", "").replace(",", "")
            sessions.append(session)
    elif evidence_format == "cboe_historical":
        if document["symbol"] != symbol:
            raise ValueError(
                f"Quote evidence is for {document['symbol']}, not {symbol}"
            )
        sessions = [
            {field: str(quote[field]) for field in QUOTE_FIELDS}
            for quote in document["data"]
            if quote["date"] == day
        ]
    else:
        raise ValueError(f"Unknown quote evidence format: {evidence_format}")
    if len(sessions) != 1:
        raise ValueError(f"Quote evidence has no single {symbol} session {day}")
    return {field: Decimal(value) for field, value in sessions[0].items()}


def correct_quote(rows, manifest, ticker):
    """Replace a defective vendor bar, or insert a missing one, from reviewed pinned evidence."""
    review = manifest["reviews"][ticker]
    method = review["method"]
    day = review["date"]
    if review["status"] != "reviewed" or method not in (
        "verified_quote_replacement",
        "verified_quote_insertion",
    ):
        raise ValueError(f"Unapproved {ticker} quote correction")
    if any(row["ticker"] != ticker for row in rows):
        raise ValueError(f"{ticker} correction received another company's session")
    matches = [row for row in rows if row["date"] == day]
    replacement = review["replacement"]
    if set(replacement) != set(FIELDS[2:]):
        raise ValueError(f"{ticker} correction must specify all six numeric fields")
    if method == "verified_quote_replacement":
        if len(matches) != 1:
            raise ValueError(
                f"{ticker} correction requires exactly one approved session"
            )
        expected = review["expected"]
        if set(expected) != set(FIELDS[2:]):
            raise ValueError(f"{ticker} correction must specify all six numeric fields")
        row = matches[0]
        if any(Decimal(row[field]) != Decimal(expected[field]) for field in expected):
            raise ValueError(f"{ticker} source differs from the reviewed missing quote")
    elif matches:
        raise ValueError(f"{ticker} source now contains the reviewed missing session")

    evidence = {reference["role"]: reference for reference in review["evidence"]}
    if set(evidence) != {"ohlcv", "independent_close"}:
        raise ValueError(
            f"{ticker} correction requires quote and independent closing evidence"
        )
    for reference in evidence.values():
        if sha256(ROOT / reference["file"]) != reference["sha256"]:
            raise ValueError(f"{ticker} correction evidence checksum mismatch")
    symbol = review["quoted_symbol"]
    quote = quoted_session(evidence["ohlcv"], symbol, day)
    for field in QUOTE_FIELDS:
        if quote[field] != Decimal(replacement[field]):
            raise ValueError(
                f"{ticker} replacement differs from the pinned OHLCV quote"
            )
    # A reviewed document (a PDF) is pinned by checksum only; a machine-readable
    # quote must also show the same closing price.
    if evidence["independent_close"]["format"] != "document":
        second = quoted_session(evidence["independent_close"], symbol, day)
        if second["close"] != Decimal(replacement["close"]):
            raise ValueError(
                f"{ticker} independent closing price differs from the replacement"
            )
    # The reviewed vendor files have no dividend/split factor: every Adj Close equals
    # Close. A changed basis must be reviewed instead of applying this equality.
    if any(
        Decimal(item["adjusted_close"]) != Decimal(item["close"]) for item in rows
    ) or Decimal(replacement["adjusted_close"]) != Decimal(replacement["close"]):
        raise ValueError(
            f"{ticker} adjustment basis differs from the reviewed snapshot"
        )
    numeric_prices = dict(
        zip(
            PRICE_COLUMNS,
            [replacement[field] for field in FIELDS[2:7]],
            strict=True,
        )
    )
    validate_price({**numeric_prices, "Volume": replacement["volume"]})
    if method == "verified_quote_replacement":
        matches[0].update(replacement)
    else:
        rows.append({"ticker": ticker, "date": day, **replacement})
        rows.sort(key=lambda row: row["date"])
    return 1


def cash_adjustment_policy(manifest):
    """Load the reviewed cash-adjustment rules of all companies.

    The AVGO review pins the whole file (path and SHA-256); the file also holds the
    dividends other companies' vendor histories omit.
    """
    review = manifest["reviews"]["AVGO"]
    policy_path = ROOT / review["rules_file"]
    if sha256(policy_path) != review["rules_sha256"]:
        raise ValueError("Cash-adjustment policy checksum mismatch")
    policy = read_json(policy_path)
    if (
        policy["version"] != 1
        or policy["source_sha256"] != manifest["sha256"]
        or policy["selection_sha256"] != manifest["selection_sha256"]
        or policy["period_end"] != manifest["period_end"]
    ):
        raise ValueError(
            "Cash-adjustment policy differs from the reviewed price snapshot"
        )
    return policy


def missing_dividend_tickers(policy, tickers):
    """List the companies with reviewed missing dividends; reject any other rule.

    In this list only AVGO's rebuilt dividends (applied by correct_avgo) and missing
    dividends of catalogue companies exist, so an unknown method or company is an error
    rather than a rule that silently applies to nothing.
    """
    result = []
    for ticker, rule in policy["companies"].items():
        if ticker == "AVGO" and rule["method"] == "cash_dividends":
            continue
        if rule["method"] != "missing_cash_dividends":
            raise ValueError(
                f"Unknown cash-adjustment method for {ticker}: {rule['method']}"
            )
        if ticker not in tickers:
            raise ValueError(
                f"Cash-adjustment rule for a company outside the catalogue: {ticker}"
            )
        result.append(ticker)
    return sorted(result)


def correct_missing_dividends(rows, policy, ticker):
    """Add reviewed dividends the vendor's Adjusted Close omits; return their number.

    Every amount must appear in the pinned evidence for the same symbol and ex-date.
    apply_adjustments refuses an ex-date where the vendor already has a factor change,
    so a dividend is never counted twice.
    """
    from .price_adjustments import apply_adjustments

    rule = policy["companies"][ticker]
    if rule["status"] != "reviewed" or rule["method"] != "missing_cash_dividends":
        raise ValueError(f"Unapproved {ticker} missing-dividend correction")
    evidence = rule["evidence"]
    if sha256(ROOT / evidence["file"]) != evidence["sha256"]:
        raise ValueError(f"{ticker} missing-dividend evidence checksum mismatch")
    document = read_json(ROOT / evidence["file"])
    if evidence["format"] != "dolthub_sql" or (
        document["query_execution_status"] != "Success"
    ):
        raise ValueError(f"{ticker} missing-dividend evidence is not a DoltHub result")
    listed = {}
    for item in document["rows"]:
        listed[(item["act_symbol"], item["ex_date"])] = Decimal(item["amount"])
    for dividend in rule["dividends"]:
        key = (ticker, dividend["ex_date"])
        if listed.get(key) != Decimal(dividend["amount"]):
            raise ValueError(
                f"{ticker} dividend {dividend['ex_date']} differs from the evidence"
            )
    corrected = apply_adjustments(rows, rule)
    for row in rows:
        # Sessions after the last added dividend keep their source text unchanged.
        if corrected[row["date"]] != Decimal(row["adjusted_close"]):
            row["adjusted_close"] = str(corrected[row["date"]])
    return len(rule["dividends"])


def dividend_change_tickers(policy, tickers):
    """List companies with reviewed vendor dividend changes; reject any other rule."""
    changes = policy.get("dividend_changes", {})
    for ticker, rule in changes.items():
        if rule["method"] != "reviewed_dividend_changes":
            raise ValueError(
                f"Unknown dividend change method for {ticker}: {rule['method']}"
            )
        if ticker not in tickers:
            raise ValueError(
                f"Dividend change rule for a company outside the catalogue: {ticker}"
            )
    return sorted(changes)


def correct_dividend_changes(rows, policy, ticker):
    """Fix dividends the vendor applied wrongly; return the number of changed events.

    Every file a review relies on is pinned, and apply_adjustments refuses an event the
    vendor's series does not show, so a review cannot apply to changed data.
    """
    from .price_adjustments import apply_adjustments

    rule = policy["dividend_changes"][ticker]
    if rule["status"] != "reviewed":
        raise ValueError(f"Unapproved {ticker} dividend change")
    if not rule["evidence"]:
        raise ValueError(f"{ticker} dividend change has no evidence")
    for evidence in rule["evidence"]:
        if sha256(ROOT / evidence["file"]) != evidence["sha256"]:
            raise ValueError(f"{ticker} dividend change evidence checksum mismatch")
    corrected = apply_adjustments(rows, rule)
    for row in rows:
        # Sessions after the last changed event keep their source text unchanged.
        if corrected[row["date"]] != Decimal(row["adjusted_close"]):
            row["adjusted_close"] = str(corrected[row["date"]])
    return len(rule["changes"])


def correct_avgo(rows, manifest):
    """Rebuild verified cash adjustments while retaining the vendor's final anchor."""
    from .price_adjustments import apply_adjustments

    review = manifest["reviews"]["AVGO"]
    policy = cash_adjustment_policy(manifest)
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


def adjustment_rules_sha256(manifest):
    """Hash the reviewed cash-adjustment rules file, not the manifest that pins it."""
    review = manifest["reviews"].get("AVGO")
    if review is None:
        return None
    return sha256(ROOT / review["rules_file"])


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
    added_dividends = {}
    corrections = review_corrections(manifest)
    policy = None
    dividend_tickers = []
    change_tickers = []
    changed_dividends = {}
    if "AVGO" in manifest["reviews"]:
        policy = cash_adjustment_policy(manifest)
        dividend_tickers = missing_dividend_tickers(policy, tickers)
        change_tickers = dividend_change_tickers(policy, tickers)
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
                if corrections.get(ticker) == "quote":
                    changed[ticker] = correct_quote(rows, manifest, ticker)
                if ticker == "AVGO":
                    changed[ticker] = correct_avgo(rows, manifest)
                if ticker in SPIN_OFF_REVIEWS:
                    from .corporate_actions import correct_spin_off

                    review = manifest["reviews"][ticker]
                    before = event_summary(rows, review["ex_date"])
                    if review["status"] == "reviewed":
                        changed[ticker] = correct_spin_off(rows, review)
                    events[ticker] = {
                        "source": before,
                        "prepared": event_summary(rows, review["ex_date"]),
                    }
                if ticker in dividend_tickers:
                    added_dividends[ticker] = correct_missing_dividends(
                        rows, policy, ticker
                    )
                if ticker in change_tickers:
                    changed_dividends[ticker] = correct_dividend_changes(
                        rows, policy, ticker
                    )
                for row in rows:
                    prices = dict(
                        zip(
                            PRICE_COLUMNS,
                            [row[field] for field in FIELDS[2:7]],
                            strict=True,
                        )
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
        "adjustment_rules_sha256": adjustment_rules_sha256(manifest),
        "adjusted_prices_changed": changed,
        "missing_dividends_added": added_dividends,
        "vendor_dividends_changed": changed_dividends,
        "unresolved": unresolved,
        "events": events,
    }
    write_json(output.parent / "validation.json", result, ensure_ascii=False)
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
