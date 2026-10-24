"""Verify full-catalogue cache safety with tiny synthetic responses and no network."""

import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.data_pipeline.sec_catalogue import (
    fetch_all_sec,
    load_all_sec,
    verify_submission,
)
from app.data_pipeline.source_files import sha256
from app.schemas.stock import AnnualMetric


class CatalogueCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="beyondcandles-sec-catalogue-")
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)
        self.issuers = {}
        for ticker, cik in (("AAPL", "0000320193"), ("MSFT", "0000789019")):
            reference = self.write_response(
                ticker + "-submission", {"cik": int(cik), "tickers": [ticker]}
            )
            self.issuers[ticker] = {
                "cik": cik,
                "name": ticker,
                "submissions": reference,
                "evidence": None,
            }
        self.patches = [
            patch("app.data_pipeline.sec_catalogue.CACHE", self.cache),
            patch(
                "app.data_pipeline.sec_catalogue.ALL_INDEX",
                self.cache / "all-index.json",
            ),
            patch(
                "app.data_pipeline.sec_catalogue.DOWNLOAD_PROGRESS",
                self.cache / "progress.json",
            ),
            patch(
                "app.data_pipeline.sec_catalogue.INDEX", self.cache / "pilot-index.json"
            ),
            patch(
                "app.data_pipeline.sec_catalogue.identifiers", return_value=self.issuers
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def write_response(self, name, body):
        path = self.cache / (name + ".json")
        path.write_text(json.dumps(body), encoding="utf-8")
        return {
            "file": path.name,
            "sha256": sha256(path),
            "url": "https://example.com/" + path.name,
        }

    def test_failed_download_resumes_without_publishing_a_partial_index(self):
        """Preserve completed downloads after failure and publish only the complete selection."""
        facts = {
            ticker: self.write_response(
                ticker + "-facts", {"cik": int(entry["cik"]), "facts": {}}
            )
            for ticker, entry in self.issuers.items()
        }

        def download(url, previous=None, json_response=True):
            if "submission" in url:
                return next(
                    entry["submissions"]
                    for entry in self.issuers.values()
                    if entry["submissions"]["url"] == url
                )
            ticker = "AAPL" if "320193" in url else "MSFT"
            if ticker == "MSFT":
                raise ValueError("SEC HTTP 503")
            return facts[ticker]

        with patch("app.data_pipeline.sec_catalogue.cached", side_effect=download):
            with self.assertRaisesRegex(ValueError, "503"):
                fetch_all_sec()
        self.assertFalse((self.cache / "all-index.json").exists())
        checkpoint = json.loads((self.cache / "progress.json").read_text())
        self.assertEqual(set(checkpoint["companies"]), {"AAPL"})

        def resume(url, previous=None, json_response=True):
            if "submission" in url:
                return next(
                    entry["submissions"]
                    for entry in self.issuers.values()
                    if entry["submissions"]["url"] == url
                )
            ticker = "AAPL" if "320193" in url else "MSFT"
            if ticker == "AAPL":
                self.assertEqual(previous, facts[ticker])
            return facts[ticker]

        with patch("app.data_pipeline.sec_catalogue.cached", side_effect=resume):
            self.assertEqual(fetch_all_sec()["companies"], 2)
        _, companies = load_all_sec()
        self.assertEqual(len(list(companies)), 2)

    def test_cache_corruption_and_cik_mismatch_are_rejected(self):
        """Reject altered bytes and foreign issuer facts before they reach the importer."""
        entries = {}
        for ticker, issuer in self.issuers.items():
            facts = self.write_response(
                ticker + "-facts", {"cik": int(issuer["cik"]), "facts": {}}
            )
            entries[ticker] = {**issuer, "facts": facts}
        index = self.cache / "all-index.json"
        index.write_text(json.dumps({"companies": entries}), encoding="utf-8")
        altered = self.cache / entries["AAPL"]["facts"]["file"]
        altered.write_text("corrupt", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            list(load_all_sec()[1])
        entries["AAPL"]["facts"] = self.write_response(
            "wrong-issuer", {"cik": 789019, "facts": {}}
        )
        index.write_text(json.dumps({"companies": entries}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identity"):
            list(load_all_sec()[1])

    def test_failed_refresh_keeps_old_index_and_resumes_new_downloads(self):
        """Retain the published snapshot until every refreshed company is cached."""
        entries = {}
        for ticker, issuer in self.issuers.items():
            facts = self.write_response(
                ticker + "-facts", {"cik": int(issuer["cik"]), "facts": {}}
            )
            entries[ticker] = {**issuer, "facts": facts}
        index = self.cache / "all-index.json"
        original = json.dumps({"companies": entries})
        index.write_text(original, encoding="utf-8")

        def download(url, previous=None, json_response=True):
            ticker = "AAPL" if "AAPL" in url or "320193" in url else "MSFT"
            if ticker == "MSFT":
                raise ValueError("SEC HTTP 503")
            key = "submissions" if "submission" in url else "facts"
            return entries[ticker][key]

        with patch("app.data_pipeline.sec_catalogue.cached", side_effect=download):
            with self.assertRaisesRegex(ValueError, "503"):
                fetch_all_sec(refresh=True)
        self.assertEqual(index.read_text(encoding="utf-8"), original)

        def resume(url, previous=None, json_response=True):
            ticker = "AAPL" if "AAPL" in url or "320193" in url else "MSFT"
            key = "submissions" if "submission" in url else "facts"
            if ticker == "AAPL":
                self.assertEqual(previous, entries[ticker][key])
            else:
                self.assertIsNone(previous)
            return entries[ticker][key]

        with patch("app.data_pipeline.sec_catalogue.cached", side_effect=resume):
            self.assertEqual(fetch_all_sec()["status"], "downloaded")
        checkpoint = json.loads((self.cache / "progress.json").read_text())
        self.assertFalse(checkpoint["pending_refresh"])

    def test_predecessor_failure_preserves_published_snapshot_and_refresh_checkpoint(
        self,
    ):
        """Do not publish refreshed companies or finish refresh until predecessor downloads succeed."""
        entries = {}
        for ticker, issuer in self.issuers.items():
            facts = self.write_response(
                ticker + "-facts", {"cik": int(issuer["cik"]), "facts": {}}
            )
            entries[ticker] = {**issuer, "facts": facts}
        index = self.cache / "all-index.json"
        original = json.dumps({"companies": entries})
        index.write_text(original, encoding="utf-8")

        def download(url, previous=None, json_response=True):
            ticker = "AAPL" if "AAPL" in url or "320193" in url else "MSFT"
            key = "submissions" if "submission" in url else "facts"
            return entries[ticker][key]

        with (
            patch("app.data_pipeline.sec_catalogue.cached", side_effect=download),
            patch(
                "app.data_pipeline.sec_history.fetch_history_sources",
                side_effect=ValueError("Predecessor HTTP 503"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "Predecessor HTTP 503"):
                fetch_all_sec(refresh=True)
        self.assertEqual(index.read_text(encoding="utf-8"), original)
        checkpoint = json.loads((self.cache / "progress.json").read_text())
        self.assertTrue(checkpoint["pending_refresh"])
        self.assertEqual(set(checkpoint["companies"]), set(self.issuers))

        with (
            patch("app.data_pipeline.sec_catalogue.cached", side_effect=download),
            patch(
                "app.data_pipeline.sec_history.fetch_history_sources", return_value=1
            ) as predecessors,
        ):
            self.assertEqual(fetch_all_sec()["predecessors"], 1)
        predecessors.assert_called_once_with(self.issuers, refresh=True)
        checkpoint = json.loads((self.cache / "progress.json").read_text())
        self.assertFalse(checkpoint["pending_refresh"])

    def test_historical_symbol_requires_filing_evidence_for_the_same_cik(self):
        """Accept a former ticker only when inline XBRL confirms both ticker and issuer."""
        path = self.cache / "annual.htm"
        path.write_text(
            '<ix:nonNumeric name="dei:TradingSymbol">OLD</ix:nonNumeric>'
            '<ix:nonNumeric name="dei:EntityCentralIndexKey">320193</ix:nonNumeric>',
            encoding="utf-8",
        )
        entry = {
            "cik": "0000320193",
            "evidence": {"file": path.name, "sha256": sha256(path)},
        }
        submission = {"cik": 320193, "tickers": ["NEW"]}
        verify_submission("OLD", entry, submission)
        with self.assertRaises(ValueError):
            verify_submission("ANOTHER", entry, submission)
        with self.assertRaises(ValueError):
            verify_submission("OLD", entry, {"cik": 789019, "tickers": ["OLD"]})

    def test_decimal_response_keeps_zero_and_long_fractional_values(self):
        """Serialize financial numbers as plain decimal strings, including exact zero."""
        for value, expected in (
            ("0E-10", "0.0000000000"),
            ("1234567890123.1234567890", "1234567890123.1234567890"),
        ):
            with self.subTest(value=value):
                metric = AnnualMetric(status="ready", value=Decimal(value), variants=[])
                self.assertEqual(metric.model_dump(mode="json")["value"], expected)
