"""Check that vendor prices are converted to the share basis of normalized EPS."""

import unittest
from datetime import date
from decimal import Decimal

from app.eps_splits import split_rules
from app.price_share_basis import share_basis_price

CONVERSION = "vendor_corporate_action_conversion_not_an_ordinary_share_split"


def rules(actions=(), excluded=()):
    return {"actions": list(actions), "excluded_vendor_events": list(excluded)}


def action(day, new, old, vendor_new, vendor_old, ticker="HON"):
    return {
        "ticker": ticker,
        "date": day,
        "new_shares": new,
        "old_shares": old,
        "vendor_new_shares": vendor_new,
        "vendor_old_shares": vendor_old,
    }


def excluded(day, vendor_new, vendor_old, reason=CONVERSION, ticker="HON"):
    return {
        "ticker": ticker,
        "date": day,
        "vendor_new_shares": vendor_new,
        "vendor_old_shares": vendor_old,
        "reason": reason,
    }


class ShareBasisArithmeticTests(unittest.TestCase):
    def test_spin_off_part_of_a_composite_vendor_ratio_is_restored(self):
        """A 1:2 reverse split recorded by the vendor as 1907:2000 doubles EPS, so the
        price must reach the same basis: vendor price × (1907/2000) / (1/2)."""
        policy = rules(actions=[action("2026-06-29", 1, 2, 1907, 2000)])

        converted = share_basis_price("HON", date(2024, 12, 20), Decimal("100"), policy)

        self.assertEqual(converted, Decimal("190.7000000000"))

    def test_conversions_multiply_and_only_later_events_apply(self):
        """Each conversion after the price date restores its vendor factor; an event on
        the price date is already in that price and is not applied again."""
        policy = rules(
            excluded=[
                excluded("2018-10-01", 1011, 1000),
                excluded("2025-10-30", 1061, 1000),
            ]
        )

        before_both = share_basis_price(
            "HON", date(2018, 9, 28), Decimal("100"), policy
        )
        on_first = share_basis_price("HON", date(2018, 10, 1), Decimal("100"), policy)
        after_both = share_basis_price(
            "HON", date(2025, 10, 30), Decimal("100"), policy
        )

        self.assertEqual(before_both, Decimal("107.2671000000"))
        self.assertEqual(on_first, Decimal("106.1000000000"))
        self.assertEqual(after_both, Decimal("100"))

    def test_ordinary_split_already_matches_the_vendor(self):
        """A plain 10:1 split divides both the vendor price and EPS, so nothing changes."""
        policy = rules(actions=[action("2024-06-10", 10, 1, 10, 1, ticker="NVDA")])

        converted = share_basis_price(
            "NVDA", date(2024, 1, 2), Decimal("48.168"), policy
        )

        self.assertEqual(converted, Decimal("48.168"))

    def test_event_reviewed_only_for_old_eps_sources_is_rejected(self):
        """Exclusions justified by preceding all EPS filings say nothing about prices,
        so a later price must not silently ignore them."""
        policy = rules(
            excluded=[
                excluded("2014-06-09", 7, 1, reason="precedes_all_selected_filings")
            ]
        )

        with self.assertRaisesRegex(ValueError, "not reviewed for prices"):
            share_basis_price("HON", date(2014, 6, 6), Decimal("100"), policy)


class FrozenSnapshotReferenceTests(unittest.TestCase):
    """Compare converted snapshot prices with prices stated in independent documents."""

    def test_danaher_matches_the_price_in_its_fortive_tax_letter(self):
        """Danaher's Form 8937 for the Fortive distribution states $78.94 as the average
        of the NYSE high and low on 5 July 2016. The vendor scaled this day by the later
        Veralto spin-off. The 0.1% tolerance covers the rounded vendor ratio (1128/1000)
        and NYSE-only versus consolidated high and low quotes."""
        vendor_high = Decimal("71.2765960693")
        vendor_low = Decimal("68.5638275146")
        vendor_average = (vendor_high + vendor_low) / 2

        converted = share_basis_price("DHR", date(2016, 7, 5), vendor_average)

        self.assertAlmostEqual(converted / Decimal("78.94"), 1, delta=Decimal("0.001"))
        # Without the conversion the vendor price is about 11% too low.
        self.assertLess(vendor_average / Decimal("78.94"), Decimal("0.9"))

    def test_company_without_vendor_events_keeps_its_reported_close(self):
        """ExxonMobil's 2024 10-K cover states a $115.12 close on 28 June 2024, the same
        as the vendor close; no conversion is applied."""
        vendor_close = Decimal("115.1200027466")

        converted = share_basis_price("XOM", date(2024, 6, 28), vendor_close)

        self.assertEqual(converted, vendor_close)
        self.assertEqual(round(converted, 2), Decimal("115.12"))

    def test_jci_prices_before_the_tyco_merger_are_not_comparable(self):
        """Before the merger the vendor quotes Tyco, while financials follow Johnson
        Controls Inc., so no price comparable with JCI per-share values exists."""
        self.assertIsNone(share_basis_price("JCI", date(2016, 9, 2), Decimal("47.74")))
        self.assertEqual(
            share_basis_price("JCI", date(2016, 9, 6), Decimal("48.90")),
            Decimal("48.90"),
        )

    def test_every_vendor_event_in_the_price_window_is_reviewed_for_prices(self):
        """All 200 companies convert from the first session without an unreviewed event."""
        policy = split_rules()
        tickers = {item["ticker"] for item in policy["actions"]}
        tickers |= {item["ticker"] for item in policy["excluded_vendor_events"]}

        for ticker in sorted(tickers):
            with self.subTest(ticker=ticker):
                share_basis_price(ticker, date(2015, 1, 2), Decimal("1"), policy)


if __name__ == "__main__":
    unittest.main()
