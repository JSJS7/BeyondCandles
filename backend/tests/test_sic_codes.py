import json
import tempfile
import unittest
from pathlib import Path

from app.data_pipeline.sic_codes import sic_document, sic_entry
from app.sic_codes import financial_tickers, is_financial_sic


class SicEntryTests(unittest.TestCase):
    def test_entry_keeps_the_code_description_and_source_checksum(self):
        """The code is stored as a number with the checksum of the file it came from."""
        submission = {"sic": "6021", "sicDescription": "National Commercial Banks"}

        entry = sic_entry("JPM", "0000019617", submission, "abc123")

        self.assertEqual(
            entry,
            {
                "ticker": "JPM",
                "cik": "0000019617",
                "sic": 6021,
                "sic_description": "National Commercial Banks",
                "submissions_sha256": "abc123",
            },
        )

    def test_missing_or_blank_code_stops_the_command(self):
        """A company without a code must not silently fall outside the financial group."""
        for submission in ({}, {"sic": ""}, {"sic": "n/a"}):
            with self.subTest(submission=submission):
                with self.assertRaises(ValueError):
                    sic_entry("TEST", "0000000001", submission, "abc123")


class SicDocumentTests(unittest.TestCase):
    def test_companies_are_sorted_and_the_financial_group_is_counted(self):
        """Both ends of 6000-6999 count; codes just outside them do not."""
        entries = [
            sic_entry(ticker, "000000000" + str(number), {"sic": code}, "x")
            for number, (ticker, code) in enumerate(
                [("ZZ", "7000"), ("BB", "6999"), ("AA", "6000"), ("CC", "5999")]
            )
        ]

        document = sic_document(entries)

        tickers = [entry["ticker"] for entry in document["companies"]]
        self.assertEqual(tickers, ["AA", "BB", "CC", "ZZ"])
        self.assertEqual(document["financial_companies"], 2)

    def test_financial_range_bounds(self):
        self.assertTrue(is_financial_sic(6000))
        self.assertTrue(is_financial_sic(6999))
        self.assertFalse(is_financial_sic(5999))
        self.assertFalse(is_financial_sic(7000))


class FinancialTickerTests(unittest.TestCase):
    def test_recorded_file_names_forty_two_financial_firms(self):
        """DEC-33: 42 companies have a SIC code of 6000-6999, health insurers and REITs
        included. Visa, Mastercard and S&P Global sit in the vendor's Financial Services
        sector but have service codes (7xxx), so the code, not the sector, decides."""
        tickers = financial_tickers()

        self.assertEqual(len(tickers), 42)
        self.assertTrue({"JPM", "BRK-B", "UNH", "PLD", "CME"} <= tickers)
        self.assertFalse({"V", "MA", "SPGI", "MCO", "MSCI"} & tickers)

    def test_tickers_are_read_from_the_given_file(self):
        """Only the code decides; the stored count is a summary, not an input."""
        document = {
            "version": 1,
            "financial_companies": 0,
            "companies": [
                {"ticker": "BANK", "sic": 6022},
                {"ticker": "SOFT", "sic": 7372},
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sic-codes.json"
            path.write_text(json.dumps(document), encoding="utf-8")

            tickers = financial_tickers(path)

        self.assertEqual(tickers, {"BANK"})


if __name__ == "__main__":
    unittest.main()
