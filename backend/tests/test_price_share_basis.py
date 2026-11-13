"""Check that vendor prices are converted to the share basis of normalized EPS."""

import unittest
from datetime import date
from decimal import Decimal
from fractions import Fraction

from app.eps_splits import split_rules
from app.price_share_basis import (
    share_basis_price,
    spin_offs,
    spin_offs_after_period,
)

CONVERSION = "vendor_corporate_action_conversion_not_an_ordinary_share_split"


def rules(actions=(), excluded=()):
    return {"actions": list(actions), "excluded_vendor_events": list(excluded)}


def action(day, new, old, vendor_new, vendor_old, ticker="HON", unadjusted_before=None):
    result = {
        "ticker": ticker,
        "date": day,
        "new_shares": new,
        "old_shares": old,
        "vendor_new_shares": vendor_new,
        "vendor_old_shares": vendor_old,
    }
    if unadjusted_before is not None:
        result["vendor_prices_unadjusted_before"] = unadjusted_before
    return result


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

    def test_split_on_the_price_day_is_already_in_that_price(self):
        """K11: the close of a split's first trading day is quoted on the new basis, so
        the split converts only earlier prices; the day before still needs it."""
        policy = rules(actions=[action("2026-06-29", 1, 2, 1907, 2000)])

        day_before = share_basis_price("HON", date(2026, 6, 26), Decimal("100"), policy)
        split_day = share_basis_price("HON", date(2026, 6, 29), Decimal("100"), policy)

        self.assertEqual(day_before, Decimal("190.7000000000"))
        self.assertEqual(split_day, Decimal("100"))

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

    def test_split_missing_from_vendor_history_applies_before_the_vendor_step(self):
        """The vendor applied a 2:1 split only from 6 August, three sessions before the
        first split-adjusted session (11 August). Its earlier prices keep the old share
        count and are halved; from the step on they already match EPS."""
        policy = rules(
            actions=[
                action(
                    "2026-08-11",
                    2,
                    1,
                    2,
                    1,
                    ticker="MNST",
                    unadjusted_before="2026-08-06",
                )
            ]
        )

        before_step = share_basis_price(
            "MNST", date(2026, 8, 5), Decimal("94.46"), policy
        )
        on_step = share_basis_price("MNST", date(2026, 8, 6), Decimal("47.08"), policy)
        on_split = share_basis_price(
            "MNST", date(2026, 8, 11), Decimal("45.53"), policy
        )

        self.assertEqual(before_step, Decimal("47.2300000000"))
        self.assertEqual(on_step, Decimal("47.08"))
        self.assertEqual(on_split, Decimal("45.53"))

    def test_share_exchange_without_vendor_event_converts_earlier_prices(self):
        """N120: Charter's old shares became 0.9042 new shares on 18 May 2016 and the
        vendor recorded nothing, so its earlier prices are of the old shares. They are
        divided by 0.9042 to match EPS restated in new shares; later prices stay."""
        exchange = dict(
            action("2016-05-18", 9042, 10000, 1, 1, ticker="CHTR"), vendor_event=False
        )
        policy = rules(actions=[exchange])

        before = share_basis_price("CHTR", date(2016, 5, 17), Decimal("203.36"), policy)
        on_day = share_basis_price("CHTR", date(2016, 5, 18), Decimal("227.41"), policy)

        self.assertEqual(before, Decimal("224.9059942491"))
        self.assertEqual(on_day, Decimal("227.41"))

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


class SpinOffTests(unittest.TestCase):
    def test_separated_fraction_comes_from_the_vendor_and_share_ratios(self):
        """A conversion of 1589:1000 means the separated business held 589/1589 of the
        price. In a composite 1907:2000 ratio with a 1:2 reverse split the price factor is
        1.907, so the business held 907/1907."""
        policy = rules(
            actions=[action("2026-06-29", 1, 2, 1907, 2000)],
            excluded=[excluded("2020-04-03", 1589, 1000)],
        )

        events = spin_offs("HON", policy)

        self.assertEqual(
            events,
            [
                (date(2020, 4, 3), Fraction(589, 1589)),
                (date(2026, 6, 29), Fraction(907, 1907)),
            ],
        )

    def test_splits_small_events_and_price_raising_events_are_not_spin_offs(self):
        """A plain split moves price and EPS alike; 1.1% is below the 3% threshold; a
        ratio below 1 raised earlier prices, as in the JCI merger, so nothing separated."""
        policy = rules(
            actions=[action("2024-06-10", 10, 1, 10, 1)],
            excluded=[
                excluded("2018-10-01", 1011, 1000),
                excluded("2016-09-06", 955, 1000),
            ],
        )

        self.assertEqual(spin_offs("HON", policy), [])

    def test_share_exchange_without_vendor_event_is_not_a_spin_off(self):
        """N120: no vendor ratio means no business left the price; the 9.6% gap between
        the share ratio and the vendor's none must not read as a separation."""
        exchange = dict(
            action("2016-05-18", 9042, 10000, 1, 1, ticker="CHTR"), vendor_event=False
        )

        self.assertEqual(spin_offs("CHTR", rules(actions=[exchange]), {}), [])

    def test_only_events_after_the_period_and_by_the_price_day_count(self):
        """The price on the event day already excludes the business; an event on the
        period end or after the price day does not mix scopes."""
        policy = rules(
            excluded=[
                excluded("2019-12-31", 1100, 1000),
                excluded("2020-04-03", 1589, 1000),
                excluded("2020-06-30", 1100, 1000),
            ]
        )

        events = spin_offs_after_period(
            "HON", date(2019, 12, 31), date(2020, 4, 3), policy
        )

        self.assertEqual(events, [(date(2020, 4, 3), Fraction(589, 1589))])

    def test_spin_off_recorded_as_a_distribution_is_marked_too(self):
        """M50: the vendor booked Adient as a distribution, so no split-like ratio shows
        it. The rise of Adjusted Close / Close on 31.10.2016 gives 10.7% of the price,
        the vendor's 4.70 per JCI share against the 43.76 close before."""
        events = spin_offs("JCI", rules())

        self.assertEqual([event_day for event_day, _ in events], [date(2016, 10, 31)])
        self.assertAlmostEqual(float(events[0][1]), 0.1074, places=4)

    def test_distribution_below_the_threshold_is_not_a_spin_off(self):
        """The same 3% threshold as for vendor ratios applies."""
        small = {"TEST": [(date(2020, 1, 2), Fraction(2, 100))]}

        self.assertEqual(spin_offs("TEST", rules(), small), [])

    def test_snapshot_spin_offs_in_the_price_window_are_the_known_separations(self):
        """Every event above 3% from 2015 to 2025 is a documented separation, named after
        the business that left; the JCI merger and HON's 1.1% Garrett spin-off are not."""
        known_separations = {
            ("APD", date(2016, 10, 3)),  # Versum Materials
            ("DHR", date(2016, 7, 5)),  # Fortive
            ("DHR", date(2023, 10, 2)),  # Veralto
            ("GE", date(2019, 2, 26)),  # transportation business, merged into Wabtec
            ("GE", date(2023, 1, 4)),  # GE HealthCare
            ("GE", date(2024, 4, 2)),  # GE Vernova
            ("HLT", date(2017, 1, 4)),  # Park Hotels & Resorts, Hilton Grand Vacations
            ("HON", date(2018, 10, 29)),  # Resideo
            ("HON", date(2025, 10, 30)),  # Solstice Advanced Materials
            ("IBM", date(2021, 11, 4)),  # Kyndryl
            ("JCI", date(2016, 10, 31)),  # Adient, recorded as a distribution (M50)
            ("MET", date(2017, 8, 7)),  # Brighthouse Financial
            ("MMM", date(2024, 4, 1)),  # Solventum
            ("MRK", date(2021, 6, 3)),  # Organon
            ("O", date(2021, 11, 15)),  # Orion Office REIT
            ("PFE", date(2020, 11, 17)),  # Upjohn, merged into Viatris
            ("RTX", date(2020, 4, 3)),  # Carrier, Otis
            ("T", date(2022, 4, 11)),  # WarnerMedia
            ("TT", date(2020, 3, 2)),  # industrial business, merged into Gardner Denver
        }
        policy = split_rules()
        tickers = {item["ticker"] for item in policy["actions"]}
        tickers |= {item["ticker"] for item in policy["excluded_vendor_events"]}

        found = set()
        for ticker in tickers:
            for event_day, _ in spin_offs(ticker, policy):
                if date(2015, 1, 2) <= event_day <= date(2025, 12, 31):
                    found.add((ticker, event_day))

        self.assertEqual(found, known_separations)


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
        assert converted is not None

        self.assertAlmostEqual(converted / Decimal("78.94"), 1, delta=Decimal("0.001"))
        # Without the conversion the vendor price is about 11% too low.
        self.assertLess(vendor_average / Decimal("78.94"), Decimal("0.9"))

    def test_events_after_the_price_window_match_closes_stated_in_sec_filings(self):
        """Each issuer has vendor events in 2025-2026, after the price window, so the data
        itself cannot show them. Each stated close comes from a proxy statement or merger
        prospectus. HON's EPS basis also reflects its 2026 1-for-2 share
        change, so its converted price is twice the historical quote."""
        references = [
            # ticker, day, vendor close, stated close, share-count factor after the day
            ("BDX", date(2017, 4, 21), "142.1153564453", "185.29", 1),  # S-4 Bard
            ("CMCSA", date(2023, 12, 29), "41.0965309143", "43.85", 1),  # 2024 proxy
            ("FDX", date(2019, 5, 31), "124.3190994263", "154.28", 1),  # 2019 proxy
            ("SPGI", date(2020, 11, 27), "323.1504211426", "341.57", 1),  # S-4 IHS
            ("HON", date(2023, 12, 29), "207.2922515869", "209.71", 2),  # 2024 proxy
        ]
        policy = split_rules()

        for ticker, day, vendor_close, stated_close, share_factor in references:
            with self.subTest(ticker=ticker):
                converted = share_basis_price(
                    ticker, day, Decimal(vendor_close), policy
                )
                assert converted is not None
                expected = Decimal(stated_close) * share_factor

                self.assertEqual(round(converted, 2), expected)

    def test_amphenol_and_monster_prices_include_their_2026_splits(self):
        """The vendor did not restate history for the 2:1 splits of APH and MNST in 2026,
        while EPS includes them. APH's vendor close of 70.58 on 20 December 2024 is the
        historical quote (snapshot capitalization 85.09 bn / about 1.206 bn shares), so on
        the EPS basis it is 35.29 and P/E 2024 is about 36.8 (EPS 0.96), not 73.5. From
        the vendor step on (MNST 6 August, APH 31 August 2026) prices are unchanged."""
        policy = split_rules()
        references = [
            # ticker, day, vendor close, expected close on the EPS share basis
            ("APH", date(2024, 12, 20), "70.5800018311", "35.29"),
            ("MNST", date(2024, 12, 20), "51.7200012207", "25.86"),
            ("MNST", date(2026, 8, 5), "94.4599990845", "47.23"),
            ("MNST", date(2026, 8, 6), "47.0800018311", "47.08"),
            ("APH", date(2026, 8, 28), "157.7400054932", "78.87"),
            ("APH", date(2026, 8, 31), "79.2750015259", "79.28"),
        ]

        for ticker, day, vendor_close, expected in references:
            with self.subTest(ticker=ticker, day=day):
                converted = share_basis_price(
                    ticker, day, Decimal(vendor_close), policy
                )
                assert converted is not None

                self.assertEqual(round(converted, 2), Decimal(expected))

    def test_company_without_vendor_events_keeps_its_reported_close(self):
        """ExxonMobil's 2024 10-K cover states a $115.12 close on 28 June 2024, the same
        as the vendor close; no conversion is applied."""
        vendor_close = Decimal("115.1200027466")

        converted = share_basis_price("XOM", date(2024, 6, 28), vendor_close)
        assert converted is not None

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
