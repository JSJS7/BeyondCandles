"""Verify exact equity approvals, source coherence and independent statement controls."""

import unittest
from datetime import date
from decimal import Decimal

from test_stocks_api import reported_fact

from app.data_pipeline.equity import (
    select_components,
    statement_files,
    statement_positions,
)
from app.equity import fact_identity, select_equity, variants_fingerprint
from app.schemas.stock import FilingValue

START = date(2020, 1, 1)
END = date(2020, 12, 31)
KEY = "0000320193:2020-12-31"
GROUP = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"


def balance(**changes):
    fields = {
        "metric": "equity",
        "tag": "StockholdersEquity",
        "period_kind": "instant",
        "period_start": END,
    }
    fields.update(changes)
    return reported_fact(**fields)


def approval(variants, components, expected, **changes):
    rule = {
        "observed_sha256": variants_fingerprint(variants),
        "value": expected,
        "components": [
            {"fact": fact_identity(fact), "coefficient": coefficient}
            for fact, coefficient in components
        ],
        "note": "Checked against the dated face statement.",
    }
    rule.update(changes)
    return {KEY: rule}


class EquitySelectionTests(unittest.TestCase):
    def select(self, facts, variants, rules):
        values = [FilingValue.model_validate(fact) for fact in variants]
        return select_equity(
            values,
            START,
            END,
            facts=[reported_fact(), *facts],
            calendar={},
            rules=rules,
        )

    def test_single_unverified_tag_is_not_a_verified_parent_balance(self):
        """A solitary parent or group tag requires a source approval, not tag priority."""
        for tag in ("StockholdersEquity", GROUP, "PartnersCapital"):
            with self.subTest(tag=tag):
                fact = balance(tag=tag)
                result = self.select([fact], [fact], {})
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)
        self.assertEqual(self.select([], [], {}).status, "not_available")

    def test_reported_negative_and_zero_balances_are_preserved(self):
        """A verified deficit or explicit zero is a valid equity value."""
        for amount in ("0", "-3797000000", "100"):
            with self.subTest(amount=amount):
                fact = balance(value=Decimal(amount))
                result = self.select(
                    [fact], [fact], approval([fact], [(fact, 1)], amount)
                )
                self.assertEqual(result.status, "ready")
                self.assertEqual(result.value, Decimal(amount))
                self.assertEqual(result.selected_tag, "StockholdersEquity")

    def test_calculation_preserves_signed_nci_and_both_original_sources(self):
        """Subtract a signed NCI balance from group equity in one filing, including explicit zero."""
        for minority, expected in (("20", "80"), ("-20", "120"), ("0", "100")):
            with self.subTest(minority=minority):
                group = balance(tag=GROUP)
                nci = balance(
                    metric="equity",
                    tag="MinorityInterest",
                    value=Decimal(minority),
                )
                rules = approval(
                    [group],
                    [(group, 1), (nci, -1)],
                    expected,
                    calculation="Group equity - NCI",
                )
                result = self.select([group, nci], [group], rules)
                self.assertEqual(result.value, Decimal(expected))
                self.assertEqual(len(result.selected_sources), 2)
                self.assertEqual(len(result.variants), 2)
                self.assertIsNone(result.selected_tag)

    def test_new_filing_or_conflict_invalidates_an_old_snapshot(self):
        """A newer report, changed amount or same-day conflict requires a fresh review."""
        fact = balance()
        rules = approval([fact], [(fact, 1)], "100")
        alternatives = (
            balance(filed=date(2022, 1, 1), accession="new"),
            balance(value=Decimal("99")),
            balance(value=Decimal("99"), accession="conflict"),
        )
        for alternative in alternatives:
            with self.subTest(alternative=alternative):
                result = self.select([fact, alternative], [fact, alternative], rules)
                self.assertEqual(result.status, "needs_review")

    def test_missing_changed_and_mixed_filing_components_are_rejected(self):
        """A missing or changed allocation is never treated as zero or borrowed from another filing."""
        group = balance(tag=GROUP)
        nci = balance(metric="equity", tag="MinorityInterest", value=Decimal("20"))
        rules = approval([group], [(group, 1), (nci, -1)], "80")
        for parts in (
            [group],
            [group, dict(nci, value=Decimal("21"))],
            [group, dict(nci, accession="other")],
        ):
            with self.subTest(parts=parts):
                result = self.select(parts, [group], rules)
                self.assertEqual(result.status, "needs_review")
        mixed = dict(nci, accession="other")
        result = self.select(
            [group, mixed], [group], approval([group], [(group, 1), (mixed, -1)], "80")
        )
        self.assertEqual(result.status, "needs_review")

    def test_rejected_candidate_is_retained_without_overriding_verified_parent(self):
        """Keep an anomalous alternative visible while selecting the exact reviewed balance."""
        parent = balance(value=Decimal("5399900000"))
        wrong = balance(tag=GROUP, value=Decimal("-257300000"))
        result = self.select(
            [parent, wrong],
            [parent, wrong],
            approval([parent, wrong], [(parent, 1)], "5399900000"),
        )
        self.assertEqual(result.value, Decimal("5399900000"))
        self.assertEqual(len(result.variants), 2)

    def test_history_exclusion_requires_a_narrow_source_approval(self):
        """Do not bypass issuer-history rules merely because an excluded source matches a number."""
        fact = balance(history_eligible=False)
        result = self.select([fact], [], approval([], [(fact, 1)], "100"))
        self.assertEqual(result.status, "not_available")

    def test_decimal_identity_is_exact_and_independent_of_trailing_zeros(self):
        """Fingerprinting must not round database-sized decimals or depend on their textual scale."""
        first = balance(value=Decimal("1234567890123456789012345678.1234567890"))
        second = dict(first, value=Decimal("1234567890123456789012345678.123456789"))
        self.assertEqual(
            fact_identity(first)["value"], "1234567890123456789012345678.123456789"
        )
        self.assertEqual(variants_fingerprint([first]), variants_fingerprint([second]))

    def test_report_column_keeps_its_actual_concept_and_live_filing_anchor(self):
        """A parent column omitted by Companyfacts remains tied to its actual report context."""
        group = balance(tag=GROUP)
        parent = dict(
            group,
            value=Decimal("80"),
            statement_url="https://www.sec.gov/Archives/R7.htm",
            statement_label="Balance at Dec. 31, 2020",
            statement_column="Parent [Member]",
        )
        rules = approval(
            [group],
            [(parent, 1)],
            "80",
            anchor=fact_identity(group),
            statement={"url": parent["statement_url"]},
        )
        rules[KEY]["components"][0]["statement_position"] = True
        result = self.select([group], [group], rules)
        self.assertEqual(result.value, Decimal("80"))
        self.assertEqual(result.selected_sources[0].tag, GROUP)
        self.assertEqual(result.selected_sources[0].statement_column, "Parent [Member]")
        self.assertEqual(len(result.variants), 2)
        changed = dict(group, value=Decimal("99"))
        self.assertEqual(self.select([changed], [group], rules).status, "needs_review")

    def test_statement_allocation_is_calculated_without_inventing_a_new_tag(self):
        """An NCI column of the group concept is a report position, not a fake MinorityInterest fact."""
        group = balance(tag=GROUP)
        minority = dict(
            group,
            value=Decimal("20"),
            statement_url="https://www.sec.gov/Archives/R7.htm",
            statement_label="Balance at Dec. 31, 2020",
            statement_column="Noncontrolling interests",
        )
        rules = approval(
            [group],
            [(group, 1), (minority, -1)],
            "80",
            anchor=fact_identity(group),
            statement={"url": minority["statement_url"]},
            calculation="Group equity - NCI",
        )
        rules[KEY]["components"][1]["statement_position"] = True
        result = self.select([group], [group], rules)
        self.assertEqual(result.value, Decimal("80"))
        self.assertEqual(
            [source.tag for source in result.selected_sources], [GROUP, GROUP]
        )
        self.assertIsNone(result.selected_tag)
        rules[KEY]["components"][1]["fact"]["statement_url"] = (
            "https://www.sec.gov/Archives/other.htm"
        )
        self.assertEqual(self.select([group], [group], rules).status, "needs_review")


class EquityStatementTests(unittest.TestCase):
    def test_balance_dates_scale_and_parent_scope_are_distinct(self):
        """Read the correct date and distinguish parent equity from group equity and NCI."""
        html = '<table class="report"><tr><th>USD ($) $ in Millions</th><th>Dec. 31, 2020</th><th>Dec. 31, 2019</th></tr>'
        for tag, label, current, previous in (
            ("StockholdersEquity", "Total shareholders equity", "80", "70"),
            ("MinorityInterest", "Noncontrolling interests", "20", "10"),
            (GROUP, "Total equity", "100", "80"),
        ):
            html += f'<tr><td><a onclick="defref_us-gaap_{tag}">{label}</a></td><td>{current}</td><td>{previous}</td></tr>'
        html += "</table>"
        positions = statement_positions(html, END, "balance")
        self.assertEqual(
            {p["scope"]: p["value"] for p in positions},
            {
                "parent": Decimal("80000000"),
                "minority": Decimal("20000000"),
                "group": Decimal("100000000"),
            },
        )

    def test_equity_rollforward_reads_parent_column_and_dated_balance(self):
        """Use the dated parent column, not the consolidated total or another year's opening balance."""
        html = '<table class="report"><tr><th>USD ($) $ in Millions</th><th>Total</th><th>Parent [Member]</th><th>Noncontrolling interests</th></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Balance at Dec. 31, 2020</a></td><td>100</td><td>80</td><td>20</td></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Balance at Dec. 31, 2021</a></td><td>200</td><td>150</td><td>50</td></tr></table>'
        positions = statement_positions(html, END, "equity")
        self.assertEqual(
            {p["scope"]: p["value"] for p in positions},
            {
                "parent": Decimal("80000000"),
                "minority": Decimal("20000000"),
                "group": Decimal("100000000"),
            },
        )

    def test_conflicting_joint_statement_values_are_not_accepted_by_size(self):
        """Conflicting parent scopes require review instead of choosing the larger balance."""
        parent = balance(value=Decimal("100"))
        positions = [
            {"scope": "parent", "value": Decimal("100")},
            {"scope": "parent", "value": Decimal("200")},
        ]
        second = balance(value=Decimal("200"), accession="other")
        with self.assertRaises(ValueError):
            select_components(
                positions,
                [parent, second],
                parent["source_cik"],
                parent["accession"],
                END,
                {},
                parent["source_cik"],
            )

    def test_source_file_selection_excludes_notes_and_parentheticals(self):
        """Only primary face statements may establish a generic equity selection."""
        xml = "<FilingSummary><MyReports>"
        for name, category, filename in (
            ("Consolidated Balance Sheets", "Statements", "R2.htm"),
            ("Consolidated Balance Sheets (Parenthetical)", "Statements", "R3.htm"),
            ("Equity Note", "Notes", "R20.htm"),
            ("Consolidated Statements of Equity", "Statements", "R7.htm"),
        ):
            xml += f"<Report><ShortName>{name}</ShortName><MenuCategory>{category}</MenuCategory><HtmlFileName>{filename}</HtmlFileName></Report>"
        xml += "</MyReports></FilingSummary>"
        self.assertEqual(statement_files(xml, "balance"), ["R2.htm"])
        self.assertEqual(statement_files(xml, "equity"), ["R7.htm"])

    def test_mislabelled_component_is_not_accepted_as_total_parent_equity(self):
        """A misleading StockholdersEquity tag on an AOCI row must fail the face-label check."""
        html = '<table class="report"><tr><th>USD ($) $ in Millions</th><th>Dec. 31, 2020</th></tr>'
        html += '<tr><td><a onclick="defref_us-gaap_StockholdersEquity">Accumulated other comprehensive loss</a></td><td>(258)</td></tr></table>'
        self.assertEqual(statement_positions(html, END, "balance"), [])

    def test_component_column_is_not_a_whole_equity_balance(self):
        """Do not interpret the common-stock column of a roll-forward as total parent equity."""
        html = '<table class="report"><tr><th>USD ($) $ in Millions</th><th>Common Stock</th><th>Retained earnings</th></tr>'
        html += '<tr><td><a onclick="defref_us-gaap_StockholdersEquity">Balance at Dec. 31, 2020</a></td><td>100</td><td>900</td></tr></table>'
        self.assertEqual(statement_positions(html, END, "equity"), [])

    def test_parent_accounts_reconcile_broadcom_total_without_assuming_missing_nci(
        self,
    ):
        """The reported 2024 capital accounts independently reconcile Broadcom's 67,678 million total."""
        html = '<table class="report"><tr><th>USD ($) $ in Millions</th><th>Total</th><th>Common Stock</th><th>Additional Paid-in Capital</th><th>Retained Earnings (Accumulated Deficit)</th><th>Accumulated Other Comprehensive Income (Loss)</th></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Beginning Balance at Dec. 31, 2020</a></td><td>67,678</td><td>5</td><td>67,466</td><td>0</td><td>207</td></tr></table>'
        positions = statement_positions(html, END, "equity")
        parent = next(
            position for position in positions if position["scope"] == "parent"
        )
        self.assertEqual(parent["value"], Decimal("67678000000"))
        self.assertEqual(len(parent["scope_control"]), 4)

    def test_common_equity_is_not_total_parent_equity(self):
        """Preferred shares belong to parent equity; a common-only column must not compete with it."""
        html = '<table class="report"><tr><th>USD ($) $ in Millions</th><th>Total</th><th>Citigroup stockholders equity</th><th>Total Citigroup common stockholders equity</th><th>Noncontrolling interests</th></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Balance at Dec. 31, 2020</a></td><td>209,366</td><td>208,598</td><td>190,748</td><td>768</td></tr></table>'
        positions = statement_positions(html, END, "equity")
        self.assertEqual(
            [p["value"] for p in positions if p["scope"] == "parent"],
            [Decimal("208598000000")],
        )

    def test_overlapping_or_unknown_accounts_cannot_prove_parent_scope(self):
        """Even a matching sum cannot approve overlapping APIC columns or an unknown ownership class."""
        from app.data_pipeline.equity import closed_parent_accounts

        columns = [
            "Statement",
            "Total",
            "Common stock and additional paid-in capital",
            "Additional paid-in capital",
            "Retained earnings",
        ]
        self.assertIsNone(
            closed_parent_accounts(columns, ["", "1000", "100", "200", "700"], 1, 1)
        )
        columns = ["Statement", "Total", "Common Stock", "Unknown ownership class"]
        self.assertIsNone(
            closed_parent_accounts(columns, ["", "1000", "100", "900"], 1, 1)
        )

    def test_date_only_header_keeps_the_row_label_offset(self):
        """A separate date-header row must not shift a 2020 balance into the 2019 column."""
        html = '<table class="report"><tr><th>USD ($) $ in Millions</th><th colspan="2">Balances</th></tr><tr><th>Dec. 31, 2020</th><th>Dec. 31, 2019</th></tr>'
        html += '<tr><td><a onclick="defref_us-gaap_StockholdersEquity">Total shareholders equity</a></td><td>80</td><td>70</td></tr></table>'
        parent = statement_positions(html, END, "balance")
        self.assertEqual(parent[0]["value"], Decimal("80000000"))

    def test_overall_nci_is_not_added_to_its_subsidiary_breakdown(self):
        """The overall 20 balance already includes the subsidiary's 5 balance."""
        html = '<table class="report"><tr><th>USD ($)</th><th>Total</th><th>Noncontrolling Interests</th><th>Noncontrolling Interests Subsidiary</th></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Balance at Dec. 31, 2020</a></td><td>100</td><td>20</td><td>5</td></tr></table>'
        positions = statement_positions(html, END, "equity")
        group = balance(tag=GROUP)
        _, coefficients, amount, _ = select_components(
            positions,
            [group],
            group["source_cik"],
            group["accession"],
            END,
            {},
            group["source_cik"],
        )
        self.assertEqual(coefficients, [1, -1])
        self.assertEqual(amount, Decimal("80"))

    def test_consolidated_balance_ends_before_additional_segment_balance_sheets(self):
        """GE's consolidated sheet must not be combined with later segment sheets in the same report."""
        html = '<table class="report"><tr><th>USD ($)</th><th>Dec. 31, 2020</th></tr>'
        for tag, label, number in [
            ("StockholdersEquity", "Total GE shareowners equity", "100"),
            (GROUP, "Total equity", "120"),
            ("LiabilitiesAndStockholdersEquity", "Total liabilities and equity", "500"),
            ("StockholdersEquity", "Total GE shareowners equity", "50"),
        ]:
            html += f'<tr><td><a onclick="defref_us-gaap_{tag}">{label}</a></td><td>{number}</td></tr>'
        html += "</table>"
        positions = statement_positions(html, END, "balance")
        self.assertEqual(
            [p["value"] for p in positions if p["scope"] == "parent"], [Decimal("100")]
        )

    def test_generic_shareholder_heading_does_not_override_reported_nci(self):
        """A generic shareholder column cannot turn group equity into parent equity when NCI is nonzero."""
        html = '<table class="report"><tr><th>USD ($)</th><th>Total</th><th>Shareholders Equity</th><th>Noncontrolling interests</th></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Balance at Dec. 31, 2020</a></td><td>100</td><td>100</td><td>20</td></tr></table>'
        positions = statement_positions(html, END, "equity")
        group = balance(tag=GROUP)
        selected, coefficients, value, _ = select_components(
            positions,
            [group],
            group["source_cik"],
            group["accession"],
            END,
            {},
            group["source_cik"],
        )
        self.assertEqual(value, Decimal("80"))
        self.assertEqual(coefficients, [1, -1])

    def test_generic_shareholder_heading_without_scope_evidence_requires_review(self):
        """Do not approve an omitted ownership scope using an ambiguous column title alone."""
        html = '<table class="report"><tr><th>USD ($)</th><th>Total</th><th>Shareholders Equity</th></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Balance at Dec. 31, 2020</a></td><td>100</td><td>80</td></tr></table>'
        group = balance(tag=GROUP)
        positions = statement_positions(html, END, "equity")
        with self.assertRaises(ValueError):
            select_components(
                positions,
                [group],
                group["source_cik"],
                group["accession"],
                END,
                {},
                group["source_cik"],
            )

    def test_dated_nci_on_a_separate_row_is_deducted_once(self):
        """RTX-style equity tables report the total and NCI in separate dated balance rows."""
        html = '<table class="report"><tr><th>USD ($)</th><th>Total</th><th>Common stock</th><th>Noncontrolling Interest</th></tr>'
        html += "<tr><td>Balance at Dec. 31, 2020</td><td></td><td></td><td></td></tr>"
        html += '<tr><td><a onclick="defref_us-gaap_MinorityInterest">Noncontrolling interest</a></td><td></td><td></td><td>20</td></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Total equity</a></td><td>100</td><td></td><td></td></tr>'
        html += "<tr><td>Balance at Dec. 31, 2021</td><td></td><td></td><td></td></tr>"
        html += '<tr><td><a onclick="defref_us-gaap_MinorityInterest">Noncontrolling interest</a></td><td></td><td></td><td>30</td></tr></table>'
        group = balance(tag=GROUP)
        positions = statement_positions(html, END, "equity")
        selected, coefficients, amount, _ = select_components(
            positions,
            [group],
            group["source_cik"],
            group["accession"],
            END,
            {},
            group["source_cik"],
        )
        self.assertEqual(amount, Decimal("80"))
        self.assertEqual(coefficients, [1, -1])
        self.assertEqual(selected[1]["value"], Decimal("20"))

    def test_revision_column_does_not_compete_with_parent_equity(self):
        """Tesla's adoption adjustment is not a second ownership balance."""
        html = '<table class="report"><tr><th>USD ($)</th><th>Total</th><th>Total Stockholders Equity</th><th>Total Stockholders Equity Revision of Prior Period Adjustment</th><th>Noncontrolling interests</th></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Balance at Dec. 31, 2020</a></td><td>100</td><td>80</td><td>(5)</td><td>20</td></tr></table>'
        group = balance(tag=GROUP)
        positions = statement_positions(html, END, "equity")
        _, _, amount, _ = select_components(
            positions,
            [group],
            group["source_cik"],
            group["accession"],
            END,
            {},
            group["source_cik"],
        )
        self.assertEqual(amount, Decimal("80"))

    def test_parent_account_closure_excludes_redeemable_nci(self):
        """Copart's temporary NCI sits outside permanent parent equity and its account sum."""
        from app.data_pipeline.equity import closed_parent_accounts

        columns = [
            "Statement",
            "Total",
            "Common Stock",
            "Retained Earnings",
            "Redeemable Noncontrolling Interest",
        ]
        controls = closed_parent_accounts(columns, ["", "100", "1", "99", "20"], 1, 1)
        assert controls is not None
        self.assertEqual(
            sum(Decimal(item["value"]) for item in controls), Decimal("100")
        )
        self.assertIsNone(
            closed_parent_accounts(columns, ["", "120", "1", "99", "20"], 1, 1)
        )

    def test_dimension_blocks_do_not_turn_equity_accounts_into_parent_totals(self):
        """A later AOCI/member block must not override the consolidated parent row."""
        html = '<table class="report"><tr><th>USD ($)</th><th>Dec. 31, 2020</th></tr>'
        html += '<tr><td><a onclick="defref_us-gaap_StockholdersEquity">Total shareholders equity</a></td><td>80</td></tr>'
        html += '<tr><td><a onclick="defref_us-gaap_StatementEquityComponentsAxis">AOCI</a></td><td></td></tr>'
        html += '<tr><td><a onclick="defref_us-gaap_StockholdersEquity">Total shareholders equity</a></td><td>(5)</td></tr></table>'
        positions = statement_positions(html, END, "equity")
        self.assertEqual(
            [p["value"] for p in positions if p["scope"] == "parent"], [Decimal("80")]
        )


class EquityPrimaryTableTests(unittest.TestCase):
    def test_primary_row_checks_headers_scale_and_the_whole_numeric_row(self):
        """Reproduce ADP's reviewed restated 2017 capital from its independent financial-summary row."""
        from app.data_pipeline.equity_exceptions import verify_original_position

        html = "<table><tr><th>Dollars in millions</th><th>2019</th><th>2018</th><th>2017</th></tr><tr><td>Stockholders equity</td><td>5,399.9</td><td>4,735.9</td><td>4,984.1</td></tr></table>"
        exception = {
            "row_label": "stockholders equity",
            "row_numbers": ["5399.9", "4735.9", "4984.1"],
            "value_column": 2,
            "scale": "1000000",
            "value": "4984100000",
            "header_terms": ["millions", "2019", "2018", "2017"],
        }
        self.assertEqual(
            verify_original_position(html, exception), Decimal("4984100000")
        )
        for changed in (
            html.replace("2017", "2016"),
            html.replace("4,984.1", "4,984.2"),
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    verify_original_position(changed, exception)

    def test_primary_statement_can_use_an_annual_filing_anchor(self):
        """A report position omitted by Companyfacts retains exact instant dates and original filing metadata."""
        from app.equity import select_equity

        annual = reported_fact(accession="report")
        group = balance(tag=GROUP, accession="later", filed=date(2022, 1, 1))
        position = dict(
            group,
            accession="report",
            filed=annual["filed"],
            value=Decimal("80"),
            statement_url="https://www.sec.gov/Archives/report.htm",
            statement_label="Stockholders equity",
            statement_column="FY2020",
        )
        rules = approval(
            [group],
            [(position, 1)],
            "80",
            anchor=fact_identity(annual),
            statement={"url": position["statement_url"]},
        )
        rules[KEY]["components"][0]["statement_position"] = True
        result = select_equity(
            [FilingValue.model_validate(group)],
            START,
            END,
            facts=[annual, group],
            calendar={},
            rules=rules,
        )
        self.assertEqual(result.value, Decimal("80"))
        self.assertEqual(result.selected_sources[0].period_start, END)
        self.assertEqual(result.selected_sources[0].accession, "report")


class EquityInstanceTests(unittest.TestCase):
    def document(
        self,
        context_extra="",
        period=None,
        cik="0000320193",
        unit="iso4217:USD",
        facts=None,
    ):
        """Build a small source instance with explicit issuer, date, unit and ownership context."""
        if period is None:
            period = "<instant>2020-12-31</instant>"
        if facts is None:
            facts = f'<us-gaap:{GROUP} contextRef="c" unitRef="usd">100</us-gaap:{GROUP}><us-gaap:MinorityInterest contextRef="c" unitRef="usd">20</us-gaap:MinorityInterest>'
        return f'<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"><context id="c"><entity><identifier>{cik}</identifier>{context_extra}</entity><period>{period}</period></context><unit id="usd"><measure>{unit}</measure></unit>{facts}</xbrl>'.encode()

    def test_consolidated_instant_supplies_same_filing_group_and_nci(self):
        """Original XBRL dates resolve a renderer without dates; the parent is 100 minus 20."""
        from app.data_pipeline.equity_facts import instant_equity_positions

        positions = instant_equity_positions(self.document(), "0000320193", END)
        group = balance(tag=GROUP)
        _, coefficients, value, _ = select_components(
            positions,
            [group],
            group["source_cik"],
            group["accession"],
            END,
            {},
            group["source_cik"],
        )
        self.assertEqual(value, Decimal("80"))
        self.assertEqual(coefficients, [1, -1])
        self.assertIn("consolidated instant context c", positions[0]["column"])

    def test_account_adoption_and_subsidiary_dimensions_are_excluded(self):
        """A tagged account or adoption adjustment cannot masquerade as whole parent equity."""
        from app.data_pipeline.equity_facts import instant_equity_positions

        for dimension in (
            "StatementEquityComponentsAxis",
            "StatementBusinessSegmentsAxis",
            "AccountingStandardsUpdateAxis",
        ):
            with self.subTest(dimension=dimension):
                segment = f'<segment><xbrldi:explicitMember dimension="us-gaap:{dimension}">us-gaap:SomeMember</xbrldi:explicitMember></segment>'
                self.assertEqual(
                    instant_equity_positions(
                        self.document(context_extra=segment), "0000320193", END
                    ),
                    [],
                )

    def test_foreign_issuer_date_duration_and_currency_are_excluded(self):
        """A numerically plausible position is unusable when issuer, instant or currency differs."""
        from app.data_pipeline.equity_facts import instant_equity_positions

        for changes in (
            {"cik": "0000008670"},
            {"period": "<instant>2019-12-31</instant>"},
            {
                "period": "<startDate>2020-01-01</startDate><endDate>2020-12-31</endDate>"
            },
            {"unit": "iso4217:EUR"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(
                    instant_equity_positions(
                        self.document(**changes), "0000320193", END
                    ),
                    [],
                )

    def test_missing_nci_does_not_become_zero(self):
        """A consolidated group tag without parent or NCI still requires a source decision."""
        from app.data_pipeline.equity_facts import instant_equity_positions

        body = self.document(
            facts=f'<us-gaap:{GROUP} contextRef="c" unitRef="usd">100</us-gaap:{GROUP}>'
        )
        group = balance(tag=GROUP)
        positions = instant_equity_positions(body, "0000320193", END)
        with self.assertRaisesRegex(ValueError, "noncontrolling"):
            select_components(
                positions,
                [group],
                group["source_cik"],
                group["accession"],
                END,
                {},
                group["source_cik"],
            )

    def test_explicit_parent_precedes_rounded_group_minus_nci(self):
        """Comcast's reported parent 85,560 is retained instead of subtracting rounded totals to 85,561."""
        from app.data_pipeline.equity_facts import instant_equity_positions

        facts = f'<us-gaap:StockholdersEquity contextRef="c" unitRef="usd">85560000000</us-gaap:StockholdersEquity><us-gaap:{GROUP} contextRef="c" unitRef="usd">86038000000</us-gaap:{GROUP}><us-gaap:MinorityInterest contextRef="c" unitRef="usd">477000000</us-gaap:MinorityInterest>'
        group = balance(tag=GROUP, value=Decimal("86038000000"))
        parent = balance(value=Decimal("85560000000"))
        positions = instant_equity_positions(
            self.document(facts=facts), "0000320193", END
        )
        _, coefficients, value, _ = select_components(
            positions,
            [group, parent],
            group["source_cik"],
            group["accession"],
            END,
            {},
            group["source_cik"],
        )
        self.assertEqual(value, parent["value"])
        self.assertEqual(coefficients, [1])

    def member_document(self, members):
        """Attach explicit ownership/account contexts to the same consolidated filing."""
        body = self.document(
            facts=f'<us-gaap:{GROUP} contextRef="c" unitRef="usd">100</us-gaap:{GROUP}>'
        ).decode()
        additions = []
        for index, (member, value) in enumerate(members):
            context = f"m{index}"
            additions.append(
                f'<context id="{context}"><entity><identifier>0000320193</identifier><segment><xbrldi:explicitMember dimension="us-gaap:StatementEquityComponentsAxis">us-gaap:{member}</xbrldi:explicitMember></segment></entity><period><instant>2020-12-31</instant></period></context>'
            )
            additions.append(
                f'<us-gaap:{GROUP} contextRef="{context}" unitRef="usd">{value}</us-gaap:{GROUP}>'
            )
        return body.replace("</xbrl>", "".join(additions) + "</xbrl>").encode()

    def test_parent_member_does_not_subtract_temporary_nci(self):
        """An explicit parent 100 remains 100 even when a mixed table also displays redeemable NCI 20."""
        from app.data_pipeline.equity_facts import instant_equity_positions

        body = self.member_document(
            [("ParentMember", "100"), ("NoncontrollingInterestMember", "20")]
        )
        positions = instant_equity_positions(body, "0000320193", END)
        self.assertEqual(
            [p["value"] for p in positions if p["scope"] == "parent"], [Decimal("100")]
        )
        group = balance(tag=GROUP)
        _, _, value, _ = select_components(
            positions,
            [group],
            group["source_cik"],
            group["accession"],
            END,
            {},
            group["source_cik"],
        )
        self.assertEqual(value, Decimal("100"))

    def test_ambiguous_nci_member_requires_scope_or_account_proof(self):
        """A group-tag NCI member needs a permanent-ownership column or a complete account reconciliation."""
        from app.data_pipeline.equity_facts import instant_equity_positions

        body = self.member_document([("NoncontrollingInterestMember", "20")])
        positions = instant_equity_positions(body, "0000320193", END)
        self.assertFalse(any(p["scope"] == "minority" for p in positions))
        controls = [
            {
                "value": "20",
                "column": "Noncontrolling interests",
                "statement": {"sha256": "pinned"},
            }
        ]
        positions = instant_equity_positions(
            body, "0000320193", END, nci_controls=controls
        )
        self.assertEqual(
            [p["value"] for p in positions if p["scope"] == "minority"], [Decimal("20")]
        )
        body = self.member_document(
            [
                ("CommonStockMember", "1"),
                ("RetainedEarningsMember", "79"),
                ("NoncontrollingInterestMember", "20"),
            ]
        )
        positions = instant_equity_positions(body, "0000320193", END)
        self.assertEqual(
            [p["value"] for p in positions if p["scope"] == "minority"], [Decimal("20")]
        )

    def test_unknown_accounts_cannot_prove_ownership(self):
        """A matching numeric total cannot replace understanding an unknown ownership account."""
        from app.data_pipeline.equity_facts import instant_equity_positions

        body = self.member_document(
            [("CommonStockMember", "1"), ("UnknownCapitalMember", "99")]
        )
        positions = instant_equity_positions(body, "0000320193", END)
        self.assertFalse(any(p["scope"] == "parent" for p in positions))

    def test_nci_scope_control_excludes_redeemable_columns(self):
        """A mixed equity table's redeemable column cannot prove permanent NCI scope."""
        from app.data_pipeline.equity import statement_nci_controls

        html = '<table class="report"><tr><th>USD ($)</th><th>Total</th><th>Noncontrolling interests</th><th>Redeemable noncontrolling interests</th></tr>'
        html += f'<tr><td><a onclick="defref_us-gaap_{GROUP}">Balance</a></td><td>100</td><td>20</td><td>30</td></tr></table>'
        controls = statement_nci_controls(html, {"sha256": "pinned"})
        self.assertEqual([c["value"] for c in controls], ["20"])


class EquityReviewedNoteTests(unittest.TestCase):
    def test_note_approval_is_bound_to_snapshot_filing_and_issuer(self):
        """A reviewed nested-note column cannot approve another filing or changed source snapshot."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from app.data_pipeline.equity_exceptions import reviewed_nci_controls

        group = balance(tag=GROUP)
        variants = [FilingValue.model_validate(group)]
        task = {
            "primary": group["source_cik"],
            "period": SimpleNamespace(period_end=END),
            "variants": variants,
        }
        html = "<table><tr><th>Equity in millions</th><th>Noncontrolling interests</th></tr><tr><td>December 31, 2020</td><td>20</td></tr></table>"
        control = {
            "source_cik": group["source_cik"],
            "accession": group["accession"],
            "observed_sha256": variants_fingerprint(variants),
            "evidence": {"sha256": "pinned"},
            "row_label": "december",
            "row_numbers": ["20"],
            "value_column": 0,
            "value": "20000000",
            "scale": "1000000",
            "header_terms": ["millions", "Noncontrolling interests", "2020"],
            "column_label": "Noncontrolling interests",
        }
        document = {"version": 1, "nci_controls": {KEY: control}}
        source = SimpleNamespace(read_text=lambda **kwargs: html)
        with (
            patch(
                "app.data_pipeline.equity_exceptions.read_json", return_value=document
            ),
            patch(
                "app.data_pipeline.equity_exceptions.verify_reference",
                return_value=source,
            ),
        ):
            result = reviewed_nci_controls(
                task, group["source_cik"], group["accession"]
            )
            self.assertEqual(result[0]["value"], "20000000")
            self.assertEqual(
                reviewed_nci_controls(task, group["source_cik"], "different-report"), []
            )
            self.assertEqual(
                reviewed_nci_controls(task, "0000008670", group["accession"]), []
            )
            control["observed_sha256"] = "changed"
            self.assertEqual(
                reviewed_nci_controls(task, group["source_cik"], group["accession"]), []
            )

    def test_boston_scientific_reported_total_is_not_a_sum_of_rounded_accounts(self):
        """The 2020 report states 15,326 million even though displayed parent components sum to 15,327."""
        from app.data_pipeline.equity_exceptions import verify_original_position

        html = "<table><tr><th>Equity in millions</th><th>Total</th><th>Preferred stock</th><th>Common stock</th><th>Treasury stock</th><th>Additional paid-in capital</th><th>Retained earnings</th><th>AOCI</th></tr><tr><td>December 31, 2020</td><td>15,326</td><td>0</td><td>17</td><td>(2,251)</td><td>19,732</td><td>(2,378)</td><td>207</td></tr></table>"
        row = ["15326", "0", "17", "-2251", "19732", "-2378", "207"]
        exception = {
            "row_label": "december",
            "row_numbers": row,
            "value_column": 0,
            "scale": "1000000",
            "value": "15326000000",
            "header_terms": ["millions", "Preferred stock", "2020"],
        }
        self.assertEqual(
            verify_original_position(html, exception), Decimal("15326000000")
        )
        self.assertEqual(sum(Decimal(value) for value in row[1:]), Decimal("15327"))
