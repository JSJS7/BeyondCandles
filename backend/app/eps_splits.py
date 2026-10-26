"""Present frozen SEC EPS on a reviewed, common share-count basis."""

import hashlib
import json
import logging
from datetime import date
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from app.equity import fact_identity
from app.schemas.stock import EpsShareBasis, EpsSplit

DATA = Path(__file__).resolve().parents[2] / "data"
RULES_PATH = DATA / "eps-split-rules.json"
logger = logging.getLogger(__name__)


def sources_fingerprint(periods):
    """Bind approval to the actual EPS definition, issuer, period and filing version."""
    sources = []
    for period in periods:
        metric = period.metrics["eps_diluted"]
        if metric.status != "ready":
            continue
        facts = []
        for source in metric.selected_sources:
            identity = fact_identity(source)
            for key in ("statement_url", "statement_label", "statement_column"):
                identity.pop(key, None)
            facts.append(identity)
        number = format(metric.value, "f") if metric.value is not None else None
        if number is not None and "." in number:
            number = number.rstrip("0").rstrip(".")
        if metric.value == 0:
            number = "0"
        sources.append(
            {
                "start": str(period.period_start),
                "end": str(period.period_end),
                "basis": metric.eps_basis,
                "value": number,
                "facts": facts,
            }
        )
    sources.sort(key=lambda item: (item["start"], item["end"]))
    encoded = json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_share_exchanges(ticker, approval):
    """Several issuers share one EPS basis only through reviewed one-for-one exchanges.

    Each predecessor must lead, exchange by exchange, to the single current issuer. Another
    exchange ratio would change the per-share scale and is not supported.
    """
    issuers = set(approval.get("source_ciks", []))
    successor_of = {}
    for exchange in approval.get("share_exchanges", []):
        from_cik = exchange["from_cik"]
        to_cik = exchange["to_cik"]
        if from_cik not in issuers or to_cik not in issuers or from_cik == to_cik:
            raise ValueError(f"Share exchange issuer is not approved: {ticker}")
        if from_cik in successor_of:
            raise ValueError(f"Predecessor has several share exchanges: {ticker}")
        if (exchange["new_shares"], exchange["old_shares"]) != (1, 1):
            raise ValueError(
                f"Only one-for-one share exchanges are supported: {ticker}"
            )
        if not exchange.get("evidence", {}).get("url"):
            raise ValueError(f"Share exchange requires source evidence: {ticker}")
        successor_of[from_cik] = to_cik
    current_issuers = issuers - set(successor_of)
    if issuers and len(current_issuers) != 1:
        raise ValueError(
            f"Every predecessor requires a reviewed share exchange: {ticker}"
        )
    for cik in successor_of:
        visited = set()
        while cik in successor_of:
            if cik in visited:
                raise ValueError(f"Share exchanges form a cycle: {ticker}")
            visited.add(cik)
            cik = successor_of[cik]


@lru_cache(maxsize=1)
def load_rules(stamps):
    document = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    selection_hash = hashlib.sha256(
        (DATA / "companies-200.json").read_bytes()
    ).hexdigest()
    if document["version"] != 1:
        raise ValueError("Unsupported EPS split policy version")
    if (
        document["price_manifest_sha256"] != manifest["sha256"]
        or document["selection_sha256"] != selection_hash
    ):
        raise ValueError(
            "EPS share basis does not match the active prices or catalogue"
        )
    basis_date = date.fromisoformat(document["basis_date"])
    keys = set()
    for action in document["actions"]:
        key = action["ticker"], action["date"]
        if key in keys or date.fromisoformat(action["date"]) > basis_date:
            raise ValueError("Duplicate or future EPS split")
        keys.add(key)
        EpsSplit.model_validate(action)
        approval = document["companies"].get(action["ticker"], {})
        if action["source_cik"] not in approval.get("source_ciks", []):
            raise ValueError("Split issuer does not match its reviewed EPS sources")
    for ticker, approval in document["companies"].items():
        validate_share_exchanges(ticker, approval)
    return document


def split_rules():
    paths = (RULES_PATH, DATA / "manifest.json", DATA / "companies-200.json")
    return load_rules(tuple(path.stat().st_mtime_ns for path in paths))


def share_count_ratio(actions):
    """Multiply exact new/old share counts; never use dividend or vendor spin-off factors."""
    ratio = Fraction(1)
    for action in actions:
        ratio *= Fraction(action.new_shares, action.old_shares)
    return ratio


def adjusted_eps(reported, ratio):
    """Round only the final result, retaining ten decimal places and exact input metadata."""
    if ratio == 1:
        return reported
    with localcontext() as context:
        context.prec = 60
        result = reported * Decimal(ratio.denominator) / Decimal(ratio.numerator)
        return result.quantize(Decimal("0.0000000001"))


def normalize_eps(ticker, periods, rules=None):
    """Return copies; raw facts and the reported annual aggregator remain unchanged."""
    result = [period.model_copy(deep=True) for period in periods]
    if not any(p.metrics["eps_diluted"].status == "ready" for p in result):
        return result
    try:
        policy = split_rules() if rules is None else rules
    except (OSError, ValueError, KeyError) as error:
        # Fail closed for the API, but keep the cause visible to the operator.
        logger.warning("EPS split rules are unusable; EPS needs review: %r", error)
        policy = {}
    approval = policy.get("companies", {}).get(ticker)
    approved = approval and approval["observed_sha256"] == sources_fingerprint(periods)
    # Approved issuers are linked by reviewed one-for-one share exchanges, so they share
    # one share basis and every later split of the ticker also restates predecessor filings.
    shared_basis_issuers = approval.get("source_ciks", []) if approval else []
    for period in result:
        metric = period.metrics["eps_diluted"]
        if metric.status != "ready":
            continue
        single_source = len(metric.selected_sources) == 1
        source = metric.selected_sources[0] if single_source else None
        if (
            not approved
            or not single_source
            or source.source_cik not in shared_basis_issuers
        ):
            metric.status = "needs_review"
            metric.value = None
            metric.selection_note = (
                "EPS share basis requires review after a source or price change."
            )
            continue
        # This rule is approved for the exact frozen sources above, not arbitrary future filings.
        pending = [
            EpsSplit.model_validate(action)
            for action in policy["actions"]
            if action["ticker"] == ticker
            and source.filed < date.fromisoformat(action["date"])
        ]
        ratio = share_count_ratio(pending)
        reported = metric.value
        metric.value = adjusted_eps(reported, ratio)
        metric.eps_share_basis = EpsShareBasis(
            as_of=policy["basis_date"],
            reported_value=reported,
            new_shares=ratio.numerator,
            old_shares=ratio.denominator,
            applied_splits=pending,
        )
        metric.selection_note = (metric.selection_note or "") + (
            f" Normalized share-count basis: {policy['basis_date']}. "
            "Cash dividends and spin-off price conversions are excluded. "
            "This is a latest-source view, not point-in-time data."
        )
        if pending:
            metric.calculation = (
                f"Reported EPS × {ratio.denominator} / {ratio.numerator}"
            )
    return result
