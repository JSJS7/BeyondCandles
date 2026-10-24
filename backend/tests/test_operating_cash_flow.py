"""Check annual operating cash flow, scope, report versions and exact source approvals."""

import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from test_stocks_api import reported_fact

from app.data_pipeline.operating_cash_flow import (
    cash_flow_files,
    cash_positions,
    choose_positions,
    resolve_reviewed_period,
    reviewed_exceptions,
)
from app.equity import fact_identity, variants_fingerprint
from app.operating_cash_flow import (
    CONTINUING_TAG,
    DISCONTINUED_CASH_TAG,
    DISCONTINUED_TAG,
    TOTAL_TAG,
    select_cash_flow,
)
from app.schemas.stock import FilingValue

START = date(2020, 1, 1)
END = date(2020, 12, 31)
KEY = "0000320193:2020-01-01:2020-12-31"


def cash_fact(**changes):
    fields = {"metric": "operating_cash_flow", "tag": TOTAL_TAG}
    fields.update(changes)
    return reported_fact(**fields)


def approval(variants, selected, value, **changes):
    rule = {
        "observed_sha256": variants_fingerprint(variants),
        "components": [
            {"fact": fact_identity(fact), "coefficient": 1} for fact in selected
        ],
        "value": value,
        "note": "Verified against the exact cash-flow statement.",
    }
    rule.update(changes)
    return {KEY: rule}


class CashFlowSelectionTests(unittest.TestCase):
    def select(self, facts, variants=None, rules=None):
        if variants is None:
            variants = facts
        return select_cash_flow(
            [FilingValue.model_validate(fact) for fact in variants],
            START,
            END,
            facts=facts,
            calendar={},
            rules=rules or {},
        )

    def test_single_unreviewed_total_or_continuing_tag_needs_review(self):
        """One candidate alone does not verify its economic scope."""
        for tag in (TOTAL_TAG, CONTINUING_TAG):
            with self.subTest(tag=tag):
                result = self.select([cash_fact(tag=tag)])
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)

    def test_total_preserves_the_distinct_continuing_definition(self):
        """A reviewed total 100 includes discontinued activity; continuing 90 remains a separate source."""
        total = cash_fact()
        continuing = cash_fact(tag=CONTINUING_TAG, value=Decimal(90))
        rules = approval([total, continuing], [total], "100")
        result = self.select([total, continuing], rules=rules)
        self.assertEqual(result.value, Decimal(100))
        self.assertEqual(result.selected_tag, TOTAL_TAG)
        self.assertEqual(result.scope, "total")
        self.assertEqual(len(result.variants), 2)
        self.assertEqual(len(result.selected_sources), 1)

    def test_older_total_cannot_override_newer_continuing_restatement(self):
        """A verified old 100 cannot hide a later continuing-only restatement to 70."""
        total = cash_fact()
        continuing = cash_fact(
            tag=CONTINUING_TAG,
            filed=date(2022, 1, 1),
            accession="revision",
            value=Decimal(70),
        )
        rules = approval([total, continuing], [total], "100")
        result = self.select([total, continuing], rules=rules)
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.value)

    def test_changed_snapshot_or_same_day_conflict_invalidates_approval(self):
        """An approval cannot silently survive a new value, version or contradictory same-day report."""
        total = cash_fact()
        rules = approval([total], [total], "100")
        revision = cash_fact(filed=date(2022, 1, 1), accession="new", value=Decimal(99))
        self.assertEqual(
            self.select([total, revision], rules=rules).status, "needs_review"
        )
        conflict = cash_fact(accession="conflict", value=Decimal(99))
        rules = approval([total, conflict], [total], "100")
        self.assertEqual(
            self.select([total, conflict], rules=rules).status, "needs_review"
        )

    def test_zero_and_negative_reported_cash_flows_are_valid(self):
        """An operating outflow or explicit zero is a value rather than missing data."""
        for amount in ("0", "-524499000"):
            with self.subTest(amount=amount):
                total = cash_fact(value=Decimal(amount))
                result = self.select([total], rules=approval([total], [total], amount))
                self.assertEqual(result.status, "ready")
                self.assertEqual(result.value, Decimal(amount))

    def test_signed_discontinued_component_is_added_in_one_filing(self):
        """Continuing inflow 100 plus discontinued outflow -20 yields whole-company 80."""
        continuing = cash_fact(tag=CONTINUING_TAG)
        discontinued = cash_fact(tag=DISCONTINUED_TAG, value=Decimal(-20))
        rules = approval(
            [continuing],
            [continuing, discontinued],
            "80",
            calculation="Continuing + discontinued",
        )
        result = self.select([continuing, discontinued], [continuing], rules)
        self.assertEqual(result.value, Decimal(80))
        self.assertEqual(len(result.selected_sources), 2)
        self.assertEqual(len(result.variants), 2)
        wrong = dict(discontinued, accession="other-filing")
        rules = approval([continuing], [continuing, wrong], "80")
        self.assertEqual(
            self.select([continuing, wrong], [continuing], rules).status, "needs_review"
        )

    def test_total_and_components_are_never_added_together(self):
        """Adding total 100 to continuing 90 would double-count overlapping operations."""
        total = cash_fact()
        continuing = cash_fact(tag=CONTINUING_TAG, value=Decimal(90))
        rules = approval([total, continuing], [total, continuing], "190")
        self.assertEqual(
            self.select([total, continuing], rules=rules).status, "needs_review"
        )

    def test_missing_wrong_currency_history_or_quarter_is_rejected(self):
        """A missing component, foreign currency, excluded issuer history or YTD value cannot be approved."""
        total = cash_fact()
        for changes in (
            {"unit": "EUR"},
            {"history_eligible": False},
            {"period_kind": "quarter"},
        ):
            with self.subTest(changes=changes):
                candidate = dict(total, **changes)
                result = self.select(
                    [candidate], rules=approval([candidate], [candidate], "100")
                )
                self.assertEqual(result.status, "needs_review")
        result = self.select(
            [total], rules=approval([total], [cash_fact(value=Decimal(99))], "99")
        )
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(self.select([]).status, "not_available")

    def test_statement_only_component_requires_its_exact_filing_anchor(self):
        """A missing Companyfacts component may come from the same approved report, never another filing."""
        continuing = cash_fact(tag=CONTINUING_TAG)
        position = cash_fact(
            tag=DISCONTINUED_TAG,
            value=Decimal(-20),
            statement_url="https://www.sec.gov/report/R5.htm",
            statement_label="Operating cash flow from discontinued operations",
            statement_column="Annual period ending 2020-12-31",
        )
        rules = approval(
            [continuing],
            [continuing, position],
            "80",
            anchor=fact_identity(continuing),
            statement={"url": position["statement_url"]},
        )
        rule = rules[KEY]
        rule["components"][1]["statement_position"] = True
        result = self.select([continuing], rules=rules)
        self.assertEqual(result.value, Decimal(80))
        self.assertEqual(
            result.selected_sources[1].statement_label, position["statement_label"]
        )
        rule["components"][1]["fact"]["accession"] = "another-filing"
        self.assertEqual(self.select([continuing], rules=rules).status, "needs_review")

    def test_reviewed_gap_explains_missing_scope_without_inventing_zero(self):
        """A documented missing component stays under review, with the reviewed reason exposed."""
        continuing = cash_fact(tag=CONTINUING_TAG)
        rules = {
            KEY: {
                "observed_sha256": variants_fingerprint([continuing]),
                "note": "Discontinued operating cash flows were not disclosed separately.",
            }
        }
        result = self.select([continuing], rules=rules)
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.value)
        self.assertIn("not disclosed", result.selection_note)

    def test_unlabelled_aggregate_discontinued_fact_is_never_an_operating_component(
        self,
    ):
        """Even a hand-written approval cannot substitute aggregate discontinued cash for OCF."""
        continuing = cash_fact(tag=CONTINUING_TAG)
        aggregate = cash_fact(tag=DISCONTINUED_CASH_TAG, value=Decimal(-20))
        rules = approval([continuing], [continuing, aggregate], "80")
        self.assertEqual(
            self.select([continuing, aggregate], [continuing], rules).status,
            "needs_review",
        )

    def test_total_concept_can_be_added_only_when_reviewed_as_continuing(self):
        """A mislabeled continuing subtotal may add discontinued OCF; a genuine whole total may not."""
        total = cash_fact()
        discontinued = cash_fact(tag=DISCONTINUED_TAG, value=Decimal(-20))
        rules = approval([total], [total, discontinued], "80")
        self.assertEqual(
            self.select([total, discontinued], [total], rules).status, "needs_review"
        )
        rules[KEY]["components"][0]["scope"] = "continuing"
        self.assertEqual(
            self.select([total, discontinued], [total], rules).value, Decimal(80)
        )

    def test_older_explicit_zero_requires_an_identical_current_continuing_amount(self):
        """Retain an older complete report only with its explicit operating zero and unchanged latest subtotal."""
        older = cash_fact(tag=CONTINUING_TAG)
        zero = cash_fact(tag=DISCONTINUED_TAG, value=Decimal(0))
        current = cash_fact(
            tag=CONTINUING_TAG, filed=date(2022, 2, 1), accession="latest"
        )
        rules = approval(
            [current], [older, zero], "100", current_continuing=fact_identity(current)
        )
        result = self.select([older, zero, current], [current], rules)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.selected_sources[0].filed, older["filed"])
        self.assertEqual(result.selected_sources[1].accession, older["accession"])
        self.assertIn(FilingValue.model_validate(current), result.variants)

        changed = dict(current, value=Decimal(99))
        rules[KEY]["observed_sha256"] = variants_fingerprint([changed])
        rules[KEY]["current_continuing"] = fact_identity(changed)
        self.assertEqual(
            self.select([older, zero, changed], [changed], rules).status, "needs_review"
        )

    def test_older_nonzero_component_and_current_conflict_cannot_use_zero_exception(
        self,
    ):
        """A nonzero discontinued amount or a conflicting latest report must not bypass version checks."""
        older = cash_fact(tag=CONTINUING_TAG)
        current = cash_fact(
            tag=CONTINUING_TAG, filed=date(2022, 2, 1), accession="latest"
        )
        component = cash_fact(tag=DISCONTINUED_TAG, value=Decimal(-20))
        rules = approval(
            [current],
            [older, component],
            "80",
            current_continuing=fact_identity(current),
        )
        self.assertEqual(
            self.select([older, component, current], [current], rules).status,
            "needs_review",
        )

        zero = dict(component, value=Decimal(0))
        conflict = dict(current, value=Decimal(99), accession="conflict")
        rules = approval(
            [current, conflict],
            [older, zero],
            "100",
            current_continuing=fact_identity(current),
        )
        self.assertEqual(
            self.select(
                [older, zero, current, conflict], [current, conflict], rules
            ).status,
            "needs_review",
        )

    def test_reviewed_unavailable_is_distinct_from_unknown_or_changed_data(self):
        """A pinned disclosure gap returns no value; a refreshed snapshot requires a new review."""
        current = cash_fact(tag=CONTINUING_TAG)
        rules = {
            KEY: {
                "status": "not_available",
                "observed_sha256": variants_fingerprint([current]),
                "note": "The operating component is not separately disclosed.",
            }
        }
        result = self.select([current], rules=rules)
        self.assertEqual(result.status, "not_available")
        self.assertIsNone(result.value)
        self.assertEqual(result.variants[0].value, Decimal(100))
        self.assertIn("not separately disclosed", result.selection_note)
        self.assertEqual(
            self.select([dict(current, value=Decimal(99))], rules=rules).status,
            "needs_review",
        )

    def test_reviewed_continuing_value_keeps_scope_and_original_total_or_continuing_tag(
        self,
    ):
        """Citi/GD's reported subtotal is ready only with an explicit continuing scope, even with a total tag."""
        for tag in (TOTAL_TAG, CONTINUING_TAG):
            with self.subTest(tag=tag):
                current = cash_fact(tag=tag, value=Decimal(-120))
                rules = approval([current], [current], "-120", scope="continuing")
                rules[KEY]["components"][0]["scope"] = "continuing"
                result = self.select([current], rules=rules)
                self.assertEqual(result.status, "ready")
                self.assertEqual(result.value, Decimal(-120))
                self.assertEqual(result.scope, "continuing")
                self.assertEqual(result.selected_tag, tag)
                self.assertEqual(
                    result.selected_sources[0].accession, current["accession"]
                )
                self.assertIn("excluding discontinued", result.selection_note)
                self.assertNotIn("including discontinued", result.selection_note)

    def test_continuing_scope_requires_one_reviewed_subtotal_and_unchanged_snapshot(
        self,
    ):
        """Never label a sum as continuing or accept an unreviewed scope or changed source."""
        current = cash_fact(tag=CONTINUING_TAG)
        rules = approval([current], [current], "100", scope="continuing")
        self.assertEqual(self.select([current], rules=rules).status, "needs_review")
        rules[KEY]["components"][0]["scope"] = "continuing"
        self.assertEqual(
            self.select([dict(current, value=Decimal(99))], rules=rules).status,
            "needs_review",
        )
        zero = cash_fact(tag=DISCONTINUED_TAG, value=Decimal(0))
        rules = approval([current], [current, zero], "100", scope="continuing")
        rules[KEY]["components"][0]["scope"] = "continuing"
        self.assertEqual(
            self.select([current, zero], [current], rules).status, "needs_review"
        )


class CashFlowStatementTests(unittest.TestCase):
    def table(
        self,
        rows,
        dates="<th>Dec. 31, 2020</th><th>Dec. 31, 2019</th>",
        duration="12 Months Ended",
    ):
        html = f'<table class="report"><tr><th>CONSOLIDATED STATEMENTS OF CASH FLOWS - USD ($) $ in Millions</th><th colspan="2">{duration}</th></tr><tr>{dates}</tr>'
        for tag, label, left, right in rows:
            html += f'<tr><td><a onclick="defref_us-gaap_{tag}">{label}</a></td><td>{left}</td><td>{right}</td></tr>'
        return html + "</table>"

    def test_annual_date_and_monetary_scale_are_preserved(self):
        """The 2020 total is 100 million; a 2019 amount and investing flow are not substituted."""
        html = self.table(
            [
                (TOTAL_TAG, "Net cash provided by operating activities", "100", "90"),
                (
                    "NetCashProvidedByUsedInInvestingActivities",
                    "Investing cash flows",
                    "999",
                    "888",
                ),
            ]
        )
        positions, discontinued = cash_positions(html, END)
        selected, calculation, _ = choose_positions(positions, discontinued)
        self.assertEqual(selected[0]["value"], Decimal(100000000))
        self.assertIsNone(calculation)

    def test_total_is_preferred_to_nonoverlapping_component_calculation(self):
        """A reported 100 total is retained instead of re-adding its continuing/discontinued breakdown."""
        html = self.table(
            [
                (TOTAL_TAG, "Operating cash flow", "100", "90"),
                (
                    CONTINUING_TAG,
                    "Operating cash flow from continuing operations",
                    "80",
                    "70",
                ),
                (
                    DISCONTINUED_TAG,
                    "Operating cash flow from discontinued operations",
                    "20",
                    "20",
                ),
            ]
        )
        positions, flag = cash_positions(html, END)
        selected, _, _ = choose_positions(positions, flag)
        self.assertEqual([p["tag"] for p in selected], [TOTAL_TAG])

    def test_continuing_only_requires_discontinued_operating_component(self):
        """An explicitly continuing-only 80 is not a whole-company value without the missing component."""
        html = self.table(
            [
                (
                    CONTINUING_TAG,
                    "Operating cash flow from continuing operations",
                    "80",
                    "70",
                )
            ]
        )
        positions, flag = cash_positions(html, END)
        with self.assertRaisesRegex(ValueError, "Continuing-only"):
            choose_positions(positions, flag)
        html = self.table(
            [
                (
                    CONTINUING_TAG,
                    "Operating cash flow from continuing operations",
                    "80",
                    "70",
                ),
                (
                    DISCONTINUED_TAG,
                    "Operating cash flow from discontinued operations",
                    "(20)",
                    "0",
                ),
            ]
        )
        positions, flag = cash_positions(html, END)
        selected, calculation, _ = choose_positions(positions, flag)
        self.assertEqual(sum(p["value"] for p in selected), Decimal(60000000))
        self.assertIsNotNone(calculation)

    def test_unqualified_bottom_line_has_a_source_specific_legacy_alias(self):
        """An old continuing tag can identify an unqualified face-statement total only after source review."""
        html = self.table(
            [(CONTINUING_TAG, "Net cash provided by operating activities", "100", "90")]
        )
        positions, flag = cash_positions(html, END)
        selected, _, note = choose_positions(positions, flag)
        self.assertEqual(selected[0]["value"], Decimal(100000000))
        self.assertIn("not a universal", note)

    def test_ytd_and_dimension_tables_cannot_define_annual_total(self):
        """A nine-month cash flow cannot populate the annual column."""
        html = self.table(
            [(TOTAL_TAG, "Operating cash flow", "100", "90")], duration="9 Months Ended"
        )
        with self.assertRaises(ValueError):
            cash_positions(html, END)

    def test_cash_flow_file_selection_uses_face_statements(self):
        """Exclude notes, reconciliations and subsidiary cash-flow sheets when a parent sheet exists."""
        summary = "<FilingSummary><MyReports>"
        for name, category, filename in [
            ("Consolidated Statements of Cash Flows", "Statements", "R5.htm"),
            (
                "Consolidated Statements of Cash Flows - Subsidiary",
                "Statements",
                "R7.htm",
            ),
            ("Cash Flows", "Notes", "R30.htm"),
            ("Cash Flow Reconciliation", "Statements", "R6.htm"),
        ]:
            summary += f"<Report><ShortName>{name}</ShortName><MenuCategory>{category}</MenuCategory><HtmlFileName>{filename}</HtmlFileName></Report>"
        summary += "</MyReports></FilingSummary>"
        self.assertEqual(cash_flow_files(summary, "0000320193"), ["R5.htm"])

    def test_all_discontinued_cash_flows_are_not_an_operating_component(self):
        """Discontinued operating, investing and financing totals cannot be added to continuing OCF."""
        html = self.table(
            [
                (
                    CONTINUING_TAG,
                    "Operating cash flow from continuing operations",
                    "80",
                    "70",
                ),
                (
                    "NetCashProvidedByUsedInDiscontinuedOperations",
                    "Net cash from discontinued operations",
                    "20",
                    "10",
                ),
            ]
        )
        positions, flag = cash_positions(html, END)
        with self.assertRaisesRegex(ValueError, "Continuing-only"):
            choose_positions(positions, flag)

    def test_total_label_including_discontinued_operations_is_a_whole_total(self):
        """AMD's including-discontinued total must not be misclassified as a discontinued-only component."""
        html = self.table(
            [
                (
                    TOTAL_TAG,
                    "Cash Provided by Operating Activity, Including Discontinued Operation, Total",
                    "100",
                    "90",
                )
            ]
        )
        positions, flag = cash_positions(html, END)
        selected, calculation, _ = choose_positions(positions, flag)
        self.assertEqual(selected[0]["scope"], "total")
        self.assertIsNone(calculation)

    def test_duplicate_identical_face_and_reconciliation_rows_are_not_conflicts(self):
        """CVS repeats the same operating total in its cash reconciliation; it remains one value."""
        row = (TOTAL_TAG, "Net cash from operating activities", "100", "90")
        positions, flag = cash_positions(self.table([row, row]), END)
        self.assertEqual(
            choose_positions(positions, flag)[0][0]["value"], Decimal(100000000)
        )
        conflict = (TOTAL_TAG, "Net cash from operating activities", "99", "90")
        with self.assertRaisesRegex(ValueError, "Different cash-flow"):
            cash_positions(self.table([row, conflict]), END)

    def test_continuing_section_prevents_an_unqualified_row_alias(self):
        """A continuing-operations section limits an unqualified subtotal beneath it."""
        html = self.table(
            [
                (
                    "NetCashProvidedByUsedInOperatingActivitiesAbstract",
                    "Operating activities of continuing operations",
                    "",
                    "",
                ),
                (CONTINUING_TAG, "Net cash from operating activities", "100", "90"),
            ]
        )
        positions, flag = cash_positions(html, END)
        with self.assertRaisesRegex(ValueError, "Continuing-only"):
            choose_positions(positions, flag)

    def test_displayed_zero_is_not_a_missing_cash_flow(self):
        """An explicit monetary dash is zero; an empty annual cell remains missing."""
        html = self.table(
            [(TOTAL_TAG, "Net cash from operating activities", "-", "90")]
        )
        positions, flag = cash_positions(html, END)
        self.assertEqual(choose_positions(positions, flag)[0][0]["value"], Decimal(0))
        positions, _ = cash_positions(
            self.table([(TOTAL_TAG, "Net cash from operating activities", "", "90")]),
            END,
        )
        self.assertEqual(positions, {})

    def test_reconciliation_wording_does_not_restrict_the_operating_section(self):
        """Walmart/Pfizer reconcile continuing income to total OCF; that wording is not a section header."""
        html = self.table(
            [
                (TOTAL_TAG + "Abstract", "Operating activities", "", ""),
                (
                    "AdjustmentsToReconcileNetIncomeLossToCashProvidedByUsedInOperatingActivitiesAbstract",
                    "Adjustments to reconcile income from continuing operations to net cash provided by operating activities",
                    "",
                    "",
                ),
                (TOTAL_TAG, "Net cash provided by operating activities", "100", "90"),
            ]
        )
        positions, flag = cash_positions(html, END)
        self.assertEqual(choose_positions(positions, flag)[0][0]["scope"], "total")

    def test_generic_total_tag_inherits_explicit_continuing_section(self):
        """A generic total label cannot undo a continuing-only operating section."""
        html = self.table(
            [
                (
                    CONTINUING_TAG + "Abstract",
                    "Operating activities of continuing operations",
                    "",
                    "",
                ),
                (TOTAL_TAG, "Net cash provided by operating activities", "100", "90"),
            ]
        )
        positions, flag = cash_positions(html, END)
        with self.assertRaisesRegex(ValueError, "Continuing-only"):
            choose_positions(positions, flag)

    def test_explicit_operating_discontinued_label_retains_misused_original_tag(self):
        """Fiserv labels its aggregate concept as operating-only; retain the source label and concept."""
        html = self.table(
            [
                (
                    CONTINUING_TAG,
                    "Operating cash flow from continuing operations",
                    "80",
                    "70",
                ),
                (
                    DISCONTINUED_CASH_TAG,
                    "Net cash flows from discontinued operations provided by operating activities",
                    "0",
                    "19",
                ),
            ]
        )
        positions, flag = cash_positions(html, END)
        selected, calculation, _ = choose_positions(positions, flag)
        self.assertEqual(
            [p["tag"] for p in selected], [CONTINUING_TAG, DISCONTINUED_CASH_TAG]
        )
        self.assertEqual(sum(p["value"] for p in selected), Decimal(80000000))
        self.assertIsNotNone(calculation)

    def test_discontinued_net_income_cannot_fill_missing_discontinued_cash_flow(self):
        """Citigroup's discontinued income is an accrual amount, not the missing operating cash component."""
        html = self.table(
            [
                (
                    CONTINUING_TAG,
                    "Operating cash flow from continuing operations",
                    "80",
                    "70",
                ),
                (
                    "IncomeLossFromDiscontinuedOperationsNetOfTax",
                    "Income from discontinued operations",
                    "2",
                    "1",
                ),
            ]
        )
        positions, flag = cash_positions(html, END)
        with self.assertRaisesRegex(ValueError, "Continuing-only"):
            choose_positions(positions, flag)

    def test_previous_basis_and_subsidiary_blocks_do_not_override_current_total(self):
        """Marriott's current consolidated note column must survive different old-basis and segment rows."""
        html = self.table(
            [
                (
                    TOTAL_TAG,
                    "Net cash provided by operating activities",
                    "1619",
                    "1500",
                ),
                ("RestatementAxis", "As Previously Reported", "", ""),
                (
                    TOTAL_TAG,
                    "Net cash provided by operating activities",
                    "1600",
                    "1400",
                ),
            ]
        )
        positions, flag = cash_positions(html, END)
        self.assertEqual(
            choose_positions(positions, flag)[0][0]["value"], Decimal(1619000000)
        )


class ReviewedCashFlowResolutionTests(unittest.TestCase):
    """Verify the four explicit-zero comparisons and separately reviewed disclosure gaps."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.latest_path = Path(self.directory.name) / "latest.htm"
        self.previous_path = Path(self.directory.name) / "previous.htm"
        self.proof_path = Path(self.directory.name) / "scope.htm"
        self.reference = {
            "url": "https://www.sec.gov/latest/R5.htm",
            "sha256": "latest",
        }
        self.previous_reference = {
            "url": "https://www.sec.gov/previous/R5.htm",
            "sha256": "previous",
        }
        self.proof_reference = {
            "url": "https://www.sec.gov/latest/R12.htm",
            "sha256": "scope",
        }
        self.older = cash_fact(tag=CONTINUING_TAG, value=Decimal(100000000))
        self.current = cash_fact(
            tag=CONTINUING_TAG,
            value=Decimal(100000000),
            filed=date(2022, 2, 1),
            accession="latest",
        )
        self.variants = [FilingValue.model_validate(self.current)]
        self.task = {
            "period": SimpleNamespace(period_start=START, period_end=END),
            "facts": [self.older, self.current],
            "variants": self.variants,
        }
        self.case = {
            "resolution": "reported_zero",
            "observed_sha256": variants_fingerprint(self.variants),
            "latest_accession": "latest",
            "latest_statement": self.reference,
            "previous_accession": "original",
            "previous_statement": self.previous_reference,
            "note": "An explicit earlier operating zero and unchanged continuing amount were reviewed.",
        }
        builder = CashFlowStatementTests()
        self.latest_html = builder.table(
            [
                (
                    CONTINUING_TAG,
                    "Operating cash flow from continuing operations",
                    "100",
                    "90",
                )
            ]
        )
        self.previous_html = builder.table(
            [
                (
                    CONTINUING_TAG,
                    "Operating cash flow from continuing operations",
                    "100",
                    "90",
                ),
                (
                    DISCONTINUED_TAG,
                    "Operating cash flow from discontinued operations",
                    "0",
                    "0",
                ),
            ]
        )
        self.latest_path.write_text(self.latest_html, encoding="utf-8")
        self.previous_path.write_text(self.previous_html, encoding="utf-8")
        self.proof_path.write_text(
            '<table class="report"><tr><td>Cash flows from discontinued operations were not material for all periods presented.</td></tr></table>',
            encoding="utf-8",
        )
        paths = {
            self.reference["url"]: self.latest_path,
            self.previous_reference["url"]: self.previous_path,
            self.proof_reference["url"]: self.proof_path,
        }
        verifier = patch(
            "app.data_pipeline.operating_cash_flow.verify_reference",
            side_effect=lambda ref: paths[ref["url"]],
        )
        verifier.start()
        self.addCleanup(verifier.stop)

    def resolve(self, exceptions=None, reference=None):
        positions, _ = cash_positions(self.latest_html, END)
        return resolve_reviewed_period(
            self.task,
            {},
            reference or self.reference,
            positions,
            [self.current],
            {KEY: self.case} if exceptions is None else exceptions,
        )

    def test_complete_comparison_preserves_older_sources_and_latest_control(self):
        """The audited whole-company 100 retains both older filing components and the latest subtotal control."""
        choice = self.resolve()
        self.assertEqual(choice["value"], "100000000")
        self.assertEqual(choice["current_continuing"]["accession"], "latest")
        self.assertEqual(
            {c["fact"]["accession"] for c in choice["components"]}, {"original"}
        )
        self.assertEqual(choice["components"][1]["fact"]["value"], "0")

    def test_missing_or_changed_pinned_source_does_not_resolve_the_period(self):
        """An arbitrary omission or a changed source hash cannot become a completed review."""
        self.assertIsNone(self.resolve(exceptions={}))
        self.assertIsNone(
            self.resolve(reference=dict(self.reference, sha256="changed"))
        )
        self.case["observed_sha256"] = "changed"
        self.assertIsNone(self.resolve())

    def test_changed_continuing_or_nonzero_discontinued_position_is_rejected(self):
        """An older amount 99 or discontinued outflow -1 invalidates the reviewed zero comparison."""
        self.task["facts"][0] = dict(self.older, value=Decimal(99000000))
        with self.assertRaises(ValueError):
            self.resolve()
        self.task["facts"][0] = self.older
        self.previous_path.write_text(
            self.previous_html.replace(">0<", ">1<"), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "zero"):
            self.resolve()

    def test_known_missing_scope_requires_its_verified_disclosure(self):
        """Citi's not-material disclosure establishes a known missing amount, never zero."""
        self.case.update(
            resolution="continuing_only",
            scope_disclosure=self.proof_reference,
            note="The operating amount is not separately disclosed.",
        )
        result = self.resolve()
        self.assertEqual(result["scope"], "continuing")
        self.assertEqual(result["value"], "100000000")
        self.assertEqual(result["components"][0]["fact"]["accession"], "latest")
        self.proof_path.write_text(
            '<table class="report"><tr><td>Unrelated disclosure.</td></tr></table>',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "disclosure changed"):
            self.resolve()

    def test_continuing_scope_needs_evidence_and_rejects_new_operating_fact(self):
        """A known gap requires evidence; an original operating disclosure invalidates that decision."""
        self.case.update(resolution="continuing_only")
        with self.assertRaisesRegex(ValueError, "disclosure evidence"):
            self.resolve()
        self.case.update(
            complete_filing=self.reference, original_instance=self.proof_reference
        )
        self.proof_path.write_text(
            '<xbrl xmlns:gaap="http://fasb.org/us-gaap/2020"><gaap:CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations contextRef="annual">-20</gaap:CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations></xbrl>',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "new scope review"):
            self.resolve()


class ReviewedCashFlowEvidenceTests(unittest.TestCase):
    def document(self):
        return {
            "version": 1,
            "selection_sha256": "selection",
            "periods": {
                KEY: {
                    "latest_statement": {
                        "url": "https://www.sec.gov/latest/R5.htm",
                        "sha256": "expected",
                    }
                }
            },
        }

    def test_fetch_reuses_verified_pinned_evidence_without_network(self):
        """Rebuilding an existing audit must reuse its immutable local sources."""
        with (
            patch(
                "app.data_pipeline.operating_cash_flow.read_json",
                return_value=self.document(),
            ),
            patch(
                "app.data_pipeline.operating_cash_flow.sha256", return_value="selection"
            ),
            patch("app.data_pipeline.operating_cash_flow.verify_reference"),
            patch("app.data_pipeline.operating_cash_flow.cached") as download,
        ):
            cases = reviewed_exceptions(fetch=True)
        self.assertIn(KEY, cases)
        download.assert_not_called()

    def test_changed_redownloaded_evidence_is_not_silently_approved(self):
        """A changed SEC response must invalidate a pinned review instead of replacing its expected hash."""
        with (
            patch(
                "app.data_pipeline.operating_cash_flow.read_json",
                return_value=self.document(),
            ),
            patch(
                "app.data_pipeline.operating_cash_flow.sha256", return_value="selection"
            ),
            patch(
                "app.data_pipeline.operating_cash_flow.verify_reference",
                side_effect=OSError("Missing cache"),
            ),
            patch("app.data_pipeline.operating_cash_flow.sleep"),
            patch(
                "app.data_pipeline.operating_cash_flow.cached",
                return_value={"sha256": "changed"},
            ),
        ):
            with self.assertRaisesRegex(ValueError, "source changed"):
                reviewed_exceptions(fetch=True)
