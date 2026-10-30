import hashlib
import unittest
from datetime import date
from decimal import Decimal
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

from app.data_pipeline.sec import (
    SecAccessDenied,
    cache_response,
    extract_facts,
    fetch,
    period_kind,
)
from app.data_pipeline.validation import validate_price
from app.data_pipeline.verification import fingerprint


class PriceTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "Open": "10",
            "High": "12",
            "Low": "9",
            "Close": "11",
            "Adj Close": "5",
            "Volume": "100",
        }

    def test_adjusted_close_is_independent_of_raw_envelope(self):
        """Accept adjusted close below the raw daily low and preserve its value."""
        validated_row = validate_price(self.row)
        adjusted_close = validated_row[4]

        self.assertEqual(adjusted_close, "5")

    def test_invalid_inputs_are_rejected(self):
        """Reject missing, non-finite, negative, fractional-volume and invalid OHLC values."""
        invalid_values = (
            ("Open", ""),
            ("Close", "NaN"),
            ("Low", "-1"),
            ("High", "10"),
            ("Volume", "-1"),
            ("Volume", "1.1"),
            ("Close", "Infinity"),
        )

        for column, value in invalid_values:
            with self.subTest(column=column, value=value):
                invalid_row = self.row.copy()
                invalid_row[column] = value

                with self.assertRaises(ValueError):
                    validate_price(invalid_row)

    def test_vendor_fractional_precision_is_preserved(self):
        """Keep all source decimal digits instead of rounding the adjusted price."""
        self.row["Adj Close"] = "0.12656955420970917"

        validated_row = validate_price(self.row)
        adjusted_close = validated_row[4]

        self.assertEqual(adjusted_close, self.row["Adj Close"])

    def test_fingerprint_ignores_decimal_padding(self):
        """Treat equivalent CSV and database date/number representations as equal."""
        csv_row = ["AAPL", "2020-01-02", "1.000"]
        database_row = ["AAPL", date(2020, 1, 2), Decimal("1")]

        csv_fingerprint = fingerprint(csv_row)
        database_fingerprint = fingerprint(database_row)

        self.assertEqual(csv_fingerprint, database_fingerprint)

    def test_fingerprint_keeps_all_decimal_digits(self):
        """Detect a change past Decimal's default precision of 28 significant digits."""
        original = ["TEST", "2020-01-02", "1234567890123.123456789012345678"]
        changed = ["TEST", "2020-01-02", "1234567890123.123456789012345679"]

        self.assertNotEqual(fingerprint(original), fingerprint(changed))


class SecTests(unittest.TestCase):
    def body(self) -> dict[str, Any]:
        """Build SEC-like data with one repeated fact and a later correction."""
        original = {
            "start": "2020-01-01",
            "end": "2020-12-31",
            "filed": "2021-02-01",
            "val": 100,
            "accn": "original",
            "form": "10-K",
            "fp": "FY",
        }

        repeated = original.copy()
        repeated["frame"] = "CY2020"

        corrected = original.copy()
        corrected["val"] = 95
        corrected["filed"] = "2022-02-01"
        corrected["accn"] = "later"

        return {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [original, repeated, corrected],
                        },
                    },
                },
            },
        }

    def test_download_repairs_a_corrupted_cache_file(self):
        """Replace corrupt cached bytes when a fresh response has the expected hash."""
        downloaded = b'{"cik": 320193}'
        digest = hashlib.sha256(downloaded).hexdigest()
        with TemporaryDirectory(prefix="beyondcandles-cache-test-") as directory:
            cache = Path(directory)
            cached_file = cache / f"{digest}.json"
            cached_file.write_bytes(b"interrupted download")

            with (
                patch("app.data_pipeline.sec.CACHE", cache),
                patch("app.data_pipeline.sec.fetch", return_value=downloaded),
            ):
                result = cache_response(
                    "https://example.com/facts.json", "BeyondCandles test"
                )

            self.assertEqual(cached_file.read_bytes(), downloaded)
            self.assertEqual(result["sha256"], digest)

    def test_corrections_keep_both_filing_versions_and_deduplicate_frames(self):
        """Remove a repeated frame but retain both original and corrected filings."""
        facts, _ = extract_facts(self.body())

        reported_values = {fact["value"] for fact in facts}
        filing_dates = {fact["filed"] for fact in facts}
        expected_values = {Decimal(100), Decimal(95)}
        expected_filing_dates = {date(2021, 2, 1), date(2022, 2, 1)}

        self.assertEqual(len(facts), 2)
        self.assertEqual(reported_values, expected_values)
        self.assertEqual(filing_dates, expected_filing_dates)

    def test_conflicting_values_in_same_filing_stop_import(self):
        """Reject two different values for the same fact in the same filing."""
        body = self.body()
        reported_facts = body["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
        repeated_fact = reported_facts[1]
        repeated_fact["val"] = 99

        with self.assertRaises(ValueError):
            extract_facts(body)

    def test_missing_metrics_are_not_synthesized(self):
        """Return only reported net income; do not invent missing metrics as zero."""
        facts, _ = extract_facts(self.body())
        reported_metrics = {fact["metric"] for fact in facts}

        self.assertEqual(reported_metrics, {"net_income"})

    def test_quarters_ytd_and_annual_are_separate(self):
        """Distinguish quarters, cumulative periods, annual totals and instant facts."""
        start = date(2020, 1, 1)
        self.assertEqual(period_kind(start, date(2020, 3, 31)), "quarter")
        self.assertEqual(period_kind(start, date(2020, 6, 30)), "six_months_ytd")
        self.assertEqual(period_kind(start, date(2020, 9, 30)), "nine_months_ytd")
        self.assertEqual(period_kind(start, date(2020, 12, 31)), "annual")
        self.assertEqual(period_kind(start, start, True), "instant")

    def test_unknown_identity_is_not_accepted_by_name_guessing(self):
        """Reject an Apple name match when SEC does not confirm the AAPL ticker."""
        from app.data_pipeline.sec_catalogue import verify_submission

        approved_entry = {"cik": "0000320193", "evidence": None}
        unconfirmed_company = {
            "cik": 320193,
            "name": "Apple",
            "tickers": [],
        }

        with self.assertRaises(ValueError):
            verify_submission("AAPL", approved_entry, unconfirmed_company)

    def test_access_denial_is_a_distinct_error_without_retries(self):
        """HTTP 403 raises SecAccessDenied at once, so concurrent downloads can stop.

        Download workers used to detect a denial by searching the message text;
        a reworded message would then have let the remaining workers continue.
        """
        denied = HTTPError("https://www.sec.gov/x", 403, "Forbidden", Message(), None)

        with (
            patch("app.data_pipeline.sec.urlopen", side_effect=denied) as request,
            patch("app.data_pipeline.sec.time.sleep"),
        ):
            with self.assertRaises(SecAccessDenied):
                fetch("https://www.sec.gov/x", "Test agent test@example.com")

        self.assertEqual(request.call_count, 1)

    def test_temporary_server_error_is_not_an_access_denial(self):
        """A repeated HTTP 503 fails as an ordinary error, not as SEC blocking access."""
        unavailable = HTTPError(
            "https://www.sec.gov/x", 503, "Unavailable", Message(), None
        )

        with (
            patch("app.data_pipeline.sec.urlopen", side_effect=unavailable) as request,
            patch("app.data_pipeline.sec.time.sleep"),
        ):
            with self.assertRaises(ValueError) as raised:
                fetch("https://www.sec.gov/x", "Test agent test@example.com")

        self.assertNotIsInstance(raised.exception, SecAccessDenied)
        self.assertEqual(request.call_count, 3)


class CommandLineErrorTests(unittest.TestCase):
    def run_failing_command(self, error):
        from contextlib import redirect_stderr
        from io import StringIO

        from app.data_pipeline.__main__ import main

        stderr = StringIO()
        with (
            patch("sys.argv", ["data_pipeline", "inspect-db"]),
            patch("app.data_pipeline.verification.inspect_db", side_effect=error),
            redirect_stderr(stderr),
        ):
            exit_code = main()
        return exit_code, stderr.getvalue()

    def test_database_error_hides_driver_details(self):
        """A database failure exits with 1 and never prints the driver message."""
        from sqlalchemy.exc import OperationalError

        error = OperationalError("SELECT 1", {}, Exception("password=secret"))

        exit_code, message = self.run_failing_command(error)

        self.assertEqual(exit_code, 1)
        self.assertIn("OperationalError", message)
        self.assertNotIn("secret", message)

    def test_programming_error_keeps_its_traceback(self):
        """An unexpected bug propagates instead of being reduced to its class name.

        Previously every exception was printed only as its type, which hid the
        failing line of a programming error.
        """
        with self.assertRaises(TypeError):
            self.run_failing_command(TypeError("unexpected argument"))


class CheckModeTests(unittest.TestCase):
    """--check holds every JSON write and compares it with the file on disk."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.same = self.root / "same.json"
        self.changed = self.root / "changed.json"
        self.new = self.root / "new.json"
        self.same.write_text('{\n  "a": 1,\n  "reviewed_at": "old"\n}\n')
        self.changed.write_text('{\n  "a": 1\n}\n')

    def run_check(self, review):
        import json
        from contextlib import redirect_stdout
        from io import StringIO

        from app.data_pipeline.__main__ import main

        stdout = StringIO()
        with (
            patch("sys.argv", ["data_pipeline", "review-sec-periods", "--check"]),
            patch("app.data_pipeline.source_files.ROOT", self.root),
            patch(
                "app.data_pipeline.fiscal_periods.review_fiscal_periods",
                side_effect=review,
            ),
            redirect_stdout(stdout),
        ):
            exit_code = main()
        return exit_code, json.loads(stdout.getvalue())["check"]

    def test_differences_fail_without_touching_the_files(self):
        """A changed or new file fails the check; disk content stays as it was.

        A later read in the same run sees the held content, as a real run would.
        """
        from app.data_pipeline.source_files import read_json, write_json

        def review(fetch):
            write_json(self.same, {"a": 1, "reviewed_at": "new"})
            write_json(self.changed, {"a": 2})
            write_json(self.new, {"a": 3})
            self.assertEqual(read_json(self.changed), {"a": 2})
            return {"status": "verified"}

        before = self.changed.read_text()

        exit_code, check = self.run_check(review)

        self.assertEqual(exit_code, 1)
        self.assertEqual(check["changed"], ["changed.json", "new.json"])
        self.assertEqual(check["unchanged"], ["same.json"])
        self.assertEqual(self.changed.read_text(), before)
        self.assertIn('"old"', self.same.read_text())
        self.assertFalse(self.new.exists())
        self.assertEqual(read_json(self.changed), {"a": 1})

    def test_only_a_new_run_timestamp_passes(self):
        """Regenerating the same reviewed data is a successful check."""
        from app.data_pipeline.source_files import write_json

        def review(fetch):
            write_json(self.same, {"a": 1, "reviewed_at": "new"})
            return {"status": "verified"}

        exit_code, check = self.run_check(review)

        self.assertEqual(exit_code, 0)
        self.assertEqual(check, {"changed": [], "unchanged": ["same.json"]})

    def test_a_known_gap_reproduced_unchanged_passes(self):
        """The check compares files, not the review status.

        Real case (30.10.2026): review-versions keeps one unresolved version (BK
        revenue 2015), so its status is completed_with_gaps; an identical rerun
        exited with 1 and looked like a difference. The gap is recorded in the
        file, so a new or changed gap would still show up as a changed file.
        """
        from app.data_pipeline.source_files import write_json

        def review(fetch):
            write_json(self.same, {"a": 1, "reviewed_at": "new"})
            return {"status": "completed_with_gaps"}

        exit_code, check = self.run_check(review)

        self.assertEqual(exit_code, 0)
        self.assertEqual(check["changed"], [])

    def test_commands_that_change_the_database_are_rejected(self):
        """--check cannot hold database writes, so such commands refuse it."""
        from contextlib import redirect_stderr
        from io import StringIO

        from app.data_pipeline.__main__ import main

        with (
            patch("sys.argv", ["data_pipeline", "build-history", "--check"]),
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            main()

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
