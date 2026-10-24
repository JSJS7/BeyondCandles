"""Check parent-income definitions, source coherence and exceptional statement mappings."""

import hashlib
import json
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from test_stocks_api import reported_fact

from app.annual_financials import annual_periods
from app.data_pipeline.net_income import (
    issuer_income_files,
    mastercard_control,
    position,
    review_net_income,
    supporting_facts,
    verify_exception,
)
from app.net_income import calculated_value, select_net_income
from app.schemas.stock import FilingValue

START = date(2020, 1, 1)
END = date(2020, 12, 31)
KEY = "0000320193:2020-01-01:2020-12-31"
MINORITY_TAG = "NetIncomeLossAttributableToNoncontrollingInterest"
COMMON_TAG = "NetIncomeLossAvailableToCommonStockholdersBasic"


def value(**changes):
    return FilingValue.model_validate(reported_fact(**changes))


def exception_rule(total, **changes):
    rule = {
        "filed": "2021-02-01",
        "accession": "original",
        "components": [
            {"tag": "ProfitLoss", "coefficient": 1},
            {"tag": MINORITY_TAG, "coefficient": -1},
        ],
        "value": str(total),
        "calculation": "Group net income - allocations to other owners",
        "note": "Verified against the original income statement.",
    }
    rule.update(changes)
    return {KEY: rule}


def statement(rows):
    header = (
        '<table class="report"><tr><th>USD ($) in millions</th>'
        "<th>12 Months Ended</th></tr>"
        "<tr><th>Dec. 31, 2020</th></tr>"
    )
    body = []
    for tag, label, number in rows:
        body.append(
            f'<tr><td><a onclick="defref_us-gaap_{tag}">{label}</a></td>'
            f"<td>{number}</td></tr>"
        )
    return header + "".join(body) + "</table>"


def reference(path):
    return {
        "url": "https://www.sec.gov/Archives/" + path.name,
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class NetIncomeSelectionTests(unittest.TestCase):
    def select(self, variants, rules=None):
        return select_net_income(variants, START, END, rules or {})

    def test_parent_is_selected_without_discarding_group_profit(self):
        """Select parent income whether the group's income differs or happens to equal it."""
        for group_value in ("100", "130"):
            with self.subTest(group_value=group_value):
                variants = [
                    value(),
                    value(tag="ProfitLoss", value=Decimal(group_value)),
                ]
                result = self.select(variants)

                self.assertEqual(result.status, "ready")
                self.assertEqual(result.value, Decimal("100"))
                self.assertEqual(result.selected_tag, "NetIncomeLoss")
                self.assertEqual(len(result.variants), 2)

    def test_group_and_common_concepts_are_not_universal_fallbacks(self):
        """A sole alternative concept still needs a reviewed period mapping."""
        for tag in ("ProfitLoss", COMMON_TAG):
            with self.subTest(tag=tag):
                result = self.select([value(tag=tag)])
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)
        self.assertEqual(self.select([]).status, "not_available")

    def test_latest_conflicts_and_overlapping_issuers_remain_unresolved(self):
        """Do not choose an arbitrary parent amount or combine two different issuers."""
        alternatives = (
            value(accession="conflict", value=Decimal("99")),
            value(source_cik="0000000001", tag="ProfitLoss"),
        )
        for alternative in alternatives:
            with self.subTest(alternative=alternative):
                result = self.select([value(), alternative])
                self.assertEqual(result.status, "needs_review")

    def test_newer_alternative_prevents_stale_parent_fallback(self):
        """Reconcile a newer concept instead of hiding it behind an older parent value."""
        newer = value(tag="ProfitLoss", filed=date(2022, 2, 1), accession="newer")
        result = self.select([value(), newer])
        self.assertEqual(result.status, "needs_review")

    def test_signed_minority_allocation_is_subtracted_from_one_filing(self):
        """Subtract other owners' profit, add back their loss, and preserve an explicit zero."""
        for minority, expected in (("20", "80"), ("-20", "120"), ("0", "100")):
            with self.subTest(minority=minority):
                variants = [
                    value(tag="ProfitLoss"),
                    value(tag=MINORITY_TAG, value=Decimal(minority)),
                ]
                result = self.select(variants, exception_rule(expected))

                self.assertEqual(result.status, "ready")
                self.assertEqual(result.value, Decimal(expected))
                self.assertEqual(len(result.selected_sources), 2)
                self.assertIsNotNone(result.calculation)

    def test_missing_parts_mixed_filings_and_revisions_fail_closed(self):
        """Never replace a missing allocation with zero or reuse a stale calculation."""
        group = value(tag="ProfitLoss")
        minority = value(tag=MINORITY_TAG, value=Decimal("20"))
        cases = (
            [group],
            [group, minority.model_copy(update={"accession": "another"})],
            [group, minority.model_copy(update={"filed": date(2022, 2, 1)})],
            [group, minority.model_copy(update={"value": Decimal("21")})],
            [
                group,
                minority,
                minority.model_copy(
                    update={"accession": "conflict", "value": Decimal("99")}
                ),
            ],
        )
        for variants in cases:
            with self.subTest(variants=variants):
                result = self.select(variants, exception_rule("80"))
                self.assertEqual(result.status, "needs_review")

    def test_precise_arithmetic_and_original_dates_survive(self):
        """Retain all stored decimal digits and leave original source dictionaries untouched."""
        result = calculated_value(
            [
                (Decimal("1234567890123456789012345678.1234567890"), Decimal(1)),
                (Decimal("0.0000000001"), Decimal(-1)),
            ]
        )
        self.assertEqual(result, Decimal("1234567890123456789012345678.1234567889"))
        fact = reported_fact()
        original = dict(fact)
        annual_periods([fact])
        self.assertEqual(fact, original)


class NetIncomeEvidenceTests(unittest.TestCase):
    def test_allocation_is_verified_with_presentation_negation(self):
        """A rendered deduction may be negative; arithmetic retains the original GAAP sign."""
        html = statement(
            [
                ("ProfitLoss", "Net income", "100"),
                (
                    MINORITY_TAG,
                    "Net income attributable to noncontrolling interests",
                    "(20)",
                ),
            ]
        )
        variants = [
            value(tag="ProfitLoss", value=Decimal("100000000")),
            value(tag=MINORITY_TAG, value=Decimal("20000000")),
        ]
        selected, coefficients, _, _ = verify_exception(html, variants, END)
        result = calculated_value(
            [
                (fact.value, Decimal(coefficient))
                for fact, coefficient in zip(selected, coefficients)
            ]
        )
        self.assertEqual(coefficients, [1, -1])
        self.assertEqual(result, Decimal("80000000"))

    def test_subsidiary_preferred_dividends_belong_to_other_owners(self):
        """Deduct subsidiary preferred dividends while retaining the parent's own dividends."""
        html = statement(
            [
                ("ProfitLoss", "Net income", "100"),
                (MINORITY_TAG, "Noncontrolling income", "20"),
                (
                    "PreferredStockDividendsAndOtherAdjustments",
                    "Preferred dividends of subsidiary",
                    "1",
                ),
                (
                    "PreferredStockDividendsIncomeStatementImpact",
                    "Parent preferred dividends",
                    "5",
                ),
            ]
        )
        variants = [
            value(tag="ProfitLoss", value=Decimal("100000000")),
            value(tag=MINORITY_TAG, value=Decimal("20000000")),
            value(
                tag="PreferredStockDividendsAndOtherAdjustments",
                value=Decimal("1000000"),
            ),
            value(
                tag="PreferredStockDividendsIncomeStatementImpact",
                value=Decimal("5000000"),
            ),
        ]
        selected, coefficients, _, _ = verify_exception(html, variants, END)
        result = calculated_value(
            [
                (fact.value, Decimal(coefficient))
                for fact, coefficient in zip(selected, coefficients)
            ]
        )
        self.assertEqual(result, Decimal("79000000"))
        self.assertNotIn(
            "PreferredStockDividendsIncomeStatementImpact",
            [fact.tag for fact in selected],
        )

    def test_fcx_redemption_adjustment_is_not_an_earnings_component(self):
        """Keep parent net income before the separate redemption adjustment used for common EPS."""
        continuing = (
            "IncomeLossFromContinuingOperationsAttributableToNoncontrollingEntity"
        )
        discontinued = "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToNoncontrollingInterest"
        adjustment = "PreferredStockDividendsAndOtherAdjustments"
        rows = [
            ("ProfitLoss", "Net loss", "(4,025)"),
            (continuing, "Noncontrolling income: continuing operations", "(227)"),
            (discontinued, "Noncontrolling income: discontinued operations", "(63)"),
            (
                adjustment,
                "Gain on redemption and preferred dividends attributable to redeemable noncontrolling interest",
                "161",
            ),
        ]
        variants = [
            value(source_cik="0000831259", tag=tag, value=Decimal(amount) * 1000000)
            for tag, amount in (
                ("ProfitLoss", "-4025"),
                (continuing, "227"),
                (discontinued, "63"),
                (adjustment, "-161"),
            )
        ]
        selected, coefficients, _, note = verify_exception(
            statement(rows), variants, END
        )
        result = calculated_value(
            [
                (fact.value, Decimal(coefficient))
                for fact, coefficient in zip(selected, coefficients)
            ]
        )
        self.assertEqual(result, Decimal("-4315000000"))
        self.assertNotIn(adjustment, [fact.tag for fact in selected])
        self.assertIn("not the EPS numerator", note)

    def test_common_income_with_preferred_adjustment_is_not_parent_income(self):
        """Reject common income when a preferred allocation remains unresolved."""
        html = statement(
            [
                (
                    "PreferredStockDividendsAndOtherAdjustments",
                    "Preferred dividends",
                    "10",
                ),
                (COMMON_TAG, "Net income", "90"),
            ]
        )
        variants = [value(tag=COMMON_TAG, value=Decimal("90000000"))]
        with self.assertRaises(ValueError):
            verify_exception(html, variants, END)

    def test_continuing_only_allocation_and_wrong_amount_are_rejected(self):
        """Do not substitute a continuing-only allocation or accept a different issuer's amount."""
        cases = (
            (
                "IncomeLossFromContinuingOperationsAttributableToNoncontrollingEntity",
                "20",
            ),
            (MINORITY_TAG, "999"),
        )
        for tag, shown in cases:
            with self.subTest(tag=tag):
                html = statement(
                    [
                        ("ProfitLoss", "Net income", "100"),
                        (tag, "Noncontrolling interests", shown),
                    ]
                )
                variants = [
                    value(tag="ProfitLoss", value=Decimal("100000000")),
                    value(tag=tag, value=Decimal("20000000")),
                ]
                with self.assertRaises(ValueError):
                    verify_exception(html, variants, END)

    def test_supporting_import_preserves_existing_annual_periods(self):
        """Add an existing missing year, excluding quarters and unmatched date ranges."""
        base = [reported_fact(metric="revenue", tag="Revenues")]
        row = {
            "start": "2020-01-01",
            "end": "2020-12-31",
            "val": 100,
            "filed": "2021-02-01",
            "accn": "original",
            "form": "10-K",
            "fp": "FY",
        }
        response = {
            "cik": 320193,
            "facts": {
                "us-gaap": {
                    COMMON_TAG: {
                        "units": {
                            "USD": [
                                row,
                                dict(row, start="2020-01-02"),
                                dict(row, start="2020-10-01"),
                            ]
                        }
                    }
                }
            },
        }
        imported = supporting_facts(response, base)
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["metric"], "net_income")
        self.assertEqual(imported[0]["value"], Decimal(100))
        self.assertEqual(supporting_facts(response, [reported_fact()]), [])

    def test_joint_filing_uses_explicit_parent_statement_role(self):
        """Southern's subsidiary statement must not win even if it repeats parent amounts."""
        reports = []
        for name, filename in (
            ("Consolidated Statements of Income - Southern", "R3.htm"),
            ("Consolidated Statements of Income - SPC", "R35.htm"),
        ):
            reports.append(
                f"<Report><ShortName>{name}</ShortName><MenuCategory>Statements</MenuCategory>"
                f"<HtmlFileName>{filename}</HtmlFileName></Report>"
            )
        summary = ("<Reports>" + "".join(reports) + "</Reports>").encode()
        self.assertEqual(issuer_income_files(summary, "0000092122"), ["R3.htm"])

    def test_footnotes_and_unlabelled_columns_cannot_shift_the_annual_value(self):
        """Preserve colspan alignment and exclude quarterly and unknown-duration columns."""
        html = (
            '<table class="report"><tr><th>USD ($) in millions</th><th></th>'
            '<th>3 Months Ended</th><th colspan="2">12 Months Ended</th></tr>'
            "<tr><th>Dec. 18, 2020</th><th>Dec. 31, 2020</th>"
            "<th>Dec. 31, 2020</th><th>[1]</th></tr>"
            '<tr><td><a onclick="defref_us-gaap_ProfitLoss">Net income</a></td>'
            '<td></td><td>30</td><td colspan="2">100</td></tr></table>'
        )
        _, shown = position(html, "ProfitLoss", END, Decimal("100000000"))
        self.assertEqual(shown, Decimal("100000000"))
        with self.assertRaises(ValueError):
            position(html, "ProfitLoss", END, Decimal("30000000"))

    def test_mastercard_control_uses_the_dated_parent_column(self):
        """Separate other equity activity from net income already allocated to the parent."""
        html = (
            '<table class="report"><tr><th>USD ($) $ in Millions</th><th>Total</th>'
            "<th>Mastercard Incorporated Stockholders' Equity</th><th>Non- Controlling Interests</th></tr>"
            "<tr><td>Balance at Dec. 31, 2019</td><td>0</td><td>0</td><td>0</td></tr>"
            '<tr><td><a onclick="defref_us-gaap_ProfitLoss">Net Income</a></td>'
            "<td>100</td><td>100</td><td></td></tr></table>"
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "equity.htm"
            path.write_text(html, encoding="utf-8")
            entry = {"parent_income_equity": reference(path)}
            period = SimpleNamespace(period_end=END)
            variants = [value(tag="ProfitLoss", value=Decimal("100000000"))]
            with patch("app.data_pipeline.sec_catalogue.CACHE", path.parent):
                self.assertTrue(mastercard_control(entry, period, variants))
                period.period_end = date(2021, 12, 31)
                self.assertFalse(mastercard_control(entry, period, variants))

    def test_offline_review_is_repeatable_and_corruption_preserves_prior_policy(self):
        """Rebuild byte-identical rules from evidence; reject a changed source before publication."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "income.htm"
            report.write_text(
                statement([("ProfitLoss", "Net income", "100")]), encoding="utf-8"
            )
            summary = root / "summary.xml"
            summary.write_text(
                "<Reports><Report><ShortName>Consolidated Statements of Income</ShortName>"
                "<MenuCategory>Statements</MenuCategory><HtmlFileName>R1.htm</HtmlFileName>"
                "</Report></Reports>",
                encoding="utf-8",
            )
            evidence = root / "evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "0000320193:original": {
                            "summary": reference(summary),
                            "R1.htm": reference(report),
                        }
                    }
                ),
                encoding="utf-8",
            )
            selection = root / "selection.json"
            selection.write_text("[]", encoding="utf-8")
            facts = [
                reported_fact(
                    tag="ProfitLoss", value=Decimal("100000000"), history_eligible=True
                )
            ]
            company = ("AAPL", ({"cik": "0000320193"}, {}))
            output = root / "policy.json"
            with (
                patch("app.data_pipeline.net_income.RULES_PATH", output),
                patch("app.data_pipeline.net_income.SELECTION", selection),
                patch("app.data_pipeline.revenue.EVIDENCE_INDEX", evidence),
                patch("app.data_pipeline.sec_catalogue.CACHE", root),
                patch(
                    "app.data_pipeline.sec_catalogue.identifiers",
                    return_value={"AAPL": {}},
                ),
                patch(
                    "app.data_pipeline.sec_catalogue.load_all_sec",
                    side_effect=lambda: ({}, iter([company])),
                ),
                patch(
                    "app.data_pipeline.sec_history.prepare_company_facts",
                    return_value=facts,
                ),
            ):
                result = review_net_income()
                self.assertEqual(result["verified_exceptions"], 1)
                published = output.read_bytes()
                review_net_income()
                self.assertEqual(output.read_bytes(), published)
                report.write_text("changed", encoding="utf-8")
                with self.assertRaises(ValueError):
                    review_net_income()
                self.assertEqual(output.read_bytes(), published)
