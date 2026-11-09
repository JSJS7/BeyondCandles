"""Command-line entry point for reproducible price and SEC data preparation."""

import argparse
import json
import sys
from datetime import date

import psycopg
from sqlalchemy.exc import SQLAlchemyError

# Commands that only read sources and publish JSON files, so a --check run can hold
# their writes and compare them with the files on disk.
CHECKABLE_COMMANDS = (
    "audit-sec",
    "audit-sec-history",
    "review-revenue",
    "review-net-income",
    "review-eps",
    "review-eps-splits",
    "approve-eps-splits",
    "review-equity",
    "review-operating-cash-flow",
    "review-sec-periods",
    "review-versions",
)


def run_command(args):
    """Run one pipeline command and return its result document."""
    if args.command == "fetch-prices":
        from .benjaminpo import fetch_prices

        result = fetch_prices()
    elif args.command == "prepare-prices":
        from .benjaminpo import prepare_prices

        _, _, result = prepare_prices()
    elif args.command == "replace-prices":
        from .price_replacement import replace_prices

        result = replace_prices(args.expected_price_fingerprint)
    elif args.command == "restore-prices":
        from .price_replacement import restore_prices

        result = restore_prices(args.backup, args.expected_price_fingerprint)
    elif args.command == "validate-prices":
        from .prices import validate_prices

        _, _, result = validate_prices()
    elif args.command == "import-prices":
        from .prices import import_prices

        result = import_prices()
    elif args.command == "audit-prices":
        from .price_adjustments import audit_prices

        result = audit_prices(fetch=args.fetch)
    elif args.command == "fetch-sec":
        from .sec_catalogue import fetch_all_sec

        result = fetch_all_sec(args.refresh)
    elif args.command == "resolve-sec":
        from .identifiers import resolve_sec

        result = resolve_sec()
    elif args.command == "audit-sec":
        from .sec import audit_sec

        result = audit_sec()
    elif args.command == "audit-sec-history":
        from .sec_history import audit_history

        result = audit_history()
    elif args.command == "import-sec":
        from .sec import import_sec

        result = import_sec()
    elif args.command == "build-history":
        from .metric_history import build_history

        result = build_history()
    elif args.command == "review-equity":
        from .equity import review_equity

        result = review_equity(fetch=args.fetch)
    elif args.command == "review-operating-cash-flow":
        from .operating_cash_flow import review_cash_flow

        result = review_cash_flow(fetch=args.fetch)
    elif args.command == "review-eps-splits":
        from .eps_splits import review_eps_splits

        result = review_eps_splits()
    elif args.command == "approve-eps-splits":
        from .eps_splits import approve_eps_splits

        result = approve_eps_splits(args.approved_through)
    elif args.command == "review-eps":
        from .eps import review_eps

        result = review_eps(fetch=args.fetch)
    elif args.command == "review-net-income":
        from .net_income import review_net_income

        result = review_net_income(fetch=args.fetch)
    elif args.command == "review-revenue":
        from .revenue import review_revenue

        result = review_revenue(fetch=args.fetch)
    elif args.command == "review-sec-periods":
        from .fiscal_periods import review_fiscal_periods

        result = review_fiscal_periods(fetch=args.fetch)
    elif args.command == "review-versions":
        from .version_reviews import review_versions

        result = review_versions(fetch=args.fetch)
    elif args.command == "check-documents":
        from .document_check import check_documents

        result = check_documents(fetch_missing=args.fetch)
    elif args.command == "check-automatic":
        from .document_check import check_automatic

        result = check_automatic(fetch_missing=args.fetch)
    elif args.command == "control-documents":
        from .document_check import control_documents

        result = control_documents()
    elif args.command == "check-identities":
        from .identity_checks import check_identities

        result = check_identities()
    elif args.command == "check-invariants":
        from .invariant_checks import check_invariants

        result = check_invariants()
    elif args.command == "inspect-db":
        from .verification import inspect_db

        result = inspect_db()
    else:
        from .verification import verify_db

        result = verify_db()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="BeyondCandles reproducible data preparation"
    )
    parser.add_argument(
        "command",
        choices=(
            "fetch-prices",
            "prepare-prices",
            "replace-prices",
            "restore-prices",
            "validate-prices",
            "import-prices",
            "audit-prices",
            "fetch-sec",
            "resolve-sec",
            "audit-sec",
            "audit-sec-history",
            "review-revenue",
            "review-net-income",
            "review-eps",
            "review-eps-splits",
            "approve-eps-splits",
            "review-equity",
            "review-operating-cash-flow",
            "review-sec-periods",
            "review-versions",
            "import-sec",
            "build-history",
            "check-documents",
            "check-automatic",
            "control-documents",
            "check-identities",
            "check-invariants",
            "inspect-db",
            "verify-db",
        ),
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Download missing review evidence; otherwise use the local cache",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch a new SEC cache index; retain old response files",
    )
    parser.add_argument(
        "--expected-price-fingerprint",
        help="Verified fingerprint of the stored prices before replacement or restore",
    )
    parser.add_argument("--backup", help="Full CSV backup created by replace-prices")
    parser.add_argument(
        "--approved-through",
        type=date.fromisoformat,
        help=(
            "Last period end covered by the current EPS split approvals; "
            "approve-eps-splits requires it"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Write nothing; compare the files a review or audit would publish with "
            "the files on disk and fail if any differs beyond its run timestamp"
        ),
    )
    args = parser.parse_args()
    if args.check and args.command not in CHECKABLE_COMMANDS:
        parser.error("--check works only with: " + ", ".join(CHECKABLE_COMMANDS))
    if args.check and (args.fetch or args.refresh):
        parser.error("--check reads the local cache only; omit --fetch and --refresh")
    if args.command == "approve-eps-splits" and args.approved_through is None:
        parser.error("approve-eps-splits requires --approved-through YYYY-MM-DD")

    try:
        comparison = None
        if args.check:
            from .source_files import held_writes

            with held_writes() as comparison:
                result = run_command(args)
        else:
            result = run_command(args)

        if args.command == "import-sec":
            companies = result["companies"].values()
            facts = sum(company["facts"] for company in companies)
            inserted = sum(company["inserted"] for company in companies)
            output = {
                "status": "completed",
                "companies": len(result["companies"]),
                "facts": facts,
                "inserted": inserted,
                "unchanged": facts - inserted,
                "history_updated": sum(
                    company["history_updated"] for company in companies
                ),
                "metric_versions": result["history"]["versions"],
                "metric_history_fingerprint": result["history"]["fingerprint"],
            }
        elif args.command in (
            "audit-sec",
            "audit-sec-history",
            "review-revenue",
        ):
            output = {
                "status": result["status"],
                "companies": len(result["companies"]),
                "reports": "data/processed/",
            }
            if args.command == "review-revenue":
                output["approved_periods"] = result["totals"]["approved_periods"]
                output["unresolved_cases"] = result["totals"]["issues"]
        elif args.command == "review-equity":
            output = {key: value for key, value in result.items() if key != "issues"}
            output["report"] = "data/processed/sec-equity-report.json"
        else:
            output = dict(result)
        if comparison is not None:
            output["check"] = comparison
        formatted_result = json.dumps(output, indent=2, default=str)

        print(formatted_result)

        if comparison is not None:
            # A check compares published files; a known gap is recorded in them, so
            # an unchanged gap passes and a new or changed one is a changed file.
            # needs_review still fails: some commands report it without writing a
            # file (review-eps-splits never writes; review-sec-periods stops first).
            if comparison["changed"] or result.get("status") == "needs_review":
                return 1
            return 0
        if result.get("status") in ("needs_review", "completed_with_gaps"):
            return 1
        return 0
    except (ValueError, OSError) as exc:
        print(f"Data preparation failed: {exc}", file=sys.stderr)
        return 1
    except (SQLAlchemyError, psycopg.Error) as exc:
        # Driver messages can embed connection configuration; do not print them.
        # Other unexpected errors propagate with a traceback for diagnosis.
        error_message = (
            f"Data preparation failed ({type(exc).__name__}). "
            "Check local database availability and migrations."
        )
        print(error_message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    exit_code = main()
    raise SystemExit(exit_code)
