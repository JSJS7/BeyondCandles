"""Review annual parent equity against pinned SEC balance/equity statements."""

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, InvalidOperation
from threading import Event, Lock
from time import monotonic, sleep
from typing import Any

from app.data_pipeline.equity_exceptions import manual_choice, reviewed_nci_controls
from app.data_pipeline.equity_facts import instant_equity_positions
from app.data_pipeline.identifiers import cached, write_json
from app.data_pipeline.revenue import (
    EVIDENCE_INDEX as INCOME_EVIDENCE,
)
from app.data_pipeline.sec import CACHE
from app.data_pipeline.sec_catalogue import identifiers, load_all_sec, verify_reference
from app.data_pipeline.sec_history import (
    HISTORY_INDEX,
    prepare_company_facts,
)
from app.data_pipeline.sec_statements import StatementRows, normalized_label
from app.data_pipeline.source_files import ROOT, SELECTION, read_json, sha256
from app.equity import (
    HISTORY_FALLBACKS,
    RULES_PATH,
    fact_identity,
    period_issuer,
    variants_fingerprint,
)
from app.fiscal_periods import canonical_period, fiscal_rules

EVIDENCE_INDEX = CACHE / "equity-evidence-index.json"
COMPONENT_TAGS = (
    "MinorityInterest",
    "NonredeemableNoncontrollingInterest",
    "MinorityInterestInOperatingPartnerships",
)
PARENT_TAGS = ("StockholdersEquity", "PartnersCapital")
GROUP_TAG = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
# These precise historical facts are not whole-equity balances. All originals remain stored.
REJECTED_FACTS = {
    ("0000008670", "2018-06-30", "0000008670-21-000027", GROUP_TAG): "-679800000",
    ("0000008670", "2019-06-30", "0000008670-21-000027", GROUP_TAG): "-257300000",
    ("0000899051", "2020-12-31", "0000899051-24-000013", GROUP_TAG): "-298000000",
}


def statement_files(summary, kind):
    """Use face statements, excluding notes and parenthetical disclosures."""
    files = []
    for report in ET.fromstring(summary).iter("Report"):
        name = (report.findtext("ShortName") or "").lower()
        category = report.findtext("MenuCategory")
        if kind == "scope":
            if category == "Tables" and any(
                term in name
                for term in (
                    "changes in equity",
                    "total equity",
                    "stockholders",
                    "shareholders",
                )
            ):
                filename = report.findtext("HtmlFileName") or ""
                if re.fullmatch(r"R\d+[.]htm", filename):
                    files.append(filename)
            continue
        statement = category == "Statements"
        if category in (None, "", "Uncategorized"):
            statement = " - Statement - " in (report.findtext("LongName") or "")
        if not statement or "parenthetical" in name:
            continue
        balance = any(
            term in name
            for term in ("balance", "financial position", "financial condition")
        )
        equity = any(
            term in name
            for term in (
                "equity",
                "equities",
                "stockholder",
                "shareholder",
                "shareowner",
                "partners' capital",
            )
        )
        if (kind == "balance" and not balance) or (
            kind == "equity" and (not equity or balance)
        ):
            continue
        filename = report.findtext("HtmlFileName") or ""
        if re.fullmatch(r"R\d+[.]htm", filename):
            files.append(filename)
    return files


def cache_evidence(jobs, evidence):
    """Fetch four filings concurrently, with a shared four-requests/second limit."""
    previous_income = read_json(INCOME_EVIDENCE) if INCOME_EVIDENCE.exists() else {}
    gate, checkpoint, stopped = Lock(), Lock(), Event()
    last_request = [0.0]

    def download(url, previous):
        if previous and previous.get("url") == url:
            try:
                verify_reference(previous)
                return previous
            except (OSError, ValueError):
                pass
        with gate:
            if stopped.is_set():
                raise ValueError("SEC downloads stopped after an access denial")
            sleep(max(0, last_request[0] + 0.25 - monotonic()))
            last_request[0] = monotonic()
        try:
            return cached(url, json_response=False)
        except ValueError as exc:
            if "HTTP 403" in str(exc):
                stopped.set()
            raise

    def filing(job):
        source, kind = job
        cik, accession = source.split(":")
        entry = evidence.get(source, {}).copy()
        income = previous_income.get(source, {})
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
        try:
            entry["summary"] = download(
                base + "FilingSummary.xml", entry.get("summary", income.get("summary"))
            )
            summary = verify_reference(entry["summary"]).read_bytes()
            if kind == "instance":
                from app.data_pipeline.revenue_facts import cache_instance

                with gate:
                    if stopped.is_set():
                        raise ValueError("SEC downloads stopped after an access denial")
                    sleep(max(0, last_request[0] + 0.5 - monotonic()))
                    cache_instance(cik, accession, entry)
                    last_request[0] = monotonic()
                files = []
            else:
                files = statement_files(summary, kind)
                entry[kind + "_files"] = files
            for filename in files:
                entry[filename] = download(
                    base + filename, entry.get(filename, income.get(filename))
                )
            entry.pop("download_issue", None)
        except (ValueError, ET.ParseError) as exc:
            if "HTTP 403" in str(exc):
                stopped.set()
            if stopped.is_set():
                raise
            entry["download_issue"] = str(exc)
        with checkpoint:
            evidence[source] = {**evidence.get(source, {}), **entry}

    print(
        f"Equity evidence: {len(jobs)} filing/statement groups (verified cache is reused)",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(filing, job) for job in sorted(jobs)]
        try:
            for done, future in enumerate(as_completed(futures), 1):
                future.result()
                if done % 25 == 0 or done == len(jobs):
                    with checkpoint:
                        write_json(EVIDENCE_INDEX, {"version": 1, "filings": evidence})
                    print(f"Equity evidence: {done}/{len(jobs)}", flush=True)
        except Exception:
            stopped.set()
            for future in futures:
                future.cancel()
            write_json(EVIDENCE_INDEX, {"version": 1, "filings": evidence})
            raise


def expand(cells, spans):
    result = []
    for cell, span in zip(cells, spans):
        result.extend([cell] + [""] * (span - 1))
    return result


def parse_date(text):
    match = re.search(r"([A-Za-z]+[.]? \d{1,2}, \d{4})", text)
    if not match:
        return None
    for pattern in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(match.group(1).replace(".", ""), pattern).date()
        except ValueError:
            continue
    return None


def numeric(text, scale):
    text = re.sub(r"\[[^]]*\]", "", text)
    text = re.sub(r"[$,\s]", "", text).replace("(", "-").replace(")", "")
    if text == "":
        return None
    if text in ("-", "\u2014", "\u2013"):
        return Decimal(0)
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number * scale


def parent_column(label):
    label = normalized_label(label)
    if any(
        term in label
        for term in (
            "noncontrolling",
            "non controlling",
            "preferred",
            "common",
            "cumulative effect",
            "adjustment",
            "revision of prior period",
            "previously reported",
            "effect of adoption",
        )
    ):
        return False
    if label in ("parent", "parent member"):
        return True
    return any(
        term in label
        for term in (
            "stockholders equity",
            "shareholders equity",
            "shareowners equity",
            "total disney equity",
        )
    )


PARENT_ACCOUNTS = {
    "common stock": {"common"},
    "common shares": {"common"},
    "paid in surplus": {"paid_in"},
    "common stock issued amount": {"common"},
    "common stock and additional paid in capital": {"common", "paid_in"},
    "common stock and paid in capital": {"common", "paid_in"},
    "common stock including additional paid in capital": {"common", "paid_in"},
    "additional paid in capital": {"paid_in"},
    "other capital": {"paid_in"},
    "capital in excess of par value": {"paid_in"},
    "retained earnings": {"retained"},
    "retained earnings accumulated deficit": {"retained"},
    "retained earnings and additional paid in capital": {"retained", "paid_in"},
    "accumulated income": {"retained"},
    "accumulated deficit": {"retained"},
    "accumulated deficit retained earnings": {"retained"},
    "accumulated other comprehensive income loss": {"aoci"},
    "accumulated other comprehensive income loss net": {"aoci"},
    "accumulated other comprehensive income aoci": {"aoci"},
    "accumulated other comprehensive loss": {"aoci"},
    "cumulative other comprehensive loss": {"aoci"},
    "accumulated other comprehensive income": {"aoci"},
    "treasury stock": {"treasury"},
    "treasury stock common": {"treasury"},
    "treasury shares at cost": {"treasury"},
    "treasury stock at cost": {"treasury"},
    "treasury stock amount": {"treasury"},
    "preferred stock": {"preferred"},
    "right to recover for covered losses": {"covered_losses"},
}


def closed_parent_accounts(columns, values, total_column, scale):
    """Prove a total equals disjoint parent accounts; an absent NCI is never filled with zero."""
    roles = set()
    controls = []
    for column, title in enumerate(columns):
        if column == 0 or column == total_column or column >= len(values):
            continue
        amount = numeric(values[column], scale)
        if amount is None:
            continue
        title_key = normalized_label(title).replace(" member", "")
        if (
            "cumulative effect" in title_key
            or "adoption of new accounting" in title_key
            or "revision of prior period" in title_key
            or "redeemable noncontrolling interest" in title_key
        ):
            # Separately displayed adoption changes are not another ownership balance.
            continue
        account_roles = PARENT_ACCOUNTS.get(title_key)
        if not account_roles or roles & account_roles:
            return None
        roles.update(account_roles)
        controls.append({"column": title, "value": format(amount, "f")})
    total = numeric(values[total_column], scale)
    if (
        len(roles) < 2
        or sum((Decimal(item["value"]) for item in controls), Decimal(0)) != total
    ):
        return None
    return controls


def nci_column_indices(columns):
    """Prefer overall permanent NCI to subsidiary or adoption-adjustment breakdowns."""
    labels = [
        normalized_label(title).replace(" member", "").removesuffix(" balance")
        for title in columns
    ]
    generic = {
        index
        for index, label in enumerate(labels)
        if label
        in (
            "noncontrolling interests",
            "noncontrolling interest",
            "non controlling interests",
        )
    }
    if generic:
        return generic
    return {
        index
        for index, label in enumerate(labels)
        if ("noncontrolling" in label or "non controlling" in label)
        and "cumulative effect" not in label
        and (
            "redeemable" not in label
            or "nonredeemable" in label
            or "non redeemable" in label
        )
    }


def statement_nci_controls(html, reference):
    """Confirm permanent NCI columns without inferring a balance date from rendered layout."""
    parser = StatementRows()
    parser.feed(html)
    headers = [row for row in parser.rows if row["header"]]
    text = " ".join(cell for row in headers for cell in row["cells"]).lower()
    if "usd" not in text:
        return []
    match = re.search(r"\$\s+in\s+(thousands|millions|billions)", text)
    scale = {"thousands": 1000, "millions": 1000000, "billions": 1000000000}.get(
        match.group(1) if match else "", 1
    )
    controls = []
    for header in headers:
        columns = expand(header["cells"], header["spans"])
        for column in nci_column_indices(columns):
            for row in parser.rows:
                if row["header"] or row["tags"] != {("us-gaap", GROUP_TAG)}:
                    continue
                values = expand(row["cells"], row["spans"])
                if column >= len(values):
                    continue
                value = numeric(values[column], scale)
                if value is not None:
                    controls.append(
                        {
                            "column": columns[column],
                            "value": format(value, "f"),
                            "statement": reference,
                        }
                    )
    return controls


def statement_positions(html, end, kind):
    """Read date columns or dated equity roll-forwards; retain explicit parent/group scope."""
    parser = StatementRows()
    parser.feed(html)
    headers = [row for row in parser.rows if row["header"]]
    text = " ".join(cell for row in headers for cell in row["cells"]).lower()
    if "usd" not in text:
        raise ValueError("Statement currency is not explicitly USD")
    match = re.search(r"\$\s+in\s+(thousands|millions|billions)", text)
    scale = {"thousands": 1000, "millions": 1000000, "billions": 1000000000}.get(
        match.group(1) if match else "", 1
    )
    date_columns = {}
    owner_columns = []
    for row in headers:
        offset = (
            headers[0]["spans"][0]
            if row["cells"] and parse_date(row["cells"][0])
            else 0
        )
        for cell, span in zip(row["cells"], row["spans"]):
            parsed = parse_date(cell)
            if parsed:
                for column in range(offset, offset + span):
                    date_columns[column] = parsed
            offset += span
        if kind == "equity" and not any(parse_date(cell) for cell in row["cells"]):
            owner_columns = expand(row["cells"], row["spans"])
    nci_columns = nci_column_indices(owner_columns)
    positions = []
    current_date = None
    for row in parser.rows:
        if (
            kind == "balance"
            and positions
            and any(tag == "LiabilitiesAndStockholdersEquity" for _, tag in row["tags"])
        ):
            break
        row_start = len(positions)
        if row["header"] or not row["cells"]:
            continue
        if any(tag.endswith("Axis") for _, tag in row["tags"]):
            # Later blocks describe individual equity accounts or subsidiaries.
            break
        label = row["cells"][0]
        row_date = parse_date(label)
        if row_date:
            current_date = row_date
        elif any(
            term in label.lower()
            for term in ("adoption", "cumulative effect", "accounting standards update")
        ):
            continue
        if "[member]" in label.lower():
            continue
        tags = row["tags"]
        if len(tags) != 1:
            continue
        taxonomy, tag = next(iter(tags))
        if (
            taxonomy != "us-gaap"
            or tag not in PARENT_TAGS + (GROUP_TAG,) + COMPONENT_TAGS
        ):
            continue
        values = expand(row["cells"], row["spans"])
        owners = any(
            normalized_label(title) in ("total", "total equity") or parent_column(title)
            for title in owner_columns[1:]
        )
        horizontal = kind == "balance" or (date_columns and not owners)
        if horizontal:
            if "beginning" in label.lower():
                continue
            if tag in PARENT_TAGS:
                title = normalized_label(label)
                equity_label = any(
                    word in title
                    for word in (
                        "stockholder",
                        "shareholder",
                        "shareowner",
                        "partners capital",
                        "partners equity",
                    )
                )
                equity_label = (
                    equity_label
                    or bool(re.fullmatch(r"total .+ equity", title))
                    or title == "parent equity"
                )
                if (
                    not equity_label
                    or "liabilities" in title
                    or "noncontrolling" in title
                ):
                    continue
            columns = [
                column
                for column, day in date_columns.items()
                if day == end and column < len(values)
            ]
            amounts = [numeric(values[column], scale) for column in columns]
            amounts = [amount for amount in amounts if amount is not None]
            if len(amounts) != 1:
                continue
            amount = amounts[0]
        else:
            if current_date != end:
                continue
            if tag in COMPONENT_TAGS:
                for column in nci_columns:
                    if column < len(values):
                        minority = numeric(values[column], scale)
                        if minority is not None:
                            positions.append(
                                {
                                    "scope": "minority",
                                    "value": minority,
                                    "tag": tag,
                                    "label": label,
                                    "column": owner_columns[column],
                                    "layout": "rollforward",
                                }
                            )
                continue
            if tag == GROUP_TAG:
                for column, title in enumerate(owner_columns):
                    if column and column < len(values) and parent_column(title):
                        parent = numeric(values[column], scale)
                        if parent is not None:
                            positions.append(
                                {
                                    "scope": "parent",
                                    "value": parent,
                                    "tag": tag,
                                    "label": label,
                                    "column": title,
                                    "ambiguous_scope": normalized_label(title)
                                    .replace(" member", "")
                                    .replace(" deficit", "")
                                    in (
                                        "stockholders equity",
                                        "shareholders equity",
                                        "total stockholders equity",
                                        "total shareholders equity",
                                    ),
                                }
                            )
                    if column in nci_columns and column < len(values):
                        minority = numeric(values[column], scale)
                        if minority is not None:
                            positions.append(
                                {
                                    "scope": "minority",
                                    "value": minority,
                                    "tag": tag,
                                    "label": label,
                                    "column": title,
                                }
                            )
            total_columns = [
                column
                for column, title in enumerate(owner_columns)
                if column
                and column < len(values)
                and (
                    normalized_label(title)
                    in ("total", "total equity", "total capital")
                    or parent_column(title)
                )
            ]
            amount = numeric(values[total_columns[0]], scale) if total_columns else None
            if amount is None:
                continue
        source_column = str(end) if horizontal else owner_columns[total_columns[0]]
        if not horizontal and tag == GROUP_TAG:
            controls = closed_parent_accounts(
                owner_columns, values, total_columns[0], scale
            )
            if controls:
                positions.append(
                    {
                        "scope": "parent",
                        "value": amount,
                        "tag": tag,
                        "label": label,
                        "column": source_column,
                        "scope_control": controls,
                    }
                )
        scope = (
            "parent"
            if tag in PARENT_TAGS
            else "group"
            if tag == GROUP_TAG
            else "minority"
        )
        positions.append(
            {
                "scope": scope,
                "value": amount,
                "tag": tag,
                "label": label,
                "column": source_column,
            }
        )
        for position in positions[row_start:]:
            position["layout"] = "balance" if horizontal else "rollforward"

    return positions


def rejected(fact):
    key = (fact["source_cik"], str(fact["period_end"]), fact["accession"], fact["tag"])
    expected = REJECTED_FACTS.get(key)
    return expected is not None and fact["value"] == Decimal(expected)


def select_components(positions, facts, cik, accession, end, calendar, primary_cik):
    """Select an explicit parent position, or deduct a separately reported total NCI."""
    available = [
        fact
        for fact in facts
        if fact["metric"] == "equity"
        and canonical_period(fact, calendar)[1] == end
        and (
            fact.get("history_eligible", True)
            or HISTORY_FALLBACKS.get((primary_cik, str(end))) == fact["source_cik"]
        )
        and not rejected(fact)
    ]
    anchors = [
        fact
        for fact in available
        if fact["source_cik"] == cik and fact["accession"] == accession
    ]
    if not anchors:
        raise ValueError("No stored source anchor for this statement and date")
    anchor = max(anchors, key=lambda fact: fact["tag"] == GROUP_TAG)

    def reported_position(position):
        return dict(
            anchor,
            tag=position["tag"],
            value=position["value"],
            statement_label=position["label"],
            statement_column=position["column"],
            _statement_position=True,
            _scope_control=position.get("scope_control"),
        )

    parents = []
    for position in positions:
        if position["scope"] != "parent":
            continue
        if position.get("ambiguous_scope"):
            exact_parent = any(
                fact["tag"] in PARENT_TAGS and fact["value"] == position["value"]
                for fact in anchors
            )
            reconciled = any(
                group["scope"] == "group"
                and minority["scope"] == "minority"
                and group["value"] - minority["value"] == position["value"]
                and (
                    minority.get("layout") == "balance"
                    or minority["label"] == group["label"]
                    or minority["tag"] in COMPONENT_TAGS
                )
                for group in positions
                for minority in positions
            )
            if not exact_parent and not reconciled:
                continue
        parents.append(position)
    if len({position["value"] for position in parents}) > 1:
        raise ValueError("Conflicting parent-equity scopes in the same statement")
    if parents:
        amount = parents[0]["value"]
        matching = [
            fact
            for fact in available
            if fact["source_cik"] == cik
            and fact["tag"] in PARENT_TAGS
            and fact["value"] == amount
        ]
        exact = [fact for fact in matching if fact["accession"] == accession]
        if exact or matching:
            source = max(
                exact or matching, key=lambda fact: (fact["filed"], fact["accession"])
            )
        else:
            source = reported_position(parents[0])
        return [source], [1], amount, None

    groups = [position for position in positions if position["scope"] == "group"]
    if len({position["value"] for position in groups}) != 1:
        raise ValueError("No unique group-equity position for this date")
    group_sources = [
        fact
        for fact in anchors
        if fact["tag"] == GROUP_TAG and fact["value"] == groups[0]["value"]
    ]
    if len(group_sources) != 1:
        raise ValueError("Reported group equity does not match its source fact")
    minorities = [
        position
        for position in positions
        if position["scope"] == "minority"
        and (
            position.get("layout") == "balance"
            or position["label"] == groups[0]["label"]
            or position["tag"] in COMPONENT_TAGS
        )
    ]
    unique = {
        (
            position["tag"],
            position["label"],
            position["column"],
            position["value"],
        ): position
        for position in minorities
    }
    minorities = list(unique.values())
    if len(minorities) != 1:
        raise ValueError("No unique explicitly reported total noncontrolling interest")
    minority = reported_position(minorities[0])
    amount = group_sources[0]["value"] - minority["value"]
    return (
        [group_sources[0], minority],
        [1, -1],
        amount,
        "Group equity - noncontrolling interests",
    )


def equity_tasks():
    """Build review periods from the frozen source cache without using prior approvals."""
    from app.annual_financials import annual_periods

    issuers = identifiers()
    history = read_json(HISTORY_INDEX) if HISTORY_INDEX.exists() else {"issuers": {}}
    calendar = fiscal_rules()
    tasks = []
    _, companies = load_all_sec()
    for ticker, (entry, body) in companies:
        facts = prepare_company_facts(
            entry, body, issuers[ticker].get("history"), history
        )
        for period in annual_periods(facts, equity_policy={}):
            primary = period_issuer(
                facts, period.period_start, period.period_end, calendar
            )
            variants = period.metrics["equity"].variants
            # A prior approval may append calculation components; only normal candidates define the snapshot.
            variants = [
                variant for variant in variants if variant.tag not in COMPONENT_TAGS
            ]
            eligible = [
                fact
                for fact in facts
                if fact["metric"] == "equity"
                and fact["history_eligible"]
                and canonical_period(fact, calendar)[1] == period.period_end
                and not rejected(fact)
            ]
            if not eligible:
                fallback = HISTORY_FALLBACKS.get(
                    (primary or "", str(period.period_end))
                )
                eligible = [
                    fact
                    for fact in facts
                    if fact["metric"] == "equity"
                    and fact["source_cik"] == fallback
                    and canonical_period(fact, calendar)[1] == period.period_end
                ]
            latest = max((fact["filed"] for fact in eligible), default=None)
            sources = {
                (fact["source_cik"], fact["accession"], fact["filed"])
                for fact in eligible
                if fact["filed"] == latest
            }
            tasks.append(
                {
                    "ticker": ticker,
                    "primary": primary,
                    "period": period,
                    "variants": variants,
                    "facts": facts,
                    "sources": sources,
                }
            )
    return tasks, calendar


def review_equity(fetch=False):
    """Prepare exact period approvals; unresolved positions stay visible without invented zeros."""
    tasks, calendar = equity_tasks()
    evidence = (
        read_json(EVIDENCE_INDEX).get("filings", {}) if EVIDENCE_INDEX.exists() else {}
    )
    jobs = set()
    for task in tasks:
        for cik, accession, filed in task["sources"]:
            kind = (
                "equity"
                if (filed - task["period"].period_end).days > 450
                else "balance"
            )
            jobs.add((cik + ":" + accession, kind))
    if fetch:
        cache_evidence(jobs, evidence)
    policy = {
        "version": 1,
        "selection_sha256": sha256(SELECTION),
        "definition": "Parent owners' equity, including preferred equity, excluding NCI.",
        "method": "Match exact reported positions, dates, units and source facts; retain all original alternatives.",
        "periods": {},
        "unresolved": {},
    }
    report = {"status": "reviewed", "companies": {}, "totals": {}}
    alternative_jobs = set()
    for task in tasks:
        period = task["period"]
        end = period.period_end
        key = f"{task['primary']}:{end}"
        choices, issues = [], []
        visible_parents = set()
        for cik, accession, filed in sorted(task["sources"]):
            source = cik + ":" + accession
            entry = evidence.get(source, {})
            found = False
            source_choices, instance_choices = [], []
            for kind in ("balance", "equity", "instance"):
                filenames = (
                    ["instance"]
                    if kind == "instance" and "instance" in entry
                    else entry.get(kind + "_files", [])
                )
                for filename in filenames:
                    if filename not in entry:
                        continue
                    try:
                        path = verify_reference(entry[filename])
                        if kind == "instance":
                            nci_controls = []
                            statement_files = entry.get("equity_files", []) + entry.get(
                                "scope_files", []
                            )
                            for statement_file in statement_files:
                                if statement_file not in entry:
                                    continue
                                reference = entry[statement_file]
                                body = verify_reference(reference).read_text(
                                    encoding="utf-8"
                                )
                                nci_controls.extend(
                                    statement_nci_controls(body, reference)
                                )
                            nci_controls.extend(
                                reviewed_nci_controls(task, cik, accession)
                            )
                            positions = instant_equity_positions(
                                path.read_bytes(), cik, end, nci_controls=nci_controls
                            )
                        else:
                            positions = statement_positions(
                                path.read_text(encoding="utf-8"), end, kind
                            )
                        visible_parents.update(
                            position["value"]
                            for position in positions
                            if position["scope"] == "parent"
                        )
                        selected, coefficients, value, calculation = select_components(
                            positions,
                            task["facts"],
                            cik,
                            accession,
                            end,
                            calendar,
                            task["primary"],
                        )
                        for fact in selected:
                            if fact.get("_statement_position"):
                                fact["statement_url"] = entry[filename]["url"]
                        anchor = next(
                            fact
                            for fact in task["facts"]
                            if fact["metric"] == "equity"
                            and fact["source_cik"] == cik
                            and fact["accession"] == accession
                            and canonical_period(fact, calendar)[1] == end
                            and not rejected(fact)
                        )
                        destination = (
                            instance_choices if kind == "instance" else source_choices
                        )
                        destination.append(
                            {
                                "anchor": fact_identity(anchor),
                                "scope_controls": [
                                    fact["_scope_control"]
                                    for fact in selected
                                    if fact.get("_scope_control")
                                ],
                                "observed_sha256": variants_fingerprint(
                                    task["variants"]
                                ),
                                "value": format(value, "f"),
                                "filed": str(filed),
                                "accession": accession,
                                "components": [
                                    {
                                        "fact": fact_identity(fact),
                                        "coefficient": coefficient,
                                        **(
                                            {"statement_position": True}
                                            if fact.get("_statement_position")
                                            else {}
                                        ),
                                        **(
                                            {"history_fallback": True}
                                            if not fact["history_eligible"]
                                            else {}
                                        ),
                                    }
                                    for fact, coefficient in zip(selected, coefficients)
                                ],
                                "calculation": calculation,
                                "note": "Verified against the pinned equity position. Additional statement columns retain their original row concept and column label; source facts and alternative definitions are retained.",
                                "summary": entry["summary"],
                                "statement": entry[filename],
                            }
                        )
                        found = True
                    except (OSError, ValueError, ET.ParseError) as exc:
                        issues.append(str(exc))
            if instance_choices:
                # The original XBRL retains exact dates omitted by some SEC HTML renders.
                # An explicit parent fact also takes precedence over rounded group-minus-NCI.
                choices.extend(instance_choices)
            else:
                choices.extend(source_choices)
            if not found:
                for kind in ("balance", "equity"):
                    if kind + "_files" not in entry:
                        alternative_jobs.add((source, kind))
                issues.append(
                    entry.get(
                        "download_issue",
                        "No verified parent-equity position in cached face statements",
                    )
                )
        if not choices or len({Decimal(choice["value"]) for choice in choices}) > 1:
            for cik, accession, _ in task["sources"]:
                source = cik + ":" + accession
                if "instance" not in evidence.get(source, {}):
                    alternative_jobs.add((source, "instance"))
        if not choices:
            current_dates = {filed for _, _, filed in task["sources"]}
            current_parent: list[dict[str, Any]] = [
                fact
                for fact in task["facts"]
                if fact["metric"] == "equity"
                and fact["history_eligible"]
                and fact["tag"] in PARENT_TAGS
                and fact["filed"] in current_dates
                and canonical_period(fact, calendar)[1] == end
            ]
            amounts = {fact["value"] for fact in current_parent}
            if len(amounts) == 1 and not (visible_parents - amounts):
                current = max(
                    current_parent, key=lambda fact: (fact["filed"], fact["accession"])
                )
                older = [
                    fact
                    for fact in task["facts"]
                    if fact["metric"] == "equity"
                    and fact["history_eligible"]
                    and fact["source_cik"] == current["source_cik"]
                    and fact["tag"] in PARENT_TAGS
                    and fact["value"] == current["value"]
                    and fact["form"]
                    in ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
                    and fact["filed"] <= current["filed"]
                    and 0 <= (fact["filed"] - end).days <= 450
                    and canonical_period(fact, calendar)[1] == end
                ]
                if older:
                    old = max(
                        older, key=lambda fact: (fact["filed"], fact["accession"])
                    )
                    source = old["source_cik"] + ":" + old["accession"]
                    entry = evidence.get(source, {})
                    if "balance_files" not in entry:
                        alternative_jobs.add((source, "balance"))
                    for filename in entry.get("balance_files", []):
                        if filename not in entry:
                            continue
                        try:
                            positions = statement_positions(
                                verify_reference(entry[filename]).read_text(
                                    encoding="utf-8"
                                ),
                                end,
                                "balance",
                            )
                        except (OSError, ValueError) as exc:
                            issues.append(str(exc))
                            continue
                        parent_amounts = {
                            position["value"]
                            for position in positions
                            if position["scope"] == "parent"
                        }
                        if parent_amounts == {current["value"]}:
                            choices.append(
                                {
                                    "observed_sha256": variants_fingerprint(
                                        task["variants"]
                                    ),
                                    "value": format(current["value"], "f"),
                                    "filed": str(current["filed"]),
                                    "accession": current["accession"],
                                    "anchor": fact_identity(current),
                                    "components": [
                                        {
                                            "fact": fact_identity(current),
                                            "coefficient": 1,
                                        }
                                    ],
                                    "calculation": None,
                                    "note": "The latest parent-equity fact is unchanged from the explicit parent position on an earlier balance sheet for the same day. Both filing versions are pinned; a new snapshot requires review.",
                                    "summary": entry["summary"],
                                    "statement": entry[filename],
                                }
                            )
        if not choices:
            try:
                manual = manual_choice(task, calendar, fetch=fetch)
                if manual:
                    choices.append(manual)
            except (OSError, ValueError) as exc:
                issues.append(str(exc))
        if choices and len({Decimal(choice["value"]) for choice in choices}) == 1:
            policy["periods"][key] = min(
                choices, key=lambda choice: len(choice["components"])
            )
        else:
            conflicting = any("Conflicting" in reason for reason in issues)
            note = (
                "Reported versions or ownership scopes disagree; no parent balance has been selected."
                if conflicting
                else "The parent balance has not yet been matched to this date and ownership scope in the source report."
            )
            for cik, accession, _ in task["sources"]:
                source = cik + ":" + accession
                entry = evidence.get(source, {})
                if "instance" in entry and "scope_files" not in entry:
                    alternative_jobs.add((source, "scope"))
            policy["unresolved"][key] = {
                "note": note,
                "sources": [
                    {
                        "source_cik": cik,
                        "accession": accession,
                        "filed": str(filed),
                        "statements": [
                            reference
                            for name, reference in evidence.get(
                                cik + ":" + accession, {}
                            ).items()
                            if name.endswith(".htm") and isinstance(reference, dict)
                        ],
                    }
                    for cik, accession, filed in sorted(task["sources"])
                ],
                "ticker": task["ticker"],
                "fiscal_year": period.fiscal_year,
                "reasons": sorted(set(issues)) or ["Conflicting parent-equity amounts"],
            }
    if fetch and alternative_jobs:
        cache_evidence(alternative_jobs, evidence)
        return review_equity(fetch=False)
    for task in tasks:
        key = f"{task['primary']}:{task['period'].period_end}"
        company = report["companies"].setdefault(
            task["ticker"], {"approved": 0, "unresolved": []}
        )
        if key in policy["periods"]:
            company["approved"] += 1
        else:
            company["unresolved"].append(policy["unresolved"][key])
    report["totals"] = {
        "companies": len(report["companies"]),
        "periods": len(tasks),
        "approved": len(policy["periods"]),
        "unresolved": len(policy["unresolved"]),
    }
    if policy["unresolved"]:
        report["status"] = "completed_with_gaps"
    policy["excluded_variants"] = [
        {
            "source_cik": cik,
            "period_end": end,
            "accession": accession,
            "tag": tag,
            "value": value,
            "reason": "Reviewed historical tag misuse: this is not a whole-equity balance. Original facts remain stored.",
        }
        for (cik, end, accession, tag), value in REJECTED_FACTS.items()
    ]
    write_json(RULES_PATH, policy)
    write_json(ROOT / "data/processed/sec-equity-report.json", report)
    return {
        "status": report["status"],
        **report["totals"],
        "issues": policy["unresolved"],
    }
