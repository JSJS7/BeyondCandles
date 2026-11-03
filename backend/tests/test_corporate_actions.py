"""Independent event examples for the reviewed DHR price convention."""

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.data_pipeline import corporate_actions as actions
from app.data_pipeline.source_files import sha256


class CorporateActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.folder = self.root / "data/raw/benjaminpo-v81/evidence"
        self.folder.mkdir(parents=True)
        patcher = patch.object(actions, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def evidence(self, name, role, value):
        path = self.folder / name
        if isinstance(value, dict):
            value = json.dumps(value)
        path.write_text(value, encoding="utf-8")
        return {
            "role": role,
            "file": str(path.relative_to(self.root)),
            "sha256": sha256(path),
        }

    def fixture(self):
        ticker = "DHR"
        dates = ["2016-07-01", "2016-07-05"]
        closes = ["68.7712020874", "71.2765960693"]
        adjusted = ["42.0096702576", "67.7273254395"]
        stamp = 1467725400
        amount = "24.56"
        splits = {str(stamp): {"date": stamp, "numerator": 1319, "denominator": 1000}}
        report = "<p>Stock-based awards were multiplied by a factor of 1.32.</p>"
        rows = []
        for day, close, value in zip(dates, closes, adjusted, strict=True):
            rows.append(
                {"date": day, "close": close, "adjusted_close": value, "volume": "100"}
            )
        body = {
            "chart": {
                "result": [
                    {
                        "meta": {"symbol": ticker},
                        "events": {
                            "dividends": {
                                str(stamp): {"date": stamp, "amount": amount}
                            },
                            "splits": splits,
                        },
                    }
                ]
            }
        }
        review = {
            "ticker": ticker,
            "status": "reviewed",
            "note": "Documented price convention",
            "method": "remove_duplicate_distribution",
            "ex_date": dates[1],
            "original_amount": amount,
            "evidence_files": [
                self.evidence("vendor.json", "vendor_actions", body),
                self.evidence("report.html", "annual_report", report),
            ],
        }
        return rows, review

    def test_dhr_duplicate_factor_is_removed_once_without_changing_close_or_anchor(
        self,
    ):
        """The 61.22% source jump becomes 3.6431% on the retained vendor conversion basis."""
        rows, review = self.fixture()
        original = copy.deepcopy(rows)
        changed = actions.correct_spin_off(rows, review)
        observed = (
            Decimal(rows[1]["adjusted_close"]) / Decimal(rows[0]["adjusted_close"]) - 1
        )
        self.assertAlmostEqual(float(observed), 0.0364309, places=6)
        self.assertEqual(changed, 1)
        self.assertEqual(
            Decimal(rows[1]["adjusted_close"]), Decimal(original[1]["adjusted_close"])
        )
        for before, after in zip(original, rows, strict=True):
            self.assertEqual(before["close"], after["close"])
            self.assertEqual(before["volume"], after["volume"])

    def test_a_second_correction_is_rejected_instead_of_double_adjusting(self):
        """A reviewed raw-source transition cannot be applied twice to the same series."""
        rows, review = self.fixture()
        actions.correct_spin_off(rows, review)
        with self.assertRaisesRegex(ValueError, "Source prices differ"):
            actions.correct_spin_off(rows, review)

    def test_dhr_missing_conversion_cannot_authorize_removing_a_distribution(self):
        """Removing cash requires evidence that Close already includes the spin-off conversion."""
        rows, review = self.fixture()
        vendor = self.folder / "vendor.json"
        body = json.loads(vendor.read_text(encoding="utf-8"))
        body["chart"]["result"][0]["events"]["splits"] = {}
        vendor.write_text(json.dumps(body), encoding="utf-8")
        review["evidence_files"][0]["sha256"] = sha256(vendor)
        with self.assertRaisesRegex(ValueError, "separately recorded"):
            actions.correct_spin_off(rows, review)

    def test_evidence_modification_blocks_the_correction(self):
        """Changed archived evidence cannot authorize a price rewrite."""
        rows, review = self.fixture()
        (self.folder / "report.html").write_text("Changed report", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            actions.correct_spin_off(rows, review)

    def test_corporate_action_from_another_issuer_is_rejected(self):
        """Matching dates and amounts are insufficient if the vendor document is foreign."""
        rows, review = self.fixture()
        review["ticker"] = "MMM"
        with self.assertRaisesRegex(ValueError, "another issuer"):
            actions.correct_spin_off(rows, review)

    def test_rounded_annual_benchmark_accepts_rounding_and_rejects_large_error(self):
        """An 11.26% annual change fits the rounded 2.00-to-2.23 graph; 73% does not."""
        _, review = self.fixture()
        review["benchmark"] = {
            "start_date": "2015-12-31",
            "end_date": "2016-12-30",
            "reported_start": "2.00",
            "reported_end": "2.23",
            "rounding_half_unit": "0.005",
        }
        review["evidence_files"].append(
            self.evidence("graph.pdf", "annual_performance", "Archived graph")
        )
        rows = [
            {"date": "2015-12-31", "adjusted_close": "100"},
            {"date": "2016-12-30", "adjusted_close": "111.26"},
        ]
        actions.verify_dhr_benchmark(rows, review)
        rows[1]["adjusted_close"] = "173"
        with self.assertRaisesRegex(ValueError, "annual performance graph"):
            actions.verify_dhr_benchmark(rows, review)
