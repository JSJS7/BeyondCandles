"""Exercise source-backed revenue selection and conservative face-statement recognition."""

import json
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from test_stocks_api import reported_fact

from app.data_pipeline.revenue import income_statement_files, review_jobs
from app.data_pipeline.sec_statements import matches_rendered_value, statement_total
from app.revenue import period_key, select_revenue
from app.schemas.stock import FilingValue


def revenue_value(tag="Revenues", **changes):
    fact = reported_fact(metric="revenue", tag=tag, **changes)
    return FilingValue.model_validate(fact)


def approved_rule(**changes):
    """Bind a fixture approval to the exact reported filing and amount."""
    rule = {
        "tag": "Revenues",
        "label": "Total revenues",
        "filed": "2021-02-01",
        "accession": "original",
        "value": "100",
    }
    rule.update(changes)
    return rule


def statement(rows, header="USD ($) shares in Thousands, $ in Millions"):
    return (
        '<table class="report"><tr><th>'
        + header
        + '</th><th colspan="2">12 Months Ended</th></tr>'
        "<tr><th>Dec. 31, 2020</th><th>Dec. 31, 2019</th></tr>" + rows + "</table>"
    )


def row(tag, label, values=("100", "90")):
    cells = "".join("<td>" + value + "</td>" for value in values)
    return (
        "<tr><td><a onclick=\"show('defref_"
        + tag
        + "')\">"
        + label
        + "</a></td>"
        + cells
        + "</tr>"
    )


class RevenueSelectionTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2020, 1, 1)
        self.end = date(2020, 12, 31)
        self.key = period_key("0000320193", self.start, self.end)
        self.rules = {self.key: approved_rule()}

    def test_verified_total_is_selected_without_summing_or_discarding_parts(self):
        """Select the reported total and retain the product component as an unselected source."""
        total = revenue_value(value=Decimal("100"))
        goods = revenue_value("SalesRevenueGoodsNet", value=Decimal("30"))
        result = select_revenue([total, goods], self.start, self.end, self.rules)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.value, Decimal("100"))
        self.assertEqual(result.selected_tag, "Revenues")
        self.assertEqual(len(result.variants), 2)

    def test_approval_does_not_follow_a_tag_into_an_unreviewed_filing(self):
        """A COP-like tag reuse must not turn a partial amount into approved revenue."""
        newer = revenue_value(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            value=Decimal("34590000000"),
            filed=date(2024, 2, 1),
            accession="newer",
        )
        rules = {
            self.key: {
                "tag": newer.tag,
                "label": "Sales and other operating revenues",
                "filed": "2022-02-01",
                "accession": "reviewed",
                "value": "45828000000",
            }
        }
        result = select_revenue([newer], self.start, self.end, rules)
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.value)

    def test_same_filing_with_changed_control_value_requires_review(self):
        """Even an identical accession cannot silently reuse approval for a changed amount."""
        fact = revenue_value(value=Decimal("80"))
        rules = {
            self.key: {
                "tag": fact.tag,
                "label": "Total revenues",
                "filed": str(fact.filed),
                "accession": fact.accession,
                "value": "100",
            }
        }
        result = select_revenue([fact], self.start, self.end, rules)
        self.assertEqual(result.status, "needs_review")

    def test_newer_partial_concept_invalidates_an_older_total_approval(self):
        """Do not hide a new revenue filing by continuing to display an older verified total."""
        older = revenue_value(value=Decimal("100"))
        newer = revenue_value(
            "SalesRevenueGoodsNet",
            value=Decimal("40"),
            filed=date(2024, 2, 1),
            accession="newer",
        )
        rules = {
            self.key: {
                "tag": older.tag,
                "label": "Total revenues",
                "filed": str(older.filed),
                "accession": older.accession,
                "value": "100",
            }
        }
        result = select_revenue([older, newer], self.start, self.end, rules)
        self.assertEqual(result.status, "needs_review")

    def test_unbound_legacy_approval_is_not_sufficient(self):
        """A historic issuer/period/tag mapping must be reviewed before it can approve a filing."""
        rules = {self.key: {"tag": "Revenues", "label": "Total revenues"}}
        result = select_revenue([revenue_value()], self.start, self.end, rules)
        self.assertEqual(result.status, "needs_review")

    def test_verified_new_total_preserves_the_old_concept_as_an_unselected_variant(
        self,
    ):
        """Select COP's verified 45,828 total after its old concept becomes a 34,590 component."""
        total = revenue_value(
            value=Decimal("45828000000"), accession="latest", filed=date(2024, 2, 1)
        )
        partial = revenue_value(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            value=Decimal("34590000000"),
            accession="latest",
            filed=total.filed,
        )
        rules = {
            self.key: approved_rule(
                value="45828000000", accession="latest", filed=str(total.filed)
            )
        }
        result = select_revenue([partial, total], self.start, self.end, rules)
        self.assertEqual(result.value, Decimal("45828000000"))
        self.assertEqual(result.selected_sources, [total])
        self.assertIn(partial, result.variants)

    def test_bank_component_changes_cannot_cancel_out_under_an_old_approval(self):
        """A constant sum does not approve changed component amounts in the same filing."""
        facts = [
            reported_fact(
                metric="revenue", tag="InterestIncomeExpenseNet", value=Decimal("90")
            ),
            reported_fact(
                metric="revenue", tag="NoninterestIncome", value=Decimal("110")
            ),
        ]
        rules = {
            self.key: approved_rule(
                components=["InterestIncomeExpenseNet", "NoninterestIncome"],
                value="200",
                component_values={
                    "InterestIncomeExpenseNet": "100",
                    "NoninterestIncome": "100",
                },
            )
        }
        result = select_revenue(
            [FilingValue.model_validate(fact) for fact in facts],
            self.start,
            self.end,
            rules,
            facts=facts,
        )
        self.assertEqual(result.status, "needs_review")

    def test_single_or_equal_unverified_candidates_still_require_review(self):
        """A single tag or equal amounts are not evidence that a concept is total revenue."""
        total = revenue_value()
        goods = revenue_value("SalesRevenueGoodsNet")
        for variants in ([total], [goods], [total, goods]):
            with self.subTest(tags=[variant.tag for variant in variants]):
                result = select_revenue(variants, self.start, self.end, {})
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)

    def test_goods_can_be_total_only_for_the_verified_issuer_and_period(self):
        """Allow a verified goods total without extending approval to other issuers or years."""
        rules = {
            self.key: approved_rule(
                tag="SalesRevenueGoodsNet", label="Net operating revenues"
            )
        }
        goods = revenue_value("SalesRevenueGoodsNet")
        self.assertEqual(
            select_revenue([goods], self.start, self.end, rules).status, "ready"
        )
        other = revenue_value("SalesRevenueGoodsNet", source_cik="0000000042")
        self.assertEqual(
            select_revenue([other], self.start, self.end, rules).status, "needs_review"
        )
        self.assertEqual(
            select_revenue([goods], self.start, date(2021, 12, 31), rules).status,
            "needs_review",
        )

    def test_missing_or_conflicting_verified_definition_never_falls_back_to_a_component(
        self,
    ):
        """Keep an absent total and same-day total conflicts unresolved instead of choosing goods."""
        goods = revenue_value("SalesRevenueGoodsNet")
        conflicting = [
            revenue_value(),
            revenue_value(value=Decimal("99"), accession="conflict"),
        ]
        for variants in (
            [goods],
            conflicting,
            [revenue_value(), revenue_value(source_cik="0000000042")],
        ):
            with self.subTest(count=len(variants)):
                result = select_revenue(variants, self.start, self.end, self.rules)
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)
        self.assertEqual(
            select_revenue([], self.start, self.end, {}).status, "not_available"
        )

    def test_custom_reported_total_keeps_its_original_taxonomy(self):
        """A reviewed custom issuer total is selectable without inventing a US-GAAP tag."""
        value = revenue_value("TotalRevenue", taxonomy="issuer")
        rule = {
            self.key: approved_rule(
                taxonomy="issuer", tag="TotalRevenue", label="Total revenue"
            )
        }
        result = select_revenue([value], self.start, self.end, rule)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.selected_sources[0].taxonomy, "issuer")

    def test_bank_formula_uses_complementary_components_not_taxable_equivalent_revenue(
        self,
    ):
        """Use GAAP 16,289 + 11,046 = 27,335 instead of the unrelated 27,455 total."""
        facts = [
            reported_fact(
                metric="revenue", tag="InterestIncomeExpenseNet", value=Decimal("16289")
            ),
            reported_fact(
                metric="revenue", tag="NoninterestIncome", value=Decimal("11046")
            ),
            reported_fact(metric="revenue", tag="Revenues", value=Decimal("27455")),
        ]
        variants = [FilingValue.model_validate(fact) for fact in facts]
        rules: dict[str, Any] = {
            self.key: approved_rule(
                components=["InterestIncomeExpenseNet", "NoninterestIncome"],
                value="27335",
                component_values={
                    "InterestIncomeExpenseNet": "16289",
                    "NoninterestIncome": "11046",
                },
            )
        }
        result = select_revenue(variants, self.start, self.end, rules, facts=facts)
        self.assertEqual(result.value, Decimal("27335"))
        self.assertIsNotNone(result.calculation)
        self.assertEqual(
            {source.accession for source in result.selected_sources}, {"original"}
        )
        # A loss is a real signed component, not a missing value to replace with zero.
        facts[1]["value"] = Decimal("-813")
        variants = [FilingValue.model_validate(fact) for fact in facts]
        rules[self.key]["component_values"]["NoninterestIncome"] = "-813"
        rules[self.key]["value"] = "15476"
        result = select_revenue(variants, self.start, self.end, rules, facts=facts)
        self.assertEqual(result.value, Decimal("15476"))

    def test_complementary_amounts_keep_database_decimal_precision(self):
        """Adding revenue components must not round digits beyond Decimal's default precision."""
        from app.revenue import sum_reported_values

        facts = [
            {"value": Decimal("9876543210987654321098765.1234567890")},
            {"value": Decimal("1000.0000000001")},
        ]
        self.assertEqual(
            sum_reported_values(facts), Decimal("9876543210987654321099765.1234567891")
        )

    def test_bank_formula_rejects_mixed_versions_missing_parts_and_same_day_conflicts(
        self,
    ):
        """Do not combine a corrected component with an older filing or hide conflicting facts."""
        interest = reported_fact(metric="revenue", tag="InterestIncomeExpenseNet")
        other = reported_fact(metric="revenue", tag="NoninterestIncome")
        correction = reported_fact(
            metric="revenue",
            tag="InterestIncomeExpenseNet",
            value=Decimal("150"),
            filed=date(2022, 2, 1),
            accession="correction",
            form="10-Q",
        )
        conflict = dict(interest, accession="conflict", value=Decimal("101"))
        variants = [FilingValue.model_validate(fact) for fact in [interest, other]]
        rules = {
            self.key: approved_rule(
                components=["InterestIncomeExpenseNet", "NoninterestIncome"],
                value="200",
                component_values={
                    "InterestIncomeExpenseNet": "100",
                    "NoninterestIncome": "100",
                },
            )
        }
        for facts in (
            [interest],
            [interest, other, correction],
            [interest, other, conflict],
        ):
            with self.subTest(accessions=[fact["accession"] for fact in facts]):
                result = select_revenue(
                    variants, self.start, self.end, rules, facts=facts
                )
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)


class RevenueStatementTests(unittest.TestCase):
    def test_footnote_colspans_do_not_shift_annual_values(self):
        """Expand a value spanning its footnote column before matching the older year."""
        html = (
            '<table class="report"><tr><th>USD ($) $ in Millions</th>'
            '<th colspan="3">12 Months Ended</th></tr>'
            "<tr><th>Dec. 31, 2020</th><th>Dec. 31, 2019</th><th>[1]</th></tr>"
            '<tr><td><a onclick="defref_us-gaap_Revenues">Revenue</a></td>'
            '<td>100</td><td colspan="2">90</td></tr></table>'
        )
        total = statement_total(html, date(2019, 12, 31))
        self.assertTrue(
            matches_rendered_value(total, Decimal("90000000"), date(2019, 12, 31))
        )
        self.assertFalse(
            matches_rendered_value(total, Decimal("100000000"), date(2019, 12, 31))
        )

    def test_unlabelled_duration_column_is_not_treated_as_an_annual_column(self):
        """Keep an undated-duration column aligned without silently classifying it as annual."""
        html = (
            '<table class="report"><tr><th>USD ($) $ in Millions</th><th></th>'
            "<th>3 Months Ended</th><th>12 Months Ended</th></tr>"
            "<tr><th>Dec. 31, 2020</th><th>Dec. 31, 2020</th><th>Dec. 31, 2020</th></tr>"
            + row("us-gaap_Revenues", "Revenues", ("999", "30", "100"))
            + "</table>"
        )
        total = statement_total(html, date(2020, 12, 31))
        self.assertTrue(
            matches_rendered_value(total, Decimal("100000000"), date(2020, 12, 31))
        )
        self.assertFalse(
            matches_rendered_value(total, Decimal("999000000"), date(2020, 12, 31))
        )

    def test_related_party_amount_note_does_not_hide_an_explicit_net_revenue_label(
        self,
    ):
        """Recognize PM's net-revenue label despite its parenthetical related-party disclosure."""
        html = statement(
            row(
                "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
                "Net revenues (includes $100 in 2020 and $90 in 2019 from related parties) (Note 18)",
            )
        )
        self.assertEqual(statement_total(html)["label"].split(" (")[0], "Net revenues")

    def test_face_total_is_recognized_and_later_segment_rows_are_ignored(self):
        """Use the face-statement total even when later product breakdowns repeat Net sales."""
        html = statement(
            row("us-gaap_SalesRevenueGoodsNet", "Product sales", ("30", "20"))
            + row("us-gaap_Revenues", "Total revenues")
            + row("us-gaap_GrossProfit", "Gross profit")
            + row("us-gaap_SalesRevenueGoodsNet", "Net sales", ("30", "20"))
        )
        total = statement_total(html)
        self.assertEqual(total["tag"], "Revenues")
        self.assertEqual(total["scale"], 1000000)
        self.assertTrue(
            matches_rendered_value(total, Decimal("100000000"), date(2020, 12, 31))
        )

    def test_amount_comparison_requires_the_correct_date_column_and_scale(self):
        """Do not match a prior year's amount or ignore the monetary scale beside share units."""
        total = statement_total(statement(row("us-gaap_Revenues", "Revenues")))
        self.assertFalse(
            matches_rendered_value(total, Decimal("90000000"), date(2020, 12, 31))
        )
        self.assertFalse(
            matches_rendered_value(total, Decimal("100"), date(2020, 12, 31))
        )
        self.assertFalse(
            matches_rendered_value(total, Decimal("100000000"), date(2021, 12, 31))
        )
        self.assertTrue(
            matches_rendered_value(total, Decimal("100100000"), date(2020, 12, 31))
        )
        precise = statement_total(
            statement(row("us-gaap_Revenues", "Revenues", ("100.0", "90.0")))
        )
        self.assertFalse(
            matches_rendered_value(precise, Decimal("100100000"), date(2020, 12, 31))
        )
        self.assertTrue(
            matches_rendered_value(precise, Decimal("100010000"), date(2020, 12, 31))
        )

    def test_explicit_total_takes_precedence_over_partial_contract_revenue(self):
        """Prefer the labeled total including leases over the separate contract-revenue line."""
        html = statement(
            row(
                "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                ("80", "70"),
            )
            + row("us-gaap_Revenues", "Total revenues", ("100", "90"))
        )
        self.assertEqual(statement_total(html)["tag"], "Revenues")

    def test_quarterly_column_cannot_validate_an_annual_amount_with_the_same_end_date(
        self,
    ):
        """Distinguish quarter and annual columns even when they share a report-end date."""
        html = (
            '<table class="report"><tr><th>USD ($) in Millions</th>'
            "<th>3 Months Ended</th><th>12 Months Ended</th></tr>"
            "<tr><th>Dec. 31, 2020</th><th>Dec. 31, 2020</th></tr>"
            + row("us-gaap_Revenues", "Revenues", ("30", "100"))
            + "</table>"
        )
        total = statement_total(html)
        self.assertTrue(
            matches_rendered_value(total, Decimal("100000000"), date(2020, 12, 31))
        )
        self.assertFalse(
            matches_rendered_value(total, Decimal("30000000"), date(2020, 12, 31))
        )

    def test_footnote_columns_preserve_date_alignment(self):
        """Expand date colspans so footnotes cannot shift amounts into a different year."""
        html = (
            '<table class="report"><tr><th>USD ($) in Millions</th>'
            '<th colspan="3">12 Months Ended</th></tr><tr>'
            '<th colspan="2">Dec. 31, 2020</th><th>Dec. 31, 2019</th></tr>'
            + row("us-gaap_Revenues", "Revenues", ("100", "[1]", "90"))
            + "</table>"
        )
        total = statement_total(html)
        self.assertTrue(
            matches_rendered_value(total, Decimal("90000000"), date(2019, 12, 31))
        )
        self.assertFalse(
            matches_rendered_value(total, Decimal("90000000"), date(2020, 12, 31))
        )

    def test_unknown_custom_ambiguous_or_non_usd_totals_are_not_approved(self):
        """Require a unique supported USD total; never guess from a label or the largest amount."""
        examples = [
            statement(row("custom_Revenue", "Total revenue")),
            statement(
                row("us-gaap_Revenues", "Revenues")
                + row("us-gaap_SalesRevenueNet", "Net sales")
            ),
            statement(row("us-gaap_SalesRevenueGoodsNet", "Product revenue")),
            statement(row("us-gaap_Revenues", "Revenues"), "EUR in Millions"),
        ]
        for html in examples:
            with self.subTest(html=html[:60]):
                with self.assertRaises(ValueError):
                    statement_total(html)

    def test_hidden_definition_tables_cannot_supply_a_total(self):
        """Read only the SEC report table, not hidden concept-definition tables below it."""
        html = statement(row("us-gaap_SalesRevenueGoodsNet", "Product revenue"))
        html += (
            '<table style="display:none">'
            + row("us-gaap_Revenues", "Total revenues")
            + "</table>"
        )
        with self.assertRaises(ValueError):
            statement_total(html)

    def test_income_statement_discovery_excludes_notes_and_parentheticals(self):
        """Restrict review to a primary operations statement and reject lookalike notes."""
        reports = []
        for name, category, filename in (
            ("Consolidated Statements of Operations", "Statements", "R2.htm"),
            ("Income Statement Parenthetical", "Statements", "R3.htm"),
            ("Revenue", "Notes", "R4.htm"),
            ("Statements of Comprehensive Income", "Statements", "R5.htm"),
        ):
            reports.append(
                f"<Report><ShortName>{name}</ShortName><MenuCategory>{category}</MenuCategory><HtmlFileName>{filename}</HtmlFileName></Report>"
            )
        self.assertEqual(
            income_statement_files(
                "<FilingSummary>" + "".join(reports) + "</FilingSummary>"
            ),
            ["R2.htm"],
        )

    def test_uncategorized_menu_entry_requires_explicit_statement_classification(self):
        """Handle SEC's Uncategorized menu label only when LongName identifies a Statement."""
        summary = (
            "<FilingSummary><Report><ShortName>Statements of Income</ShortName>"
            "<MenuCategory>Uncategorized</MenuCategory><LongName>1001003 - Statement - Income</LongName>"
            "<HtmlFileName>R3.htm</HtmlFileName></Report><Report><ShortName>Income</ShortName>"
            "<MenuCategory>Uncategorized</MenuCategory><LongName>2001000 - Disclosure - Income</LongName>"
            "<HtmlFileName>R4.htm</HtmlFileName></Report></FilingSummary>"
        )
        self.assertEqual(income_statement_files(summary), ["R3.htm"])

    def test_combined_comprehensive_statement_is_used_when_no_separate_income_statement_exists(
        self,
    ):
        """Permit a filer's combined income statement while the total-row parser still verifies it."""
        summary = (
            "<FilingSummary><Report><ShortName>Consolidated Statements of Comprehensive Loss</ShortName>"
            "<MenuCategory>Statements</MenuCategory><HtmlFileName>R4.htm</HtmlFileName>"
            "</Report></FilingSummary>"
        )
        self.assertEqual(income_statement_files(summary), ["R4.htm"])

    def test_review_jobs_preserve_issuer_and_accept_full_year_quarterly_revisions(self):
        """Keep issuer boundaries, allow annual 10-Q corrections and exclude quarterly-only rows."""
        facts = [
            reported_fact(metric="revenue", history_eligible=True),
            reported_fact(
                metric="revenue", history_eligible=True, source_cik="0000000042"
            ),
            reported_fact(
                metric="revenue", history_eligible=False, source_cik="0000000043"
            ),
            reported_fact(
                metric="revenue",
                history_eligible=True,
                form="10-Q",
                accession="quarter",
                filed=date(2022, 1, 1),
            ),
            reported_fact(
                metric="revenue",
                history_eligible=True,
                form="10-Q",
                accession="quarter-only",
                source_cik="0000000044",
            ),
        ]
        jobs = review_jobs(facts)
        self.assertEqual(
            {source[0] for source, _, _ in jobs}, {"0000320193", "0000000042"}
        )
        self.assertIn(("0000320193", "quarter"), {source for source, _, _ in jobs})

    def test_review_keeps_an_annual_period_without_any_revenue_candidate(self):
        """A missing revenue metric must produce an unresolved review, not disappear from coverage."""
        fact = reported_fact(history_eligible=True)
        jobs = review_jobs([fact])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            jobs[0][2],
            {period_key(fact["source_cik"], fact["period_start"], fact["period_end"])},
        )

    def test_note_letters_and_share_only_scale_do_not_change_currency_amounts(self):
        """Revenue (Note D) is a revenue label; shares in thousands do not scale dollars."""
        total = statement_total(
            statement(
                row("us-gaap_Revenues", "Revenue (Note D)"),
                "USD ($) shares in Thousands",
            )
        )
        self.assertEqual(total["scale"], 1)
        self.assertTrue(
            matches_rendered_value(total, Decimal("100"), date(2020, 12, 31))
        )

    def test_tax_definition_is_chosen_for_its_own_nonempty_annual_column(self):
        """A tax-tag transition has separate definitions in different years, not two totals."""
        html = statement(
            row(
                "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
                "Sales",
                ("100", ""),
            )
            + row(
                "us-gaap_RevenueFromContractWithCustomerIncludingAssessedTax",
                "Sales",
                ("", "90"),
            )
        )
        self.assertEqual(
            statement_total(html, date(2020, 12, 31))["tag"],
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        )
        self.assertEqual(
            statement_total(html, date(2019, 12, 31))["tag"],
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        )

    def test_total_under_revenue_heading_wins_over_product_sales(self):
        """A plain Total belongs to revenue only within the verified revenue section."""
        html = statement(
            row("us-gaap_RevenuesAbstract", "Revenues", ("", ""))
            + row("us-gaap_SalesRevenueGoodsNet", "Net sales", ("80", "70"))
            + row("us-gaap_Revenues", "Total")
        )
        self.assertEqual(statement_total(html)["tag"], "Revenues")


class RevenueEvidenceTests(unittest.TestCase):
    def test_joint_filing_repeated_parent_total_does_not_select_a_subsidiary_by_position(
        self,
    ):
        """Accept repeated identical issuer totals; reject two different matching definitions."""
        from app.data_pipeline.revenue import verify_filing_period
        from app.data_pipeline.source_files import sha256

        with TemporaryDirectory(prefix="beyondcandles-joint-revenue-") as directory:
            root = Path(directory)

            def reference(name, text):
                path = root / name
                path.write_text(text, encoding="utf-8")
                return {
                    "file": name,
                    "sha256": sha256(path),
                    "url": "https://www.sec.gov/" + name,
                }

            summary = (
                "<FilingSummary>"
                + "".join(
                    f"<Report><ShortName>Statements of Income</ShortName><MenuCategory>Statements</MenuCategory><HtmlFileName>{name}</HtmlFileName></Report>"
                    for name in ("R2.htm", "R3.htm")
                )
                + "</FilingSummary>"
            )
            old = {
                "summary": reference("summary.xml", summary),
                "R2.htm": reference(
                    "R2.htm", statement(row("us-gaap_Revenues", "Total revenues"))
                ),
                "R3.htm": reference(
                    "R3.htm", statement(row("us-gaap_Revenues", "Total revenues"))
                ),
            }
            fact = reported_fact(
                metric="revenue", tag="Revenues", value=Decimal("100000000")
            )
            period = period_key(
                fact["source_cik"], fact["period_start"], fact["period_end"]
            )
            source = (fact["source_cik"], fact["accession"])
            with patch("app.data_pipeline.sec_catalogue.CACHE", root):
                rule, _ = verify_filing_period(
                    "FIXTURE", period, [fact], source, old, {"filings": {}}, False, {}
                )
                self.assertEqual(rule["tag"], "Revenues")
                # A different supported total also matches this issuer: still an ambiguity.
                old["R3.htm"] = reference(
                    "R3.htm", statement(row("us-gaap_SalesRevenueNet", "Net sales"))
                )
                other = dict(fact, tag="SalesRevenueNet")
                rule, _ = verify_filing_period(
                    "FIXTURE",
                    period,
                    [fact, other],
                    source,
                    old,
                    {"filings": {}},
                    False,
                    {},
                )
                self.assertIsNone(rule)

    def test_nasdaq_gross_profit_tag_requires_its_exact_net_revenue_label(self):
        """A Nasdaq-specific net-revenue mapping must never approve an ordinary gross profit."""
        from app.data_pipeline.revenue import verify_filing_period
        from app.data_pipeline.source_files import sha256

        with TemporaryDirectory(prefix="beyondcandles-nasdaq-revenue-") as directory:
            root = Path(directory)
            summary = root / "summary.xml"
            summary.write_text(
                "<FilingSummary><Report><ShortName>Statements of Income</ShortName>"
                "<MenuCategory>Statements</MenuCategory><HtmlFileName>R2.htm</HtmlFileName>"
                "</Report></FilingSummary>",
                encoding="utf-8",
            )
            income = root / "income.htm"
            fact = reported_fact(
                metric="revenue",
                tag="GrossProfit",
                value=Decimal("100000000"),
                source_cik="0001120193",
            )
            source = (fact["source_cik"], fact["accession"])
            period = period_key(
                fact["source_cik"], fact["period_start"], fact["period_end"]
            )
            old = {
                "summary": {
                    "file": summary.name,
                    "sha256": sha256(summary),
                    "url": "https://www.sec.gov/summary",
                }
            }
            for label, expected in (
                ("Gross profit", False),
                ("Revenues less transaction-based expenses", True),
            ):
                with (
                    self.subTest(label=label),
                    patch("app.data_pipeline.sec_catalogue.CACHE", root),
                ):
                    income.write_text(
                        statement(row("us-gaap_GrossProfit", label)), encoding="utf-8"
                    )
                    old["R2.htm"] = {
                        "file": income.name,
                        "sha256": sha256(income),
                        "url": "https://www.sec.gov/income",
                    }
                    rule, _ = verify_filing_period(
                        "NDAQ", period, [fact], source, old, {"filings": {}}, False, {}
                    )
                    self.assertEqual(rule is not None, expected)

    def test_pinned_note_uses_the_consolidated_row_before_any_segment_axis(self):
        """Approve a same-filing note total, preserving boundaries against later segment rows."""
        from app.data_pipeline.revenue import reviewed_disclosure
        from app.data_pipeline.source_files import sha256

        with TemporaryDirectory(prefix="beyondcandles-revenue-note-") as directory:
            root = Path(directory)
            note = root / "note.htm"
            note.write_text(
                statement(
                    row("us-gaap_Revenues", "Total revenues")
                    + row(
                        "us-gaap_StatementBusinessSegmentsAxis", "Segment A", ("", "")
                    )
                    + row("us-gaap_Revenues", "Total revenues", ("40", "30"))
                ),
                encoding="utf-8",
            )
            fact = reported_fact(
                metric="revenue", tag="Revenues", value=Decimal("100000000")
            )
            source = (fact["source_cik"], fact["accession"])
            period = period_key(
                fact["source_cik"], fact["period_start"], fact["period_end"]
            )
            reference = {
                "file": note.name,
                "sha256": sha256(note),
                "url": "https://www.sec.gov/note",
            }
            pinned = {
                period: {
                    "accession": "original",
                    "filename": "R9.htm",
                    "tag": "Revenues",
                    "label": "Total revenues",
                    "value": "100000000",
                }
            }
            with (
                patch("app.data_pipeline.sec_catalogue.CACHE", root),
                patch("app.data_pipeline.revenue.REVIEWED_NOTE_TOTALS", pinned),
            ):
                rule = reviewed_disclosure(
                    period, [fact], source, {"R9.htm": reference, "summary": reference}
                )
                assert rule is not None
                self.assertEqual(rule["value"], "100000000")
                self.assertIsNone(
                    reviewed_disclosure(period, [fact], (source[0], "unreviewed"), {})
                )

    def test_offline_review_verifies_sources_and_does_not_publish_after_corruption(
        self,
    ):
        """Reproduce an approval offline; corrupt evidence must leave the published policy intact."""
        from app.data_pipeline.revenue import review_revenue
        from app.data_pipeline.source_files import sha256

        with TemporaryDirectory(prefix="beyondcandles-revenue-test-") as directory:
            root = Path(directory)
            summary = root / "summary.xml"
            summary.write_text(
                "<FilingSummary><Report><ShortName>Statements of Income</ShortName>"
                "<MenuCategory>Statements</MenuCategory><HtmlFileName>R2.htm</HtmlFileName>"
                "</Report></FilingSummary>",
                encoding="utf-8",
            )
            income = root / "income.htm"
            income.write_text(
                statement(row("us-gaap_Revenues", "Total revenues")), encoding="utf-8"
            )
            evidence = {
                "0000320193:original": {
                    "summary": {
                        "file": summary.name,
                        "sha256": sha256(summary),
                        "url": "https://www.sec.gov/summary",
                    },
                    "R2.htm": {
                        "file": income.name,
                        "sha256": sha256(income),
                        "url": "https://www.sec.gov/statement",
                    },
                },
            }
            index = root / "index.json"
            index.write_text(json.dumps(evidence), encoding="utf-8")
            latest_income = root / "latest.htm"
            latest_income.write_text(
                statement(row("us-gaap_Revenues", "Total revenues"))
                .replace("2020", "2023")
                .replace("2019", "2022"),
                encoding="utf-8",
            )
            evidence["0000320193:later"] = {
                "summary": evidence["0000320193:original"]["summary"],
                "R2.htm": {
                    "file": latest_income.name,
                    "sha256": sha256(latest_income),
                    "url": "https://www.sec.gov/later",
                },
            }
            index.write_text(json.dumps(evidence), encoding="utf-8")
            policy_path = root / "policy.json"
            fact = reported_fact(
                metric="revenue",
                tag="Revenues",
                value=Decimal("100000000"),
                history_eligible=True,
            )
            with (
                patch("app.data_pipeline.revenue.ROOT", root),
                patch(
                    "app.data_pipeline.revenue.SUPPLEMENT_INDEX",
                    root / "supplements.json",
                ),
                patch("app.data_pipeline.revenue.EVIDENCE_INDEX", index),
                patch("app.data_pipeline.revenue.RULES_PATH", policy_path),
                patch(
                    "app.data_pipeline.revenue.HISTORY_INDEX",
                    root / "missing-history.json",
                ),
                patch("app.data_pipeline.sec_catalogue.CACHE", root),
                patch(
                    "app.data_pipeline.revenue.identifiers", return_value={"AAPL": {}}
                ),
                patch(
                    "app.data_pipeline.revenue.load_all_sec",
                    return_value=({}, [("AAPL", ({}, {}))]),
                ),
                patch(
                    "app.data_pipeline.revenue.prepare_company_facts",
                    return_value=[
                        fact,
                        dict(fact, accession="later", filed=date(2023, 2, 1)),
                    ],
                ),
            ):
                report = review_revenue()
                # The older statement matches, but the latest one lacks the requested year.
                self.assertEqual(report["totals"]["approved_periods"], 0)
                self.assertEqual(report["totals"]["issues"], 1)
                self.assertEqual(
                    json.loads(policy_path.read_text(encoding="utf-8"))["periods"], {}
                )

                # After verifying the latest statement itself, approve its exact version.
                latest_income.write_text(
                    statement(row("us-gaap_Revenues", "Total revenues")),
                    encoding="utf-8",
                )
                evidence["0000320193:later"]["R2.htm"]["sha256"] = sha256(latest_income)
                index.write_text(json.dumps(evidence), encoding="utf-8")
                report = review_revenue()
                self.assertEqual(report["totals"]["approved_periods"], 1)
                published = json.loads(policy_path.read_text(encoding="utf-8"))
                rule = next(iter(published["periods"].values()))
                self.assertEqual(rule["accession"], "later")
                self.assertEqual(rule["filed"], "2023-02-01")
                original_policy = policy_path.read_bytes()
                latest_income.write_text("corrupt", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    review_revenue()
                self.assertEqual(policy_path.read_bytes(), original_policy)


class RevenueCommandTests(unittest.TestCase):
    def test_unresolved_review_returns_a_failure_exit_code(self):
        """Automation must distinguish a complete approval from an unresolved latest filing."""
        from contextlib import redirect_stdout
        from io import StringIO

        from app.data_pipeline.__main__ import main

        for status, issues, expected_exit in (
            ("reviewed", 0, 0),
            ("completed_with_gaps", 1, 1),
        ):
            report = {
                "status": status,
                "companies": {},
                "totals": {"approved_periods": 0, "issues": issues},
            }
            with (
                self.subTest(status=status),
                patch("sys.argv", ["data_pipeline", "review-revenue"]),
                patch("app.data_pipeline.revenue.review_revenue", return_value=report),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(main(), expected_exit)


class OriginalRevenueFactTests(unittest.TestCase):
    def instance(self, extra_context="", extra_facts=""):
        return (
            '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:bk="http://issuer/2020" '
            'xmlns:iso4217="http://www.xbrl.org/2003/iso4217">'
            '<xbrli:context id="total"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">'
            "1390777</xbrli:identifier></xbrli:entity><xbrli:period>"
            "<xbrli:startDate>2020-01-01</xbrli:startDate><xbrli:endDate>2020-12-31</xbrli:endDate>"
            "</xbrli:period></xbrli:context>"
            + extra_context
            + '<xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
            "<bk:TotalRevenuesIncludingRevenueGeneratedByVariableInterestEntities "
            'contextRef="total" unitRef="USD" decimals="-6">18619000000</bk:TotalRevenuesIncludingRevenueGeneratedByVariableInterestEntities>'
            + extra_facts
            + "</xbrli:xbrl>"
        ).encode()

    def test_custom_exact_fact_requires_the_issuer_and_consolidated_context(self):
        """Read exact full-dollar custom facts while excluding another issuer and segment data."""
        from app.data_pipeline.revenue_facts import instance_facts

        metadata = {
            "filed": date(2021, 2, 1),
            "accession": "original",
            "form": "10-K",
            "fiscal_period": "FY",
        }
        concepts = {
            ("bk", "TotalRevenuesIncludingRevenueGeneratedByVariableInterestEntities")
        }
        facts = instance_facts(self.instance(), "0001390777", metadata, concepts)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["value"], Decimal("18619000000"))
        self.assertEqual(facts[0]["taxonomy"], "bk")
        self.assertEqual(
            instance_facts(self.instance(), "0000000001", metadata, concepts), []
        )
        segment = (
            '<xbrli:context id="segment"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">'
            '1390777</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="bk:Axis">'
            "bk:Member</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period>"
            "<xbrli:startDate>2020-01-01</xbrli:startDate><xbrli:endDate>2020-12-31</xbrli:endDate>"
            "</xbrli:period></xbrli:context>"
        )
        fact = '<bk:TotalRevenuesIncludingRevenueGeneratedByVariableInterestEntities contextRef="segment" unitRef="USD">999</bk:TotalRevenuesIncludingRevenueGeneratedByVariableInterestEntities>'
        self.assertEqual(
            len(
                instance_facts(
                    self.instance(segment, fact), "0001390777", metadata, concepts
                )
            ),
            1,
        )
        conflicting = fact.replace('contextRef="segment"', 'contextRef="total"')
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            instance_facts(
                self.instance("", conflicting), "0001390777", metadata, concepts
            )
