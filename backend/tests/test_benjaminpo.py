"""Regression tests for pinned downloads, session dates and candidate review gates."""

import csv
import hashlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from app.data_pipeline import benjaminpo as source
from app.data_pipeline.source_files import sha256


class BenjaminpoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.selection = self.root / "selection.json"
        self.selection.write_text(
            json.dumps({"companies": [{"Symbol": "AAPL"}]}), encoding="utf-8"
        )
        self.manifest = {
            "id": "kaggle-benjaminpo-v81",
            "version": 81,
            "company_count": 1,
            "selection_sha256": sha256(self.selection),
            "period_start": "2020-01-02",
            "period_end": "2020-01-03",
            "trading_dates": 2,
            "price_rows": 2,
            "calendar_sha256": hashlib.sha256(b"2020-01-02\n2020-01-03").hexdigest(),
            "files": {},
            "reviews": {},
        }
        self.candidate = self.root / "candidate.json"
        for name, value in (
            ("ROOT", self.root),
            ("RAW", self.raw),
            ("SELECTION", self.selection),
            ("CANDIDATE", self.candidate),
            ("OUTPUT", self.root / "processed/prices.csv"),
        ):
            patcher = patch.object(source, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_history(self, *, rows=None):
        if rows is None:
            rows = [
                ["2020-01-02T00:00:00+0000", "10", "12", "9", "11", "5", "100"],
                ["2020-01-03T00:00:00+0000", "11", "13", "10", "12", "6", "200"],
            ]
        path = self.raw / "AAPL.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Datetime", *source.PRICE_COLUMNS, "Volume"])
            writer.writerows(rows)
        self.manifest["files"]["AAPL"] = {
            "file": "raw/AAPL.csv",
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
            "source_name": source.source_name("AAPL"),
            "url": source.download_url("AAPL"),
        }
        self.manifest["sha256"] = source.files_digest(self.manifest["files"])
        source.write_json(self.candidate, self.manifest)
        return path

    def write_catalogue(self, rows=None):
        """Build a tiny versioned metadata file with the same provenance checks as the real catalogue."""
        selected = {"Symbol": "AAPL", "Longname": "Apple Inc.", "Sector": "Technology"}
        self.selection.write_text(
            json.dumps({"companies": [selected]}), encoding="utf-8"
        )
        self.manifest["selection_sha256"] = sha256(self.selection)
        if rows is None:
            rows = [{**selected, "Exchange": "NMS"}]
        path = self.root / "company-metadata.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["Symbol", "Longname", "Exchange", "Sector"]
            )
            writer.writeheader()
            writer.writerows(rows)
        self.manifest["company_metadata_source"] = {
            "file": path.name,
            "sha256": sha256(path),
        }
        return path

    def test_catalogue_can_be_restored_without_the_old_price_archive(self):
        """Read the complete selected catalogue from the small tracked CSV, with exact names and exchange."""
        self.write_catalogue()

        companies = source.load_company_rows(self.manifest)

        self.assertEqual(
            companies,
            {
                "AAPL": {
                    "Symbol": "AAPL",
                    "Longname": "Apple Inc.",
                    "Exchange": "NMS",
                    "Sector": "Technology",
                }
            },
        )
        self.assertEqual(list(self.root.glob("*.zip")), [])

    def test_catalogue_checksum_and_frozen_selection_are_required(self):
        """Changed metadata bytes or a different company selection must stop import preparation."""
        for changed in ("metadata", "selection"):
            with self.subTest(changed=changed):
                metadata = self.write_catalogue()
                path = metadata if changed == "metadata" else self.selection
                with path.open("a", encoding="utf-8") as handle:
                    handle.write("\n")

                with self.assertRaisesRegex(ValueError, "checksum"):
                    source.load_company_rows(self.manifest)

    def test_missing_duplicate_or_conflicting_catalogue_rows_are_rejected(self):
        """A valid hash cannot approve missing companies, changed names/sectors or empty exchanges."""
        valid = {
            "Symbol": "AAPL",
            "Longname": "Apple Inc.",
            "Exchange": "NMS",
            "Sector": "Technology",
        }
        invalid = (
            [],
            [valid, valid],
            [dict(valid, Symbol="OTHER")],
            [dict(valid, Longname="Another company")],
            [dict(valid, Sector="Energy")],
            [dict(valid, Exchange="")],
        )
        for rows in invalid:
            with self.subTest(rows=rows):
                self.write_catalogue(rows)

                with self.assertRaisesRegex(ValueError, "metadata"):
                    source.load_company_rows(self.manifest)

    def test_old_source_or_conflicting_manifest_cannot_enter_price_import(self):
        """The active importer has one pinned source and refuses the removed Andrew path."""
        from app.data_pipeline import prices

        self.write_history()
        active = self.root / "active.json"
        for change in ({"id": "kaggle-andrew-v1022"}, {"currency": "EUR"}):
            with self.subTest(change=change):
                source.write_json(active, {**self.manifest, **change})
                with patch.object(prices, "MANIFEST", active):
                    with self.assertRaises(ValueError):
                        prices.validate_prices()

    def test_aliases_change_only_download_names(self):
        """Historical tickers retain identity while source filenames use new symbols."""
        for ticker, expected in (
            ("FI", "FISV"),
            ("MMC", "MRSH"),
            ("BK", "BNY"),
            ("AAPL", "AAPL"),
        ):
            with self.subTest(ticker=ticker):
                self.assertEqual(
                    source.source_name(ticker), f"stocks_us/1d/{expected}.csv"
                )
                self.assertTrue(
                    source.download_url(ticker).endswith("datasetVersionNumber=81")
                )

    def test_utc_midnight_is_a_session_label_not_previous_new_york_day(self):
        """UTC date labels preserve both sessions; converting zones would shift dates."""
        self.write_history()
        rows = source.read_history("AAPL", self.manifest)
        self.assertEqual([row["date"] for row in rows], ["2020-01-02", "2020-01-03"])
        self.assertEqual(rows[0]["adjusted_close"], "5")

    def test_corrupt_cached_bytes_are_rejected(self):
        """Source checksums prevent silently using a modified cached file."""
        path = self.write_history()
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            source.read_history("AAPL", self.manifest)

    def test_duplicate_session_is_rejected(self):
        """Two observations for the same company/session cannot enter preparation."""
        row = ["2020-01-02T00:00:00+0000", "10", "12", "9", "11", "5", "100"]
        self.write_history(rows=[row, row])
        with self.assertRaisesRegex(ValueError, "Duplicate price"):
            source.read_history("AAPL", self.manifest)

    def test_missing_session_and_invalid_ohlc_are_rejected(self):
        """Calendar completeness and OHLC constraints fail independently."""
        for rows, message in (
            (
                [["2020-01-02T00:00:00+0000", "10", "12", "9", "11", "5", "100"]],
                "calendar mismatch",
            ),
            ([["2020-01-02T00:00:00+0000", "10", "8", "9", "11", "5", "100"]], "OHLC"),
        ):
            with self.subTest(message=message):
                self.write_history(rows=rows)
                with self.assertRaisesRegex(ValueError, message):
                    source.read_history("AAPL", self.manifest)

    def test_unknown_session_timezone_is_rejected(self):
        """An unexpected time zone must not silently redefine a trading session."""
        self.write_history(
            rows=[["2020-01-02T00:00:00-0500", "10", "12", "9", "11", "5", "100"]]
        )
        with self.assertRaisesRegex(ValueError, "timestamp"):
            source.read_history("AAPL", self.manifest)

    def test_cached_download_is_idempotent_and_pinned(self):
        """A repeated fetch uses verified cached bytes and never asks for latest data."""
        path = self.write_history()
        before = path.read_bytes()
        with patch.object(
            source,
            "download_file",
            side_effect=AssertionError("Unexpected network request"),
        ):
            result = source.fetch_prices()
        self.assertEqual(result["downloaded"], 0)
        self.assertEqual(path.read_bytes(), before)

    def test_single_file_download_archive_returns_the_original_csv_bytes(self):
        """Kaggle may wrap an individual daily CSV in ZIP; this is independent of the removed Andrew archive."""
        csv_bytes = b"Datetime,Open,High,Low,Close,Adj Close,Volume\n2020-01-02T00:00:00+0000,10,12,9,11,5,100\n"
        response = io.BytesIO()
        with ZipFile(response, "w") as archive:
            archive.writestr("AAPL.csv", csv_bytes)
        with patch.object(
            source, "urlopen", return_value=io.BytesIO(response.getvalue())
        ):
            downloaded = source.download_file("AAPL")

        self.assertEqual(downloaded, csv_bytes)

    def test_html_download_is_not_cached_as_csv(self):
        """An HTTP success containing a login page is still an invalid price download."""
        with patch.object(
            source, "urlopen", return_value=io.BytesIO(b"<html>login</html>")
        ):
            with self.assertRaisesRegex(ValueError, "Unexpected CSV"):
                source.download_file("AAPL")

    def amd_quote_fixture(self):
        """Keep the defective vendor row separate from the pinned replacement quote."""
        original = dict.fromkeys(source.FIELDS[2:7], "2.6700000763")
        original["volume"] = "0"
        replacement = {
            "open": "2.70",
            "high": "2.73",
            "low": "2.64",
            "close": "2.69",
            "adjusted_close": "2.69",
            "volume": "8330255",
        }
        quote = {"act_symbol": "AMD", "date": "2015-01-02"}
        quote.update(
            {
                key: value
                for key, value in replacement.items()
                if key != "adjusted_close"
            }
        )
        evidence = self.root / "amd-quote.json"
        source.write_json(
            evidence, {"query_execution_status": "Success", "rows": [quote]}
        )
        document = self.root / "close-proof.pdf"
        document.write_bytes(b"fixture: independently reviewed closing price")
        self.manifest["reviews"]["AMD"] = {
            "status": "reviewed",
            "method": "verified_quote_replacement",
            "date": "2015-01-02",
            "quoted_symbol": "AMD",
            "expected": original,
            "replacement": replacement,
            "evidence": [
                {
                    "role": "ohlcv",
                    "format": "dolthub_sql",
                    "file": evidence.name,
                    "sha256": sha256(evidence),
                },
                {
                    "role": "independent_close",
                    "format": "document",
                    "file": document.name,
                    "sha256": sha256(document),
                },
            ],
        }
        rows = [
            {"ticker": "AMD", "date": "2015-01-02", **original},
            {"ticker": "AMD", "date": "2015-01-05", **original, "volume": "123"},
        ]
        return rows, evidence

    def test_amd_quote_replaces_only_the_verified_session(self):
        """Repair the missing first-session bar, retaining other dates and the source file."""
        rows, evidence = self.amd_quote_fixture()
        next_session = dict(rows[1])
        original_bytes = evidence.read_bytes()

        changed = source.correct_quote(rows, self.manifest, "AMD")

        self.assertEqual(changed, 1)
        self.assertEqual(rows[0]["date"], "2015-01-02")
        self.assertEqual(rows[0]["ticker"], "AMD")
        self.assertEqual(rows[0]["close"], "2.69")
        self.assertEqual(rows[0]["adjusted_close"], "2.69")
        self.assertEqual(rows[0]["volume"], "8330255")
        self.assertEqual(rows[1], next_session)
        self.assertEqual(evidence.read_bytes(), original_bytes)

    def test_amd_quote_refuses_unexpected_vendor_values_or_missing_session(self):
        """A changed source or absent target must stop preparation instead of hiding a conflict."""
        for missing in (False, True):
            with self.subTest(missing=missing):
                rows, _ = self.amd_quote_fixture()
                if missing:
                    rows = rows[1:]
                else:
                    rows[0]["volume"] = "100"
                before = [dict(row) for row in rows]

                with self.assertRaisesRegex(ValueError, "AMD.*(session|source)"):
                    source.correct_quote(rows, self.manifest, "AMD")

                self.assertEqual(rows, before)

    def test_amd_quote_requires_unchanged_evidence_and_matching_replacement(self):
        """Changed evidence bytes or a correction that disagrees with the quoted bar are rejected."""
        for altered in ("evidence", "replacement", "issuer"):
            with self.subTest(altered=altered):
                rows, evidence = self.amd_quote_fixture()
                review = self.manifest["reviews"]["AMD"]
                if altered == "evidence":
                    evidence.write_bytes(b"changed")
                elif altered == "replacement":
                    review["replacement"]["close"] = "3.00"
                else:
                    document = source.read_json(evidence)
                    document["rows"][0]["act_symbol"] = "AAPL"
                    source.write_json(evidence, document)
                    review["evidence"][0]["sha256"] = sha256(evidence)

                with self.assertRaises(ValueError):
                    source.correct_quote(rows, self.manifest, "AMD")

    def test_amd_quote_does_not_assume_adjusted_close_equals_close_on_another_basis(
        self,
    ):
        """A new dividend/split basis must be reviewed before applying this frozen correction."""
        rows, _ = self.amd_quote_fixture()
        rows[1]["adjusted_close"] = "2.00"

        with self.assertRaisesRegex(ValueError, "AMD.*basis"):
            source.correct_quote(rows, self.manifest, "AMD")

    def fi_quote_fixture(self):
        """A vendor history without 2025-11-12 and two pinned exchange quote histories."""
        replacement = {
            "open": "64.20",
            "high": "64.87",
            "low": "63.11",
            "close": "64.38",
            "adjusted_close": "64.38",
            "volume": "6244651",
        }
        nasdaq = self.root / "fi-nasdaq.json"
        source.write_json(
            nasdaq,
            {
                "data": {
                    "symbol": "FISV",
                    "tradesTable": {
                        "rows": [
                            {
                                "date": "11/12/2025",
                                "close": "$64.38",
                                "volume": "6,244,651",
                                "open": "$64.20",
                                "high": "$64.87",
                                "low": "$63.11",
                            }
                        ]
                    },
                }
            },
        )
        cboe = self.root / "fi-cboe.json"
        source.write_json(
            cboe,
            {
                "symbol": "FISV",
                "data": [
                    {
                        "date": "2025-11-12",
                        "volume": 6244651,
                        "open": 64.2,
                        "high": 64.87,
                        "low": 63.11,
                        "close": 64.38,
                    }
                ],
            },
        )
        self.manifest["reviews"]["FI"] = {
            "status": "reviewed",
            "method": "verified_quote_insertion",
            "date": "2025-11-12",
            "quoted_symbol": "FISV",
            "replacement": replacement,
            "evidence": [
                {
                    "role": "ohlcv",
                    "format": "nasdaq_historical",
                    "file": nasdaq.name,
                    "sha256": sha256(nasdaq),
                },
                {
                    "role": "independent_close",
                    "format": "cboe_historical",
                    "file": cboe.name,
                    "sha256": sha256(cboe),
                },
            ],
        }
        neighbour = dict.fromkeys(source.FIELDS[2:7], "64.26")
        rows = [
            {"ticker": "FI", "date": "2025-11-11", **neighbour, "volume": "5427200"},
            {"ticker": "FI", "date": "2025-11-13", **neighbour, "volume": "9274500"},
        ]
        return rows, cboe

    def test_fi_quote_inserts_only_the_verified_missing_session(self):
        """Insert the absent 2025-11-12 bar in date order; neighbouring sessions stay unchanged."""
        rows, _ = self.fi_quote_fixture()
        neighbours = [dict(row) for row in rows]

        changed = source.correct_quote(rows, self.manifest, "FI")

        self.assertEqual(changed, 1)
        self.assertEqual(
            [row["date"] for row in rows], ["2025-11-11", "2025-11-12", "2025-11-13"]
        )
        self.assertEqual(rows[1]["ticker"], "FI")
        self.assertEqual(rows[1]["close"], "64.38")
        self.assertEqual(rows[1]["adjusted_close"], "64.38")
        self.assertEqual(rows[1]["volume"], "6244651")
        self.assertEqual([rows[0], rows[2]], neighbours)

    def test_fi_quote_insertion_refuses_conflicting_source_or_evidence(self):
        """An existing vendor bar, another issuer or a different second close stops preparation."""
        for altered in ("vendor_has_session", "issuer", "independent_close"):
            with self.subTest(altered=altered):
                rows, cboe = self.fi_quote_fixture()
                review = self.manifest["reviews"]["FI"]
                if altered == "vendor_has_session":
                    rows.insert(1, {**rows[0], "date": "2025-11-12"})
                elif altered == "issuer":
                    review["quoted_symbol"] = "FI"
                else:
                    document = source.read_json(cboe)
                    document["data"][0]["close"] = 64.39
                    source.write_json(cboe, document)
                    review["evidence"][1]["sha256"] = sha256(cboe)
                before = [dict(row) for row in rows]

                with self.assertRaisesRegex(ValueError, "FI|FISV"):
                    source.correct_quote(rows, self.manifest, "FI")

                self.assertEqual(rows, before)

    def test_calendar_accepts_a_missing_session_only_with_a_reviewed_insertion(self):
        """The vendor gap is allowed in the frozen calendar only through an approved insertion."""
        self.write_history(
            rows=[["2020-01-02T00:00:00+0000", "10", "12", "9", "11", "5", "100"]]
        )
        with self.assertRaisesRegex(ValueError, "calendar mismatch"):
            source.read_history("AAPL", self.manifest)

        self.manifest["reviews"]["AAPL"] = {
            "method": "verified_quote_insertion",
            "date": "2020-01-03",
        }
        rows = source.read_history("AAPL", self.manifest)

        self.assertEqual([row["date"] for row in rows], ["2020-01-02"])

    def test_avgo_uses_verified_cash_once_and_preserves_vendor_anchor(self):
        """A duplicated 0.175 dividend is rebuilt once; post-period anchor stays 90."""
        self.manifest["sha256"] = "a" * 64
        policy = self.root / "rules.json"
        policy.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source_sha256": self.manifest["sha256"],
                    "selection_sha256": self.manifest["selection_sha256"],
                    "period_end": self.manifest["period_end"],
                    "companies": {
                        "AVGO": {
                            "method": "cash_dividends",
                            "dividends": [
                                {
                                    "ex_date": "2018-03-21",
                                    "amount": "1.75",
                                    "split_divisor": "10",
                                }
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.manifest["reviews"]["AVGO"] = {
            "status": "reviewed",
            "method": "cash_dividends_preserve_vendor_anchor",
            "rules_file": "rules.json",
            "rules_sha256": sha256(policy),
        }
        rows = [
            {"date": "2018-03-20", "close": "100", "adjusted_close": "89.685"},
            {"date": "2018-03-21", "close": "100", "adjusted_close": "90"},
        ]
        source.correct_avgo(rows, self.manifest)
        self.assertEqual(rows[0]["adjusted_close"], "89.842500000000000000")
        self.assertEqual(rows[1]["adjusted_close"], "90.000000000000000000")

    def test_avgo_policy_cannot_be_reused_for_another_source_snapshot(self):
        """Correct evidence bytes still require matching source, company selection and anchor metadata."""
        self.write_history()
        policy = self.root / "rules.json"
        source.write_json(
            policy,
            {
                "version": 1,
                "source_sha256": "a different source",
                "selection_sha256": self.manifest["selection_sha256"],
                "period_end": self.manifest["period_end"],
                "companies": {},
            },
        )
        self.manifest["reviews"]["AVGO"] = {
            "rules_file": policy.name,
            "rules_sha256": sha256(policy),
        }

        with self.assertRaisesRegex(ValueError, "policy differs"):
            source.correct_avgo([], self.manifest)

    def test_missing_dividend_must_match_the_pinned_evidence(self):
        """A reviewed amount that differs from the pinned DoltHub row stops preparation."""
        evidence = self.root / "dividends.json"
        source.write_json(
            evidence,
            {
                "query_execution_status": "Success",
                "rows": [
                    {"act_symbol": "CB", "ex_date": "2025-09-12", "amount": "0.97000"}
                ],
            },
        )
        rule = {
            "status": "reviewed",
            "method": "missing_cash_dividends",
            "evidence": {
                "format": "dolthub_sql",
                "file": evidence.name,
                "sha256": sha256(evidence),
            },
            "dividends": [
                {"ex_date": "2025-09-12", "amount": "0.97", "split_divisor": "1"}
            ],
        }
        rows = [
            {
                "ticker": "CB",
                "date": "2025-09-11",
                "close": "100",
                "adjusted_close": "99",
            },
            {
                "ticker": "CB",
                "date": "2025-09-12",
                "close": "98",
                "adjusted_close": "97.02",
            },
        ]

        added = source.correct_missing_dividends(
            rows, {"companies": {"CB": rule}}, "CB"
        )

        self.assertEqual(added, 1)
        self.assertEqual(
            Decimal(rows[0]["adjusted_close"]), Decimal("99") * Decimal("0.9903")
        )
        self.assertEqual(rows[1]["adjusted_close"], "97.02")

        rule["dividends"][0]["amount"] = "0.98"
        with self.assertRaisesRegex(ValueError, "CB dividend 2025-09-12 differs"):
            source.correct_missing_dividends(rows, {"companies": {"CB": rule}}, "CB")

    def test_dividend_change_requires_its_pinned_evidence(self):
        """A reviewed removal runs only while the files it relies on are unchanged."""
        evidence = self.root / "filing.htm"
        evidence.write_text("one dividend of 0.43", encoding="utf-8")
        rule = {
            "status": "reviewed",
            "method": "reviewed_dividend_changes",
            "evidence": [{"file": evidence.name, "sha256": sha256(evidence)}],
            "changes": [
                {
                    "ex_date": "2020-10-29",
                    "vendor_amount": "1",
                    "amount": "0",
                    "split_divisor": "1",
                }
            ],
        }
        rows = [
            {"date": "2020-10-28", "close": "100", "adjusted_close": "99"},
            {"date": "2020-10-29", "close": "100", "adjusted_close": "100"},
        ]
        policy = {"dividend_changes": {"HCA": rule}}

        changed = source.correct_dividend_changes(rows, policy, "HCA")

        self.assertEqual(changed, 1)
        self.assertEqual(Decimal(rows[0]["adjusted_close"]), Decimal(100))
        self.assertEqual(rows[1]["adjusted_close"], "100")

        evidence.write_text("edited", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "HCA dividend change evidence"):
            source.correct_dividend_changes(rows, policy, "HCA")

        rule["evidence"] = []
        with self.assertRaisesRegex(ValueError, "HCA dividend change has no evidence"):
            source.correct_dividend_changes(rows, policy, "HCA")

    def test_dividend_change_rule_nothing_applies_to_stops_preparation(self):
        """A misspelt method or a company outside the catalogue is an error, not a skip."""
        policy = {"dividend_changes": {"COR": {"method": "reviewed_dividend_changes"}}}
        self.assertEqual(source.dividend_change_tickers(policy, ["COR"]), ["COR"])
        with self.assertRaisesRegex(ValueError, "outside the catalogue: COR"):
            source.dividend_change_tickers(policy, ["T"])

        policy["dividend_changes"]["COR"]["method"] = "other"
        with self.assertRaisesRegex(ValueError, "Unknown dividend change method"):
            source.dividend_change_tickers(policy, ["COR"])
        self.assertEqual(source.dividend_change_tickers({"companies": {}}, []), [])

    def test_review_or_cash_rule_no_correction_applies_stops_preparation(self):
        """A reviewed correction that nothing would apply is an error, not a silent skip.

        Corrections were chosen by tickers written in the code, so a reviewed quote
        for another company, an unknown kind of review, a misspelt rule method or a
        rule for a company outside the catalogue left the prices uncorrected.
        """
        quote = {"status": "reviewed", "method": "verified_quote_replacement"}
        self.manifest["reviews"] = {"XYZ": quote, "DHR": {"status": "reviewed"}}

        self.assertEqual(
            source.review_corrections(self.manifest),
            {"XYZ": "quote", "DHR": "spin_off"},
        )

        self.manifest["reviews"]["XOM"] = {"status": "reviewed", "method": "other"}
        with self.assertRaisesRegex(ValueError, "No price correction.*XOM"):
            source.review_corrections(self.manifest)

        policy = {
            "companies": {
                "AVGO": {"method": "cash_dividends"},
                "CB": {"method": "missing_cash_dividends"},
            }
        }
        self.assertEqual(
            source.missing_dividend_tickers(policy, ["AVGO", "CB"]), ["CB"]
        )
        with self.assertRaisesRegex(ValueError, "outside the catalogue: CB"):
            source.missing_dividend_tickers(policy, ["AVGO"])

        policy["companies"]["CB"]["method"] = "missing_cash_dividend"
        with self.assertRaisesRegex(
            ValueError, "Unknown cash-adjustment method for CB"
        ):
            source.missing_dividend_tickers(policy, ["AVGO", "CB"])

    def test_economic_review_blocks_numerically_valid_candidate(self):
        """Valid prices cannot bypass the unresolved DHR corporate-action gate."""
        self.write_history()
        self.manifest["reviews"] = {
            "DHR": {"status": "needs_review", "note": "Unresolved spin-off"}
        }
        source.write_json(self.candidate, self.manifest)
        with patch.object(source, "load_company_rows", return_value={}):
            _, _, summary = source.prepare_prices()
            self.assertEqual(summary["status"], "needs_review")
            with self.assertRaisesRegex(ValueError, "blocked.*DHR"):
                source.prepare_prices(require_reviewed=True)

    def test_summary_reports_the_adjustment_rules_hash(self):
        """The rules hash identifies price-adjustments.json, not the manifest.

        The summary previously repeated the manifest hash under this name, so an
        audit report could not show which rules file had been applied.
        """
        self.write_history()
        policy = self.root / "rules.json"
        source.write_json(
            policy,
            {
                "version": 1,
                "source_sha256": self.manifest["sha256"],
                "selection_sha256": self.manifest["selection_sha256"],
                "period_end": self.manifest["period_end"],
                "companies": {},
            },
        )
        self.manifest["reviews"]["AVGO"] = {
            "status": "reviewed",
            "rules_file": policy.name,
            "rules_sha256": sha256(policy),
        }
        source.write_json(self.candidate, self.manifest)

        with patch.object(source, "load_company_rows", return_value={}):
            _, _, summary = source.prepare_prices()

        self.assertEqual(summary["adjustment_rules_sha256"], sha256(policy))
        self.assertNotEqual(
            summary["adjustment_rules_sha256"], summary["manifest_sha256"]
        )

    def test_invalid_catalogue_preserves_previous_prepared_file(self):
        """A metadata error must not publish new prices when preparation as a whole failed."""
        self.write_history()
        source.OUTPUT.parent.mkdir(parents=True)
        source.OUTPUT.write_bytes(b"previous valid prepared snapshot\n")

        with patch.object(
            source, "load_company_rows", side_effect=ValueError("Invalid catalogue")
        ):
            with self.assertRaisesRegex(ValueError, "Invalid catalogue"):
                source.prepare_prices()

        self.assertEqual(
            source.OUTPUT.read_bytes(), b"previous valid prepared snapshot\n"
        )

    def test_invalid_candidate_preserves_previous_prepared_file(self):
        """An interrupted or invalid preparation cannot publish a partial replacement."""
        self.write_history()
        source.OUTPUT.parent.mkdir(parents=True)
        source.OUTPUT.write_bytes(b"previous prepared snapshot\n")
        with patch.object(
            source, "read_history", side_effect=ValueError("Invalid source")
        ):
            with self.assertRaisesRegex(ValueError, "Invalid source"):
                source.prepare_prices()
        self.assertEqual(source.OUTPUT.read_bytes(), b"previous prepared snapshot\n")

    def test_interrupted_download_resumes_without_replacing_first_cached_file(self):
        """A failed batch preserves its completed file and publishes no partial manifest."""
        self.selection.write_text(
            json.dumps(
                {
                    "companies": [
                        {"Symbol": "AAPL"},
                        {"Symbol": "MSFT"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.manifest["company_count"] = 2
        self.manifest["selection_sha256"] = sha256(self.selection)
        source.write_json(self.candidate, self.manifest)
        body = b"Datetime,Open,High,Low,Close,Adj Close,Volume\n"
        with patch.object(
            source, "download_file", side_effect=[body, OSError("Interrupted")]
        ):
            with self.assertRaisesRegex(OSError, "Interrupted"):
                source.fetch_prices()
        self.assertEqual(source.read_json(self.candidate)["files"], {})
        self.assertEqual((self.raw / "AAPL.csv").read_bytes(), body)
        with patch.object(source, "download_file", return_value=body) as download:
            result = source.fetch_prices()
        download.assert_called_once_with("MSFT")
        self.assertEqual(result["files"], 2)
        self.assertEqual(result["downloaded"], 1)

    def test_unpinned_version_is_rejected_before_network_access(self):
        """Changing manifest version cannot silently fetch a differently labelled source."""
        self.manifest["version"] = 82
        source.write_json(self.candidate, self.manifest)
        with patch.object(source, "download_file") as download:
            with self.assertRaisesRegex(ValueError, "version 81"):
                source.fetch_prices()
        download.assert_not_called()

    def test_event_report_preserves_source_values_before_in_place_correction(self):
        """A diagnostic source snapshot must not change when prepared rows are corrected."""
        rows = [
            {"date": "2020-01-02", "close": "10", "adjusted_close": "5"},
            {"date": "2020-01-03", "close": "11", "adjusted_close": "6"},
        ]
        before = source.event_summary(rows, "2020-01-03")
        rows[0]["adjusted_close"] = "8"
        self.assertEqual(before["previous"]["adjusted_close"], "5")
