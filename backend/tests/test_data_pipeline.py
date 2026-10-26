import hashlib
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from app.data_pipeline.sec import (
    cache_response,
    extract_facts,
    period_kind,
    verify_identity,
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
        unconfirmed_company = {
            "cik": 320193,
            "name": "Apple",
            "tickers": [],
        }

        with self.assertRaises(ValueError):
            verify_identity("AAPL", "0000320193", unconfirmed_company)


if __name__ == "__main__":
    unittest.main()
