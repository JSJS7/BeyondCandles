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
            "validate-prices",
            "import-prices",
            "fetch-sec",
            "audit-sec",
            "import-sec",
            "reconcile-sec",
            "inspect-db",
            "verify-db",
        ),
    )
    parser.add_argument("--archive", help="Optional path to the pinned Kaggle ZIP")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch a new SEC cache index; retain old response files",
    )
    args = parser.parse_args()

    try:
        if args.command == "validate-prices":
            from .prices import validate_prices

            processed_path, companies, validation_summary = validate_prices(args.archive)
            result = validation_summary
        elif args.command == "import-prices":
            from .prices import import_prices

            result = import_prices(args.archive)
        elif args.command == "fetch-sec":
            from .sec import fetch_sec

            result = fetch_sec(args.refresh)
        elif args.command == "audit-sec":
            from .sec import audit_sec

            result = audit_sec()
        elif args.command == "import-sec":
            from .sec import import_sec

            result = import_sec()
        elif args.command == "reconcile-sec":
            from .reconciliation import reconcile_sec

            result = reconcile_sec()
        elif args.command == "inspect-db":
            from .verification import inspect_db

            result = inspect_db()
        else:
            from .verification import verify_db

            result = verify_db()

        if args.command in ("audit-sec", "reconcile-sec"):
            output = {
                "status": result["status"],
                "companies": len(result["companies"]),
                "reports": "data/processed/",
            }
            formatted_result = json.dumps(output, indent=2)
        else:
            formatted_result = json.dumps(result, indent=2, default=str)

        print(formatted_result)

        if result.get("status") == "needs_review":
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
