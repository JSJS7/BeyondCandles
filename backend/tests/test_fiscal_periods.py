"""Verify fiscal labels, exact aliases and the preservation of original SEC dates."""

import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_stocks_api import reported_fact

from app.annual_financials import annual_periods
from app.data_pipeline.fiscal_periods import (
    cover_filename,
    label_candidates,
    parse_cover,
)
from app.fiscal_periods import canonical_period, period_key


def cover(cik="0000200406", year="2015", end="Jan. 03, 2016", form="10-K"):
    rows = [
        ("EntityCentralIndexKey", cik),
        ("DocumentFiscalYearFocus", year),
        ("DocumentPeriodEndDate", end),
        ("DocumentType", form),
    ]
    return (
        '<table class="report">'
        + "".join(
            "<tr><td><a onclick=\"show('defref_dei_"
            + tag
            + "')\">"
            + tag
            + "</a></td><td>"
            + value
            + "</td></tr>"
            for tag, value in rows
        )
        + "</table>"
    )


class FiscalCoverTests(unittest.TestCase):
    def test_cover_year_is_independent_of_the_calendar_end_year(self):
        """JNJ FY2015 ends in January 2016; its official cover must keep the 2015 label."""
        result = parse_cover(cover(), "0000200406")
        self.assertEqual(result, {"fiscal_year": 2015, "document_end": "2016-01-03"})

    def test_cover_requires_the_correct_issuer_and_an_annual_document(self):
        """Reject a mismatched issuer, quarterly filing and absent fiscal-year field."""
        for html, cik in (
            (cover(), "0000000001"),
            (cover(form="10-Q"), "0000200406"),
            (cover().replace("DocumentFiscalYearFocus", "Unknown"), "0000200406"),
        ):
            with self.subTest(cik=cik):
                with self.assertRaises(ValueError):
                    parse_cover(html, cik)

    def test_joint_cover_accepts_the_verified_issuer_when_fiscal_fields_agree(self):
        """A joint filing may list several CIKs while sharing one annual calendar."""
        extra = "<tr><td><a onclick=\"show('defref_dei_EntityCentralIndexKey')\">CIK</a></td><td>0000000001</td></tr>"
        html = cover().replace("</table>", extra + "</table>")
        self.assertEqual(parse_cover(html, "0000200406")["fiscal_year"], 2015)

    def test_stale_cover_year_requires_the_original_heading_and_matching_hash(self):
        """Correct the reviewed HON year only with its original annual-report evidence."""
        from app.data_pipeline.fiscal_periods import verified_cover
        from app.data_pipeline.source_files import sha256

        with TemporaryDirectory(prefix="beyondcandles-stale-year-") as directory:
            root = Path(directory)
            cover_path = root / "cover.htm"
            report = root / "annual.htm"
            cover_path.write_text(
                cover(cik="0000773840", year="2020", end="Dec. 31, 2021"),
                encoding="utf-8",
            )
            report.write_text(
                "<p>For the fiscal year ended December 31, 2021</p>", encoding="utf-8"
            )
            entry = {
                "cover": {
                    "file": cover_path.name,
                    "sha256": sha256(cover_path),
                    "url": "https://www.sec.gov/cover",
                },
                "original_report": {
                    "file": report.name,
                    "sha256": sha256(report),
                    "url": "https://www.sec.gov/annual",
                },
            }
            with patch("app.data_pipeline.sec_catalogue.CACHE", root):
                result = verified_cover("0000773840:0000773840-22-000018", entry)
                self.assertEqual(result["fiscal_year"], 2021)
                report.write_text("corrupt", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    verified_cover("0000773840:0000773840-22-000018", entry)

    def test_kroger_uses_the_explicit_fiscal_names_in_its_report(self):
        """KR calls the year ending February 2024 FY2023; its tagged 2024 is not the label."""
        from app.data_pipeline.fiscal_periods import verified_cover
        from app.data_pipeline.source_files import sha256

        with TemporaryDirectory(prefix="beyondcandles-kroger-year-") as directory:
            root = Path(directory)
            path = root / "cover.htm"
            report = root / "annual.htm"
            path.write_text(
                cover(cik="0000056873", year="2024", end="Feb. 03, 2024"),
                encoding="utf-8",
            )
            report.write_text(
                "<p>All references to 2023, 2022 and 2021 are to the fiscal years ended February 3, 2024, "
                "January 28, 2023 and January 29, 2022, respectively.</p>",
                encoding="utf-8",
            )
            entry = {
                "cover": {
                    "file": path.name,
                    "sha256": sha256(path),
                    "url": "https://www.sec.gov/cover",
                },
                "original_report": {
                    "file": report.name,
                    "sha256": sha256(report),
                    "url": "https://www.sec.gov/annual",
                },
            }
            with patch("app.data_pipeline.sec_catalogue.CACHE", root):
                result = verified_cover("0000056873:0001558370-24-004603", entry)
            self.assertEqual(
                result, {"fiscal_year": 2023, "document_end": "2024-02-03"}
            )

    def test_cover_discovery_does_not_confuse_recoverability_notes_with_cover(self):
        """Find Cover Page while ignoring a note whose name merely contains recovery."""
        summary = (
            "<FilingSummary><Report><ShortName>Cover Page</ShortName><HtmlFileName>R1.htm</HtmlFileName></Report>"
            "<Report><ShortName>Asset recoverability</ShortName><HtmlFileName>R20.htm</HtmlFileName></Report></FilingSummary>"
        )
        self.assertEqual(cover_filename(summary), "R1.htm")

    def test_comparison_years_follow_fiscal_distance_across_december_and_january(self):
        """Two full fiscal years may end in one calendar year without sharing an FY label."""
        from app.data_pipeline.source_files import sha256

        body = {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2014-12-29",
                                    "end": "2016-01-03",
                                    "form": "10-K",
                                    "accn": "annual",
                                    "filed": "2018-02-01",
                                },
                                {
                                    "start": "2016-01-04",
                                    "end": "2017-01-01",
                                    "form": "10-K",
                                    "accn": "annual",
                                    "filed": "2018-02-01",
                                },
                                {
                                    "start": "2017-01-02",
                                    "end": "2017-12-31",
                                    "form": "10-K",
                                    "accn": "annual",
                                    "filed": "2018-02-01",
                                },
                            ]
                        }
                    }
                }
            }
        }
        with TemporaryDirectory(prefix="beyondcandles-fiscal-test-") as directory:
            root = Path(directory)
            path = root / "cover.htm"
            path.write_text(cover(year="2017", end="Dec. 31, 2017"), encoding="utf-8")
            evidence = {
                "0000200406:annual": {
                    "cover": {
                        "file": path.name,
                        "sha256": sha256(path),
                        "url": "https://www.sec.gov/cover",
                    }
                }
            }
            with patch("app.data_pipeline.sec_catalogue.CACHE", root):
                labels = label_candidates(body, "0000200406", evidence)
            years = {
                end: labels[period_key("0000200406", start, end)][0]["fiscal_year"]
                for start, end in [
                    ("2014-12-29", "2016-01-03"),
                    ("2016-01-04", "2017-01-01"),
                    ("2017-01-02", "2017-12-31"),
                ]
            }
            self.assertEqual(
                years, {"2016-01-03": 2015, "2017-01-01": 2016, "2017-12-31": 2017}
            )


class FiscalAliasTests(unittest.TestCase):
    def setUp(self):
        self.cik = "0000315189"
        self.old_start, self.old_end = date(2014, 11, 1), date(2015, 10, 31)
        self.start, self.end = date(2014, 11, 3), date(2015, 11, 1)
        self.rules = {
            "periods": {
                period_key(self.cik, self.old_start, self.old_end): {
                    "fiscal_year": 2015
                },
                period_key(self.cik, self.start, self.end): {"fiscal_year": 2015},
            },
            "aliases": {
                period_key(self.cik, self.old_start, self.old_end): {
                    "period_start": str(self.start),
                    "period_end": str(self.end),
                    "note": "Reviewed 52-week dates.",
                },
            },
        }

    def test_only_an_exact_reviewed_issuer_and_period_alias_is_applied(self):
        """Never merge another issuer or a nearby period merely because its dates are similar."""
        original = reported_fact(
            source_cik=self.cik, period_start=self.old_start, period_end=self.old_end
        )
        self.assertEqual(canonical_period(original, self.rules), (self.start, self.end))
        for changed in (
            dict(original, source_cik="0000000001"),
            dict(original, period_start=date(2014, 11, 2)),
        ):
            with self.subTest(cik=changed["source_cik"], start=changed["period_start"]):
                self.assertEqual(
                    canonical_period(changed, self.rules),
                    (changed["period_start"], changed["period_end"]),
                )
        self.assertEqual(original["period_end"], self.old_end)

    def test_grouping_combines_reviewed_dates_and_preserves_source_facts(self):
        """Show one fiscal row, choose the latest version and retain its originally reported dates."""
        original = reported_fact(
            source_cik=self.cik,
            period_start=self.old_start,
            period_end=self.old_end,
            filed=date(2016, 2, 1),
            value=Decimal("100"),
        )
        corrected = reported_fact(
            source_cik=self.cik,
            period_start=self.start,
            period_end=self.end,
            filed=date(2017, 12, 1),
            accession="corrected",
            value=Decimal("95"),
        )
        balance = reported_fact(
            source_cik=self.cik,
            metric="equity",
            tag="StockholdersEquity",
            period_start=self.old_end,
            period_end=self.old_end,
            period_kind="instant",
            filed=date(2016, 2, 1),
            value=Decimal("50"),
        )
        facts = [original, corrected, balance]
        untouched = [dict(fact) for fact in facts]
        periods = annual_periods(facts, period_rules=self.rules)
        self.assertEqual(len(periods), 1)
        period = periods[0]
        self.assertEqual(
            (period.fiscal_year, period.period_start, period.period_end),
            (2015, self.start, self.end),
        )
        self.assertEqual(period.metrics["net_income"].value, Decimal("95"))
        self.assertEqual(period.metrics["equity"].variants[0].value, Decimal("50"))
        self.assertEqual(period.metrics["equity"].variants[0].period_end, self.old_end)
        self.assertIsNotNone(period.period_note)
        self.assertEqual(facts, untouched)

    def test_aliases_preserve_same_day_conflicts_and_do_not_merge_other_periods(self):
        """Date normalization does not authorize resolving conflicting reported values."""
        first = reported_fact(
            source_cik=self.cik,
            period_start=self.old_start,
            period_end=self.old_end,
            filed=date(2017, 12, 1),
            value=Decimal("100"),
        )
        second = dict(
            first,
            period_start=self.start,
            period_end=self.end,
            accession="other",
            value=Decimal("99"),
        )
        result = annual_periods([first, second], period_rules=self.rules)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metrics["net_income"].status, "needs_review")
        # No rule means that two different periods stay different, even for equal amounts.
        self.assertEqual(len(annual_periods([first, second], period_rules={})), 2)
        self.assertIsNone(annual_periods([first], period_rules={})[0].fiscal_year)


class FiscalPolicyPublicationTests(unittest.TestCase):
    def test_offline_policy_is_reproducible_and_corruption_preserves_the_previous_file(
        self,
    ):
        """Build from the official cover rather than fact fy; corrupt cache must not replace policy."""
        import json

        from app.data_pipeline.fiscal_periods import review_fiscal_periods
        from app.data_pipeline.source_files import sha256

        with TemporaryDirectory(prefix="beyondcandles-fiscal-policy-") as directory:
            root = Path(directory)
            cover_path = root / "cover.htm"
            cover_path.write_text(cover(), encoding="utf-8")
            index = root / "covers.json"
            index.write_text(
                json.dumps(
                    {
                        "0000200406:annual": {
                            "cover": {
                                "file": cover_path.name,
                                "sha256": sha256(cover_path),
                                "url": "https://www.sec.gov/cover",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            evidence = root / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            output = root / "policy.json"
            body = {
                "facts": {
                    "us-gaap": {
                        "NetIncomeLoss": {
                            "units": {
                                "USD": [
                                    {
                                        "start": "2014-12-29",
                                        "end": "2016-01-03",
                                        "form": "10-K",
                                        "filed": "2016-02-01",
                                        "accn": "annual",
                                        "fy": 2020,
                                    }
                                ]
                            }
                        }
                    }
                }
            }
            fact = reported_fact(
                source_cik="0000200406",
                period_start=date(2014, 12, 29),
                period_end=date(2016, 1, 3),
                history_eligible=True,
            )
            with (
                patch("app.data_pipeline.fiscal_periods.COVER_INDEX", index),
                patch("app.data_pipeline.fiscal_periods.EVIDENCE_INDEX", evidence),
                patch("app.data_pipeline.fiscal_periods.RULES_PATH", output),
                patch(
                    "app.data_pipeline.fiscal_periods.HISTORY_INDEX",
                    root / "absent.json",
                ),
                patch("app.data_pipeline.sec_catalogue.CACHE", root),
                patch(
                    "app.data_pipeline.fiscal_periods.identifiers",
                    return_value={"JNJ": {}},
                ),
                patch(
                    "app.data_pipeline.fiscal_periods.load_all_sec",
                    return_value=({}, [("JNJ", ({"cik": "0000200406"}, body))]),
                ),
                patch(
                    "app.data_pipeline.fiscal_periods.prepare_company_facts",
                    return_value=[fact],
                ),
            ):
                self.assertEqual(review_fiscal_periods()["status"], "reviewed")
                original = output.read_bytes()
                self.assertEqual(
                    next(iter(json.loads(original)["periods"].values()))["fiscal_year"],
                    2015,
                )
                review_fiscal_periods()
                self.assertEqual(output.read_bytes(), original)
                cover_path.write_text("corrupt", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    review_fiscal_periods()
                self.assertEqual(output.read_bytes(), original)
