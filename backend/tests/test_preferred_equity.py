"""M49: preferred stock read from the equity statement column of an XBRL instance."""

import unittest
from datetime import date
from decimal import Decimal

from app.data_pipeline.preferred_equity import needs_preferred_value, preferred_column

CIK = "0000895421"
PERIOD_END = date(2023, 12, 31)


def context(context_id, members=(), instant="2023-12-31", cik="895421"):
    """One instant context; members are (axis, member) pairs."""
    member_xml = "".join(
        f'<xbrldi:explicitMember dimension="{axis}">{member}</xbrldi:explicitMember>'
        for axis, member in members
    )
    segment = f"<xbrli:segment>{member_xml}</xbrli:segment>" if members else ""
    return (
        f'<xbrli:context id="{context_id}">'
        f'<xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}'
        f"</xbrli:identifier>{segment}</xbrli:entity>"
        f"<xbrli:period><xbrli:instant>{instant}</xbrli:instant></xbrli:period>"
        "</xbrli:context>"
    )


def instance(contexts, facts):
    """A minimal instance with a USD unit; facts are (concept, context, value, unit)."""
    fact_xml = "".join(
        f'<us-gaap:{concept} contextRef="{context_id}" unitRef="{unit}" decimals="-6">'
        f"{value}</us-gaap:{concept}>"
        for concept, context_id, value, unit in facts
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"'
        ' xmlns:xbrldi="http://xbrl.org/2006/xbrldi"'
        ' xmlns:iso4217="http://www.xbrl.org/2003/iso4217"'
        ' xmlns:us-gaap="http://fasb.org/us-gaap/2023"'
        ' xmlns:ms="http://www.morganstanley.com/20231231">'
        '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
        '<xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>'
        + "".join(contexts)
        + fact_xml
        + "</xbrli:xbrl>"
    ).encode("utf-8")


COMPONENTS = "us-gaap:StatementEquityComponentsAxis"
PREFERRED = (COMPONENTS, "us-gaap:PreferredStockMember")
INCLUDING_NCI = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"


class PreferredColumnTests(unittest.TestCase):
    def test_column_balance_is_read(self):
        """MS 2023: the preferred column of the equity statement holds 8,750 million."""
        body = instance(
            [context("c1", [PREFERRED])],
            [(INCLUDING_NCI, "c1", "8750000000", "usd")],
        )

        column = preferred_column(body, CIK, PERIOD_END)

        self.assertEqual(column["value"], Decimal("8750000000"))
        self.assertEqual(column["member"], "PreferredStockMember")

    def test_paid_in_capital_counts_only_in_a_column_that_includes_it(self):
        """PNC 2015–2018 states its preferred column under paid-in capital; the same
        concept in a plain preferred column would be only a part."""
        including = (
            COMPONENTS,
            "us-gaap:PreferredStockIncludingAdditionalPaidInCapitalMember",
        )
        concept = "AdditionalPaidInCapitalPreferredStock"
        whole = instance(
            [context("c1", [including])], [(concept, "c1", "3452000000", "usd")]
        )
        part = instance(
            [context("c1", [PREFERRED])], [(concept, "c1", "3452000000", "usd")]
        )

        self.assertEqual(
            preferred_column(whole, CIK, PERIOD_END)["value"], Decimal("3452000000")
        )
        self.assertEqual(
            preferred_column(part, CIK, PERIOD_END), {"reason": "no preferred column"}
        )

    def test_other_dimensions_dates_entities_and_units_are_ignored(self):
        """SRE shows a subsidiary's preferred column with a legal entity axis, and KKR
        splits the column by series; neither is the company's one column total."""
        legal_entity = ("dei:LegalEntityAxis", "sre:SoCalGasMember")
        series = (
            "us-gaap:StatementClassOfStockAxis",
            "us-gaap:SeriesCPreferredStockMember",
        )
        body = instance(
            [
                context("entity", [PREFERRED, legal_entity]),
                context("series", [PREFERRED, series]),
                context("year_before", [PREFERRED], instant="2022-12-31"),
                context("subsidiary", [PREFERRED], cik="1234567"),
                context("plain", [PREFERRED]),
            ],
            [
                (INCLUDING_NCI, "entity", "22000000", "usd"),
                (INCLUDING_NCI, "series", "1115792000", "usd"),
                (INCLUDING_NCI, "year_before", "1000", "usd"),
                (INCLUDING_NCI, "subsidiary", "2000", "usd"),
                (INCLUDING_NCI, "plain", "3000", "eur"),
            ],
        )

        self.assertEqual(
            preferred_column(body, CIK, PERIOD_END), {"reason": "no preferred column"}
        )

    def test_zero_and_disagreeing_columns_give_no_value(self):
        """AXP reports a zero column (preferred stock sits in paid-in capital); two
        preferred columns with different balances leave the amount open."""
        other = (
            COMPONENTS,
            "us-gaap:PreferredStockIncludingAdditionalPaidInCapitalMember",
        )
        zero = instance(
            [context("c1", [PREFERRED])], [("StockholdersEquity", "c1", "0", "usd")]
        )
        disagreeing = instance(
            [context("c1", [PREFERRED]), context("c2", [other])],
            [(INCLUDING_NCI, "c1", "100", "usd"), (INCLUDING_NCI, "c2", "200", "usd")],
        )

        self.assertEqual(preferred_column(zero, CIK, PERIOD_END), {"reason": "zero"})
        self.assertEqual(
            preferred_column(disagreeing, CIK, PERIOD_END),
            {"reason": "columns disagree"},
        )

    def test_same_balance_under_both_equity_concepts_is_one_value(self):
        """DUK states the column as parent equity in some years and with noncontrolling
        interests in others; one report with both gives one value."""
        body = instance(
            [context("c1", [PREFERRED])],
            [
                ("StockholdersEquity", "c1", "1962000000", "usd"),
                (INCLUDING_NCI, "c1", "1962000000", "usd"),
            ],
        )

        self.assertEqual(
            preferred_column(body, CIK, PERIOD_END)["value"], Decimal("1962000000")
        )


class ScopeTests(unittest.TestCase):
    def test_only_material_dividends_need_a_value(self):
        """Dividends up to 0.2% of equity deduct zero anyway (rule M27); above that, or
        with unknown or negative equity, the value is needed."""
        self.assertFalse(needs_preferred_value(Decimal(1000), Decimal(2)))
        self.assertTrue(needs_preferred_value(Decimal(1000), Decimal("2.01")))
        self.assertTrue(needs_preferred_value(None, Decimal(1)))
        self.assertTrue(needs_preferred_value(Decimal(-3908), Decimal(58)))


if __name__ == "__main__":
    unittest.main()
