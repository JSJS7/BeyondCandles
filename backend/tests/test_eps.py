"""Verify EPS definitions, reported precision, class identity and reproducible source selection."""

import hashlib
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_stocks_api import reported_fact

from app.annual_financials import annual_periods
from app.data_pipeline.eps import (
    eps_position,
    instance_eps,
    report_column_eps,
    source_fact,
)
from app.data_pipeline.sec_statements import matches_rendered_value
from app.eps import BASIC_TAG, COMBINED_TAG, CONTINUING_TAG, DILUTED_TAG, select_eps
from app.fact_identity import fact_identity, variants_fingerprint
from app.schemas.stock import FilingValue

START = date(2020, 1, 1)
END = date(2020, 12, 31)
KEY = "0000320193:2020-01-01:2020-12-31"


def eps_value(**changes):
    fields = dict(
        metric="eps_diluted", tag=DILUTED_TAG, unit="USD/shares", value=Decimal("1.23")
    )
    fields.update(changes)
    return FilingValue.model_validate(reported_fact(**fields))


def rule_for(variants, selected=None, **changes):
    selected = selected or variants[0]
    rule = {
        "status": "ready",
        "observed_sha256": variants_fingerprint(variants),
        "fact": fact_identity(selected),
        "note": "Verified annual net EPS for the selected class.",
        "statement": {"url": "https://www.sec.gov/Archives/reviewed/R4.htm"},
        "label": "Diluted net income per share",
        "column": "12 months ended 2020-12-31",
    }
    rule.update(changes)
    return {KEY: rule}


def html_row(tag, label, value="1.23"):
    return f'<tr><td><a onclick="defref_us-gaap_{tag}">{label}</a></td><td>{value}</td></tr>'


def statement(rows, header="12 Months Ended"):
    return (
        '<table class="report"><tr><th>USD ($) $ in Millions</th>'
        f"<th>{header}</th></tr><tr><th>Dec. 31, 2020</th></tr>" + rows + "</table>"
    )


def original_instance(
    member="us-gaap:CommonClassAMember",
    *,
    cik="0001403161",
    value="9.73",
    unit="usdPerShare",
    extra_dimension="",
    end="2020-12-31",
    default_namespace=False,
):
    dimension = (
        f'<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">{member}</xbrldi:explicitMember>{extra_dimension}</xbrli:segment>'
        if member
        else ""
    )
    default = ' xmlns="http://www.xbrl.org/2003/instance"' if default_namespace else ""
    shares = "shares" if default_namespace else "xbrli:shares"
    return f"""<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"{default}
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:v="https://visa.example/2025">
      <xbrli:context id="c"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier>{dimension}</xbrli:entity>
      <xbrli:period><xbrli:startDate>2020-01-01</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:unit id="usdPerShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>{shares}</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
      <us-gaap:EarningsPerShareDiluted contextRef="c" unitRef="{unit}" decimals="2">{value}</us-gaap:EarningsPerShareDiluted>
      </xbrli:xbrl>""".encode()


class EpsSelectionTests(unittest.TestCase):
    def test_standard_diluted_preserves_negative_zero_and_decimal_values(self):
        """Report the exact per-share decimal, not a value recomputed from parent net income."""
        for amount in ("-2.52", "0", "1.2345"):
            with self.subTest(amount=amount):
                variant = eps_value(value=Decimal(amount))
                result = select_eps([variant], START, END, {})
                self.assertEqual(result.status, "ready")
                self.assertEqual(result.value, Decimal(amount))
                self.assertEqual(result.eps_basis, "diluted")
                self.assertEqual(result.selected_sources[0], variant)

    def test_alternative_tags_require_review_instead_of_becoming_global_aliases(self):
        """Basic, combined and continuing concepts cannot be selected solely because they exist."""
        for tag in (BASIC_TAG, COMBINED_TAG, CONTINUING_TAG):
            with self.subTest(tag=tag):
                variant = eps_value(tag=tag)
                self.assertEqual(
                    select_eps([variant], START, END, {}).status, "needs_review"
                )
                approved = select_eps([variant], START, END, rule_for([variant]))
                self.assertEqual(approved.status, "ready")
                self.assertEqual(approved.selected_tag, tag)
                self.assertEqual(approved.eps_basis, "diluted")
                assert approved.selected_sources[0].statement_url is not None
                self.assertIn("sec.gov", approved.selected_sources[0].statement_url)
                self.assertEqual(approved.selected_sources[0], approved.variants[0])

    def test_changed_filing_or_value_invalidates_the_review(self):
        """A period approval must not silently follow a newer filing or changed numeric source."""
        original = eps_value(tag=COMBINED_TAG)
        for change in (
            {"value": Decimal("9")},
            {"accession": "newer", "filed": date(2022, 2, 1)},
        ):
            with self.subTest(change=change):
                changed = original.model_copy(update=change)
                result = select_eps([changed], START, END, rule_for([original]))
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)

    def test_legacy_unavailable_rule_keeps_basic_in_sources_until_reviewed(self):
        """A legacy unavailable approval stays unavailable until an explicit basis is reviewed."""
        basic = eps_value(tag=BASIC_TAG)
        result = select_eps(
            [basic],
            START,
            END,
            rule_for(
                [basic], status="not_available", note="Only basic EPS is reported."
            ),
        )
        self.assertEqual(result.status, "not_available")
        self.assertIsNone(result.value)
        self.assertEqual(result.variants[0].value, basic.value)
        self.assertEqual(result.variants[0].tag, BASIC_TAG)
        assert result.variants[0].statement_url is not None
        self.assertIn("sec.gov", result.variants[0].statement_url)
        assert result.selection_note is not None
        self.assertIn("Only basic", result.selection_note)

    def test_reviewed_berkshire_basic_is_published_in_the_same_eps_field(self):
        """Show reviewed Berkshire Class B basic EPS, preserving its basis instead of calling it diluted."""
        basic = eps_value(
            source_cik="0001067983", tag=BASIC_TAG, value=Decimal("41.27")
        )
        rule = next(iter(rule_for([basic], eps_basis="basic").values()))
        rules = {"0001067983:2020-01-01:2020-12-31": rule}
        result = select_eps([basic], START, END, rules)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.value, Decimal("41.27"))
        self.assertEqual(result.eps_basis, "basic")
        self.assertEqual(result.selected_tag, BASIC_TAG)
        self.assertEqual(result.selected_sources, result.variants)

    def test_basic_fallback_does_not_apply_to_other_issuers_or_hide_diluted_values(
        self,
    ):
        """The approved fallback is restricted to Berkshire and must never suppress a diluted candidate."""
        for variants in (
            [eps_value(tag=BASIC_TAG)],
            [
                eps_value(source_cik="0001067983", tag=BASIC_TAG),
                eps_value(source_cik="0001067983"),
            ],
        ):
            with self.subTest(variants=variants):
                issuer = variants[0].source_cik
                rule = next(iter(rule_for(variants, eps_basis="basic").values()))
                result = select_eps(
                    variants, START, END, {f"{issuer}:{START}:{END}": rule}
                )
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)

    def test_wrong_units_issuers_and_same_day_conflicts_are_rejected(self):
        """Neither currency totals, overlapping issuers nor conflicting duplicates are valid EPS."""
        cases = (
            [eps_value(unit="USD")],
            [eps_value(), eps_value(source_cik="0000000001")],
            [eps_value(), eps_value(accession="conflict", value=Decimal("4"))],
        )
        for variants in cases:
            with self.subTest(variants=variants):
                self.assertEqual(
                    select_eps(variants, START, END, {}).status, "needs_review"
                )
        self.assertEqual(select_eps([], START, END, {}).status, "not_available")

    def test_annual_grouping_keeps_latest_full_year_revision_and_excludes_ytd(self):
        """A 10-Q full-year EPS correction is eligible; nine-month EPS never becomes annual."""
        original = reported_fact(
            metric="eps_diluted", tag=DILUTED_TAG, unit="USD/shares", value=Decimal("1")
        )
        revised = dict(
            original,
            value=Decimal("2"),
            filed=date(2022, 2, 1),
            accession="revised",
            form="10-Q",
        )
        ytd = dict(
            revised,
            value=Decimal("99"),
            period_kind="nine_months_ytd",
            period_start=date(2020, 4, 1),
        )
        result = annual_periods([original, revised, ytd])[0].metrics["eps_diluted"]
        self.assertEqual(result.value, Decimal("2"))
        self.assertEqual(result.selected_sources[0].accession, "revised")


class EpsStatementTests(unittest.TestCase):
    def test_combined_eps_is_not_multiplied_by_the_statement_monetary_scale(self):
        """A loss of $2.52 per share stays -2.52 even when the statement amounts are in millions."""
        tag, parsed, status, note = eps_position(
            statement(
                html_row(
                    COMBINED_TAG, "Net loss per share, basic and diluted", "(2.52)"
                )
            ),
            "0001373715",
            END,
        )
        self.assertEqual(tag, COMBINED_TAG)
        self.assertEqual(parsed["scale"], 1)
        self.assertTrue(matches_rendered_value(parsed, Decimal("-2.52"), END))
        self.assertEqual(status, "ready")
        self.assertIn("both basic and diluted", note)

    def test_statement_selects_visa_class_a_instead_of_a_larger_class_c_eps(self):
        """Match the selected share class, not whichever EPS is numerically largest."""
        rows = html_row(
            "StatementClassOfStockAxis", "Class A common stock", ""
        ) + html_row(DILUTED_TAG, "Diluted", "9.73")
        rows += html_row(
            "StatementClassOfStockAxis", "Class C common stock", ""
        ) + html_row(DILUTED_TAG, "Diluted", "38.92")
        _, parsed, _, _ = eps_position(statement(rows), "0001403161", END)
        self.assertTrue(matches_rendered_value(parsed, Decimal("9.73"), END))
        self.assertFalse(matches_rendered_value(parsed, Decimal("38.92"), END))

    def test_fcx_2022_continuing_tag_needs_the_exact_reviewed_net_income_heading(self):
        """FCX's full net-income EPS section uses a continuing-tag label; the exception is period-specific."""
        html = statement(
            html_row("ProfitLoss", "Net income", "4479")
            + html_row(
                "EarningsPerShareAbstract",
                "Net income per share attributable to common stockholders:",
                "",
            )
            + html_row(
                CONTINUING_TAG,
                "Income (Loss) from Continuing Operations, Per Diluted Share",
                "2.39",
            )
        ).replace("Dec. 31, 2020", "Dec. 31, 2022")
        _, parsed, status, _ = eps_position(html, "0000831259", date(2022, 12, 31))
        self.assertEqual(status, "ready")
        self.assertTrue(
            matches_rendered_value(parsed, Decimal("2.39"), date(2022, 12, 31))
        )
        with self.assertRaises(ValueError):
            eps_position(
                html.replace("Dec. 31, 2022", "Dec. 31, 2021"),
                "0000831259",
                date(2021, 12, 31),
            )

    def test_reviewed_continuing_row_is_net_eps_only_without_discontinued_amounts(self):
        """AT&T 2016 tags both net income and its only EPS row as continuing operations.

        The reviewed period accepts that row as net EPS; another year, or the same year
        with a discontinued amount on the statement, still needs review.
        """
        end = date(2016, 12, 31)
        rows = html_row(
            "IncomeLossFromContinuingOperations",
            "Net Income Attributable to AT&T",
            "12976",
        ) + html_row(
            CONTINUING_TAG, "Diluted Earnings Per Share Attributable to AT&T", "2.10"
        )
        html = statement(rows).replace("Dec. 31, 2020", "Dec. 31, 2016")

        tag, parsed, status, _ = eps_position(html, "0000732717", end)

        self.assertEqual((tag, status), (CONTINUING_TAG, "ready"))
        self.assertTrue(matches_rendered_value(parsed, Decimal("2.10"), end))
        with_discontinued = html.replace(
            "</table>",
            html_row(
                "IncomeLossFromDiscontinuedOperationsNetOfTax",
                "Income from discontinued operations",
                "45",
            )
            + "</table>",
        )
        for other_html, other_end in (
            (html.replace("Dec. 31, 2016", "Dec. 31, 2017"), date(2017, 12, 31)),
            (with_discontinued, end),
        ):
            with self.subTest(end=other_end, html=other_html):
                with self.assertRaises(ValueError):
                    eps_position(other_html, "0000732717", other_end)

    def test_berkshire_class_b_is_reviewed_as_basic_without_diluted_equivalence(self):
        """The one-column fallback retains basic Class B earnings rather than inventing a diluted value."""
        rows = html_row("StatementClassOfStockAxis", "Equivalent Class A [Member]", "")
        rows += html_row(BASIC_TAG, "Net earnings per average equivalent", "61900")
        rows += html_row("StatementClassOfStockAxis", "Equivalent Class B [Member]", "")
        rows += html_row(BASIC_TAG, "Net earnings per average equivalent", "41.27")
        tag, parsed, status, note = eps_position(statement(rows), "0001067983", END)
        self.assertEqual(
            (tag, status, parsed["eps_basis"]), (BASIC_TAG, "ready", "basic")
        )
        self.assertTrue(matches_rendered_value(parsed, Decimal("41.27"), END))
        self.assertIn("not a claim of diluted equivalence", note)

    def test_quarterly_and_unlabelled_basic_eps_cannot_fill_diluted_eps(self):
        """Only annual diluted/explicitly combined rows satisfy the target definition."""
        for html in (
            statement(html_row(DILUTED_TAG, "Diluted"), "3 Months Ended"),
            statement(html_row(BASIC_TAG, "Basic")),
        ):
            with self.subTest(html=html):
                with self.assertRaisesRegex(ValueError, "No unique"):
                    eps_position(html, "0000320193", END)

    def test_continuing_subtotal_is_rejected_but_reviewed_net_row_alias_is_supported(
        self,
    ):
        """Do not reinterpret genuine continuing-only EPS or apply an issuer alias globally."""
        rows = html_row("NetIncomeLoss", "Net income", "123") + html_row(
            CONTINUING_TAG, "Diluted", "1.23"
        )
        tag, _, status, _ = eps_position(statement(rows), "0000865752", END)
        self.assertEqual((tag, status), (CONTINUING_TAG, "ready"))
        for html, cik in (
            (statement(rows), "0000320193"),
            (
                statement(
                    rows.replace(">Diluted<", ">Continuing operations, diluted<")
                ),
                "0000865752",
            ),
            (
                statement(
                    rows
                    + html_row(
                        "IncomeLossFromDiscontinuedOperationsNetOfTaxPerDilutedShare",
                        "Discontinued EPS",
                        "0.1",
                    )
                ),
                "0000865752",
            ),
        ):
            with self.subTest(cik=cik, html=html):
                with self.assertRaises(ValueError):
                    eps_position(html, cik, END)


class EpsOriginalSourceTests(unittest.TestCase):
    metadata = {
        "accession": "original",
        "filed": date(2021, 2, 1),
        "form": "10-K",
        "fiscal_period": "FY",
    }

    def test_axon_revised_eps_requires_exact_reviewed_context(self):
        """Read the revised annual EPS, rejecting old/adjustment columns and unrelated product data."""
        body = original_instance(
            member="us-gaap:ProductMember",
            cik="0001069183",
            value="-0.85",
            end="2021-12-31",
        )
        body = body.replace(
            b"xmlns:v=", b'xmlns:srt="http://fasb.org/srt/2024" xmlns:v='
        )
        body = body.replace(
            b"us-gaap:StatementClassOfStockAxis", b"srt:ProductOrServiceAxis"
        )
        body = body.replace(b'id="c"', b'id="c-570"').replace(
            b'contextRef="c"', b'contextRef="c-570"'
        )
        body = body.replace(b"2020-01-01", b"2021-01-01")
        metadata = {
            "accession": "0001069183-24-000061",
            "filed": date(2024, 11, 8),
            "form": "10-Q",
            "fiscal_period": "Q3",
        }
        self.assertEqual(instance_eps(body, "0001069183", metadata), [])
        revised = instance_eps(body, "0001069183", metadata, reviewed_revision=True)
        self.assertEqual(len(revised), 1)
        self.assertEqual(revised[0]["value"], Decimal("-0.85"))
        self.assertEqual(revised[0]["period_end"], date(2021, 12, 31))
        self.assertEqual(revised[0]["form"], "10-Q")
        for altered in (
            body.replace(b"c-570", b"c-568"),
            body.replace(b"us-gaap:ProductMember", b"us-gaap:ServiceMember"),
            body.replace(b"2021-12-31", b"2022-12-31"),
            body.replace(b"2021-01-01", b"2021-04-01"),
            body.replace(
                b"http://fasb.org/srt/2024", b"http://unrelated.example/srt/2024"
            ),
        ):
            with self.subTest(body=altered):
                self.assertEqual(
                    instance_eps(
                        altered, "0001069183", metadata, reviewed_revision=True
                    ),
                    [],
                )
        with self.subTest(accession="unreviewed"):
            self.assertEqual(
                instance_eps(
                    body,
                    "0001069183",
                    dict(metadata, accession="another"),
                    reviewed_revision=True,
                ),
                [],
            )

    def test_class_a_usd_per_share_supports_prefixed_and_default_unit_namespaces(self):
        """Read SEC's USD/share divide unit with either valid QName spelling of shares."""
        for default_namespace in (False, True):
            with self.subTest(default_namespace=default_namespace):
                facts = instance_eps(
                    original_instance(default_namespace=default_namespace),
                    "0001403161",
                    self.metadata,
                )
                self.assertEqual(len(facts), 1)
                self.assertEqual(facts[0]["value"], Decimal("9.73"))
                self.assertEqual(facts[0]["unit"], "USD/shares")

    def test_default_namespace_redeclared_on_a_dimension_member_keeps_the_unit(self):
        """A later default-namespace declaration must not change the root-scoped unit.

        RR Donnelley instances (e.g. TSLA's 10-K for 2015) declare
        xmlns="http://xbrl.org/2006/xbrldi" on every explicitMember. The unprefixed
        "shares" of a unit declared at the root still means xbrli:shares, so the
        company-wide EPS fact must be read.
        """
        other_context = (
            '<context id="segment"><entity><identifier scheme="http://www.sec.gov/CIK">'
            "0001318605</identifier><segment>"
            '<explicitMember xmlns="http://xbrl.org/2006/xbrldi" '
            'dimension="us-gaap:StatementBusinessSegmentsAxis">v:SegmentMember'
            "</explicitMember></segment></entity>"
            "<period><instant>2020-12-31</instant></period></context>"
        )
        body = original_instance(member="", cik="0001318605", default_namespace=True)
        body = body.replace(b"</xbrli:xbrl>", other_context.encode() + b"</xbrli:xbrl>")

        facts = instance_eps(body, "0001318605", self.metadata)

        self.assertEqual([fact["value"] for fact in facts], [Decimal("9.73")])
        self.assertEqual(facts[0]["unit"], "USD/shares")

    def test_wrong_class_subsidiary_segment_unit_and_quarter_are_excluded(self):
        """Reject sources that could otherwise attach a different security's earnings to Visa."""
        extra = '<xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">v:SegmentMember</xbrldi:explicitMember>'
        examples = (
            original_instance(member="us-gaap:CommonClassCMember"),
            original_instance(member=""),
            original_instance(cik="0000000001"),
            original_instance(extra_dimension=extra),
            original_instance(unit="USD"),
            original_instance(end="2020-03-31"),
        )
        for body in examples:
            with self.subTest(body=body):
                self.assertEqual(instance_eps(body, "0001403161", self.metadata), [])

    def test_nonfinite_and_conflicting_original_values_stop_preparation(self):
        """Bad decimals and two different values for one approved context must not reach the importer."""
        with self.assertRaisesRegex(ValueError, "numeric range"):
            instance_eps(original_instance(value="NaN"), "0001403161", self.metadata)
        body = original_instance()
        duplicate = '<us-gaap:EarningsPerShareDiluted contextRef="c" unitRef="usdPerShare">99</us-gaap:EarningsPerShareDiluted>'
        body = body.replace(b"</xbrli:xbrl>", duplicate.encode() + b"</xbrli:xbrl>")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            instance_eps(body, "0001403161", self.metadata)

    def test_supplement_reparses_exact_source_and_rejects_changed_bytes(self):
        """Pinned SHA-256 and exact fact comparison protect repeat imports from changed source files."""
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.xml"
            source.write_bytes(original_instance())
            reference = {
                "file": source.name,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
            fact = instance_eps(source.read_bytes(), "0001403161", self.metadata)[0]
            record = {
                "source_kind": "instance",
                "source": reference,
                "fact": {
                    k: str(v) if isinstance(v, (date, Decimal)) else v
                    for k, v in fact.items()
                },
            }
            with patch("app.data_pipeline.sec_catalogue.CACHE", Path(directory)):
                self.assertEqual(source_fact(record), fact)
                record["fact"]["value"] = "88"
                with self.assertRaisesRegex(ValueError, "changed"):
                    source_fact(record)
                source.write_bytes(b"corrupt")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    source_fact(record)


if __name__ == "__main__":
    unittest.main()


COMBINED_CIK = "0000000007"


def companyfacts_eps(*rows):
    """Companyfacts with EarningsPerShareBasicAndDiluted in the given filings."""
    items = [
        {
            "start": start,
            "end": end,
            "val": value,
            "accn": accession,
            "form": form,
            "filed": filed,
            "fp": "FY",
        }
        for start, end, value, accession, form, filed in rows
    ]
    return {
        "cik": int(COMBINED_CIK),
        "facts": {"us-gaap": {COMBINED_TAG: {"units": {"USD/shares": items}}}},
    }


def index_record(kind, tag=COMBINED_TAG, accession="0000000007-24-000001", form="10-K"):
    return {
        "source_kind": kind,
        "source": {"url": "u", "sha256": "instance", "file": "f"},
        "fact": {
            "source_cik": COMBINED_CIK,
            "tag": tag,
            "accession": accession,
            "filed": "2024-02-15",
            "form": form,
            "fiscal_period": "FY",
        },
    }


class ReportColumnEpsTests(unittest.TestCase):
    def test_every_annual_report_of_a_reviewed_companyfacts_tag_is_kept(self):
        """N117: MNST's EPS cells each keep one fact, from the latest report stating
        the year, so no stored report has two years. Every 10-K's columns of the
        reviewed tag are kept for growth; a 10-Q is not an annual report."""
        body = companyfacts_eps(
            ("2022-01-01", "2022-12-31", "1.0", "a-23", "10-K", "2023-02-15"),
            ("2021-01-01", "2021-12-31", "0.8", "a-23", "10-K", "2023-02-15"),
            ("2023-01-01", "2023-12-31", "1.2", "a-24", "10-K", "2024-02-15"),
            ("2022-01-01", "2022-12-31", "1.0", "a-24", "10-K", "2024-02-15"),
            ("2023-01-01", "2023-12-31", "1.1", "q-24", "10-Q", "2024-05-01"),
        )

        facts = report_column_eps(
            [(COMBINED_CIK, body)], [index_record("companyfacts")], {}
        )

        kept = {(fact["accession"], str(fact["period_end"])) for fact in facts}
        self.assertEqual(
            kept,
            {
                ("a-23", "2021-12-31"),
                ("a-23", "2022-12-31"),
                ("a-24", "2022-12-31"),
                ("a-24", "2023-12-31"),
            },
        )
        self.assertEqual({fact["metric"] for fact in facts}, {"eps_report_column"})

    def test_pinned_instance_gives_its_columns_under_the_reviewed_tag(self):
        """Visa's class A EPS is read from each report's instance. Both years of a
        pinned instance are kept, other EPS tags of it are not, and an instance pinned
        only for a 10-Q (AXON's revision) is not read."""
        instance_facts = [
            {"tag": DILUTED_TAG, "period_end": date(2023, 12, 31), "value": Decimal(5)},
            {"tag": DILUTED_TAG, "period_end": date(2022, 12, 31), "value": Decimal(4)},
            {"tag": BASIC_TAG, "period_end": date(2023, 12, 31), "value": Decimal(6)},
        ]
        read = []

        def parse(body, cik, metadata):
            read.append(metadata["form"])
            return [
                dict(
                    fact,
                    source_cik=cik,
                    accession=metadata["accession"],
                    period_start=date(fact["period_end"].year, 1, 1),
                )
                for fact in instance_facts
            ]

        records = [
            index_record("instance", DILUTED_TAG),
            index_record("instance", DILUTED_TAG, "q", form="10-Q"),
        ]
        with (
            patch("app.data_pipeline.eps.verify_reference") as reference,
            patch("app.data_pipeline.eps.instance_eps", side_effect=parse),
        ):
            reference.return_value.read_bytes.return_value = b""
            facts = report_column_eps([(COMBINED_CIK, {})], records, {})

        self.assertEqual(read, ["10-K"])
        self.assertEqual(
            sorted((fact["tag"], fact["value"]) for fact in facts),
            [(DILUTED_TAG, Decimal(4)), (DILUTED_TAG, Decimal(5))],
        )

    def test_two_values_for_one_column_stop_the_import(self):
        """One report states one EPS per year; two values mean a wrong source."""
        body = companyfacts_eps(
            ("2023-01-01", "2023-12-31", "1.2", "same", "10-K", "2024-02-15"),
        )
        conflicting = {
            "tag": COMBINED_TAG,
            "source_cik": COMBINED_CIK,
            "accession": "same",
            "period_start": date(2023, 1, 1),
            "period_end": date(2023, 12, 31),
            "value": Decimal("1.3"),
        }
        records = [
            index_record("companyfacts"),
            index_record("instance", accession="same"),
        ]
        with (
            patch("app.data_pipeline.eps.verify_reference") as reference,
            patch("app.data_pipeline.eps.instance_eps", return_value=[conflicting]),
        ):
            reference.return_value.read_bytes.return_value = b""
            with self.assertRaisesRegex(ValueError, "Conflicting EPS"):
                report_column_eps([(COMBINED_CIK, body)], records, {})
