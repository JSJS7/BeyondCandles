import argparse
import json
import sys


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
            "review-equity",
            "review-operating-cash-flow",
            "review-sec-periods",
            "import-sec",
            "inspect-db",
            "verify-db",
        ),
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Download missing SEC review evidence; otherwise use the local cache",
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
    args = parser.parse_args()

    try:
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

            result = audit_prices()
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
        elif args.command == "review-equity":
            from .equity import review_equity

            result = review_equity(fetch=args.fetch)
        elif args.command == "review-operating-cash-flow":
            from .operating_cash_flow import review_cash_flow

            result = review_cash_flow(fetch=args.fetch)
        elif args.command == "review-eps-splits":
            from .eps_splits import review_eps_splits

            result = review_eps_splits()
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
        elif args.command == "inspect-db":
            from .verification import inspect_db

            result = inspect_db()
        else:
            from .verification import verify_db

            result = verify_db()

        if args.command == "import-sec":
            facts = sum(company["facts"] for company in result.values())
            inserted = sum(company["inserted"] for company in result.values())
            output = {
                "status": "completed",
                "companies": len(result),
                "facts": facts,
                "inserted": inserted,
                "unchanged": facts - inserted,
                "history_updated": sum(
                    company["history_updated"] for company in result.values()
                ),
            }
            formatted_result = json.dumps(output, indent=2)
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
            formatted_result = json.dumps(output, indent=2)
        elif args.command == "review-equity":
            output = {key: value for key, value in result.items() if key != "issues"}
            output["report"] = "data/processed/sec-equity-report.json"
            formatted_result = json.dumps(output, indent=2)
        else:
            formatted_result = json.dumps(result, indent=2, default=str)

        print(formatted_result)

        if result.get("status") in ("needs_review", "completed_with_gaps"):
            return 1
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"Data preparation failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Driver messages can embed connection configuration; do not print them.
        error_message = (
            f"Data preparation failed ({type(exc).__name__}). "
            "Check local database availability and migrations."
        )
        print(error_message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    exit_code = main()
    raise SystemExit(exit_code)
