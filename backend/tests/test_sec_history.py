"""Verify reviewed issuer mappings, preserved raw facts and fail-closed history selection."""

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import test_data_pipeline as pipeline_tests
from test_stocks_api import reported_fact

from app.annual_financials import annual_periods
from app.data_pipeline.sec_history import (
    history_eligible,
    prepare_company_facts,
    read_predecessor,
    validate_history_catalogue,
)
from app.data_pipeline.source_files import sha256


class HistoryRulesTests(unittest.TestCase):
    def test_primary_issuer_is_the_default_but_unmapped_issuers_are_excluded(self):
        """Use the approved primary CIK and reject another issuer without a reviewed link."""
        self.assertTrue(history_eligible(reported_fact(), "0000320193", None))
        other = reported_fact(source_cik="0000000042")
        self.assertFalse(history_eligible(other, "0000320193", None))

    def test_filing_boundary_preserves_later_comparisons_for_earlier_periods(self):
        """Separate a former issuer's reports from later predecessor comparisons, as in JCI."""
        history = {"status": "reviewed", "primary": {"filed_from": "2022-01-01"}}
        original = reported_fact()
        comparative = reported_fact(filed=date(2022, 2, 1))

        self.assertFalse(history_eligible(original, "0000320193", history))
        self.assertTrue(history_eligible(comparative, "0000320193", history))

    def test_predecessor_bounds_keep_source_periods_separate(self):
        """Allow a predecessor through its approved end date and reject later periods."""
        history = {
            "status": "reviewed",
            "primary": {"period_end_from": "2021-01-01"},
            "predecessors": [
                {"cik": "0000000042", "eligibility": {"period_end_to": "2020-12-31"}}
            ],
        }
        earlier = reported_fact(source_cik="0000000042")
        later = reported_fact(source_cik="0000000042", period_end=date(2021, 12, 31))

        self.assertTrue(history_eligible(earlier, "0000320193", history))
        self.assertFalse(history_eligible(later, "0000320193", history))
        self.assertFalse(history_eligible(reported_fact(), "0000320193", history))

    def test_unresolved_history_is_not_presented_as_approved(self):
        """Keep unresolved source assignments out of the selected financial history."""
        self.assertFalse(
            history_eligible(reported_fact(), "0000320193", {"status": "needs_review"})
        )

    def test_excluded_facts_never_define_rows_or_replace_selected_values(self):
        """Ignore raw facts outside the chosen history even if they were filed later."""
        excluded = reported_fact(history_eligible=False, filed=date(2023, 1, 1))
        self.assertEqual(annual_periods([excluded]), [])
        periods = annual_periods([reported_fact(), excluded])
        self.assertEqual(
            periods[0].metrics["net_income"].variants[0].filed, date(2021, 2, 1)
        )

    def test_different_issuers_do_not_silently_replace_each_other(self):
        """Retain an unexpected source overlap as variants requiring review."""
        other = reported_fact(source_cik="0000000042", filed=date(2023, 1, 1))
        metric = annual_periods([reported_fact(), other])[0].metrics["net_income"]
        self.assertEqual(metric.status, "needs_review")
        self.assertEqual(
            {v.source_cik for v in metric.variants}, {"0000320193", "0000000042"}
        )

    def test_invalid_rules_and_cross_company_predecessors_are_rejected(self):
        """Reject misspelled rules, reversed dates and links into another selected firm."""
        for rules in (
            {"filed_after": "2020-01-01"},
            {"filed_from": "2021-01-01", "filed_to": "2020-01-01"},
        ):
            with self.subTest(rules=rules):
                history = {
                    "status": "reviewed",
                    "note": "Example",
                    "evidence": [{}],
                    "primary": rules,
                }
                with self.assertRaises(ValueError):
                    validate_history_catalogue(
                        {"A": {"cik": "0000000001", "history": history}}
                    )
        history = {
            "status": "reviewed",
            "note": "Example",
            "evidence": [{}],
            "predecessors": [{"cik": "0000000002", "name": "Other", "submissions": {}}],
        }
        with self.assertRaisesRegex(ValueError, "another selected company"):
            validate_history_catalogue(
                {
                    "A": {"cik": "0000000001", "history": history},
                    "B": {"cik": "0000000002"},
                }
            )


class HistoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="beyondcandles-history-test-")
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)
        for module in (
            "app.data_pipeline.sec_history",
            "app.data_pipeline.sec_catalogue",
        ):
            patcher = patch(module + ".CACHE", self.cache)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.source = {
            "cik": "0000000042",
            "name": "Former company",
            "eligibility": {"period_end_to": "2020-12-31"},
        }
        body = pipeline_tests.SecTests().body()
        body["cik"] = 42
        self.index = {
            "issuers": {
                "0000000042": {
                    "submissions": self.write(
                        "submission.json", {"cik": 42, "name": "Former company"}
                    ),
                    "facts": self.write("facts.json", body),
                }
            }
        }

    def write(self, name, body):
        path = self.cache / name
        path.write_text(json.dumps(body), encoding="utf-8")
        return {"file": name, "sha256": sha256(path)}

    def test_preparation_retains_excluded_raw_facts_with_source_cik(self):
        """Keep both issuers' original facts while marking only the reviewed history eligible."""
        evidence = self.write("evidence.json", {"reviewed": True})
        history = {
            "status": "reviewed",
            "primary": {"filed_from": "2022-01-01"},
            "predecessors": [self.source],
            "evidence": [evidence],
        }
        facts = prepare_company_facts(
            {"cik": "0000320193"}, pipeline_tests.SecTests().body(), history, self.index
        )

        self.assertEqual(len(facts), 4)
        self.assertEqual(sum(f["history_eligible"] for f in facts), 3)
        self.assertEqual({f["source_cik"] for f in facts}, {"0000320193", "0000000042"})

    def test_missing_corrupt_or_wrong_identity_predecessor_stops_preparation(self):
        """Reject missing cache, modified bytes and a different CIK before importing anything."""
        with self.assertRaisesRegex(ValueError, "Missing predecessor"):
            read_predecessor(self.source, {"issuers": {}})
        (self.cache / "facts.json").write_text("corrupt", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_predecessor(self.source, self.index)
        self.index["issuers"]["0000000042"]["facts"] = self.write(
            "wrong.json", {"cik": 43}
        )
        with self.assertRaisesRegex(ValueError, "CIK mismatch"):
            read_predecessor(self.source, self.index)

    def test_coverage_audit_uses_approved_history_including_predecessors(self):
        """Count eligible predecessor facts in coverage while reporting excluded raw facts separately."""
        from app.data_pipeline.sec import audit_sec

        history = {
            "status": "reviewed",
            "note": "Reviewed predecessor",
            "primary": {"filed_from": "2022-01-01"},
            "predecessors": [self.source],
            "evidence": [self.write("evidence.json", {"reviewed": True})],
        }
        index_path = self.cache / "history-index.json"
        index_path.write_text(json.dumps(self.index), encoding="utf-8")
        entry = {
            "cik": "0000320193",
            "name": "Current company",
            "facts": {"sha256": "fixture"},
        }
        companies = [("TEST", (entry, pipeline_tests.SecTests().body()))]
        with (
            patch("app.data_pipeline.sec.ROOT", self.cache),
            patch(
                "app.data_pipeline.sec_catalogue.load_all_sec",
                return_value=({"retrieved_at": "fixture"}, iter(companies)),
            ),
            patch(
                "app.data_pipeline.sec_catalogue.identifiers",
                return_value={"TEST": {"history": history}},
            ),
            patch("app.data_pipeline.sec_history.HISTORY_INDEX", index_path),
        ):
            report = audit_sec()["companies"]["TEST"]

        self.assertEqual(report["raw_facts"], 4)
        self.assertEqual(report["history_facts"], 3)
        self.assertEqual(report["excluded_facts"], 1)
        self.assertEqual(report["source_ciks"], ["0000000042", "0000320193"])
        self.assertEqual(
            sum(metric["facts"] for metric in report["metrics"].values()), 3
        )
