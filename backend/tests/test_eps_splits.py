"""Check share-count arithmetic and source approvals independently of downloaded data."""

import copy
import hashlib
import json
import unittest
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_stocks_api import reported_fact

from app.annual_financials import annual_periods
from app.data_pipeline.eps_splits import checked_reference, verify_comparisons
from app.data_pipeline.source_files import sha256
from app.eps_splits import (
    adjusted_eps,
    load_rules,
    normalize_eps,
    sources_fingerprint,
    split_rules,
)
from app.schemas.stock import EpsSplit


def eps_period(value="8.31", filed=date(2018, 11, 5), cik="0000320193"):
    return annual_periods(
        [
            reported_fact(
                source_cik=cik,
                metric="eps_diluted",
                tag="EarningsPerShareDiluted",
                unit="USD/shares",
                value=Decimal(value),
                filed=filed,
            )
        ]
    )


def approved_policy(periods, actions=(), ticker="AAPL"):
    return {
        "version": 1,
        "basis_date": "2026-10-13",
        "companies": {ticker: {"observed_sha256": sources_fingerprint(periods)}},
        "actions": [
            dict(
                ticker=ticker,
                source_cik="0000320193",
                date=day,
                new_shares=new,
                old_shares=old,
                source_url="https://www.sec.gov/Archives/reviewed-split.htm",
            )
            for day, new, old in actions
        ],
    }


class EpsSplitTests(unittest.TestCase):
    def test_known_forward_reverse_compound_and_fractional_results(self):
        """Use independently worked examples, including negative EPS and a 50% stock dividend."""
        cases = (
            ("8.31", Fraction(4), "2.0775000000"),  # Apple FY2016, 2020 split.
            ("6.63", Fraction(40), "0.1657500000"),  # NVIDIA FY2019, 4:1 then 10:1.
            ("-2.62", Fraction(1, 8), "-20.9600000000"),  # GE FY2018, reverse 1:8.
            (
                "8.71",
                Fraction(1, 2),
                "17.4200000000",
            ),  # HON FY2024, actual reverse 1:2.
            ("9.17", Fraction(15), "0.6113333333"),  # O'Reilly FY2015, 2025 split.
            ("3", Fraction(3, 2), "2.0000000000"),
            ("0", Fraction(50), "0.0000000000"),
        )
        for reported, ratio, expected in cases:
            with self.subTest(reported=reported, ratio=ratio):
                self.assertEqual(
                    adjusted_eps(Decimal(reported), ratio), Decimal(expected)
                )

    def test_compound_splits_preserve_sources_and_input(self):
        """Apply two pending splits once, retain exact raw facts and leave the caller's data untouched."""
        periods = eps_period("6.63", filed=date(2021, 2, 26))
        before = copy.deepcopy(periods)
        policy = approved_policy(periods, (("2021-07-20", 4, 1), ("2024-06-10", 10, 1)))

        result = normalize_eps("AAPL", periods, policy)
        metric = result[0].metrics["eps_diluted"]

        self.assertEqual(metric.value, Decimal("0.16575"))
        self.assertEqual(metric.eps_share_basis.new_shares, 40)
        self.assertEqual(metric.eps_share_basis.old_shares, 1)
        self.assertEqual(metric.eps_share_basis.reported_value, Decimal("6.63"))
        self.assertEqual(
            metric.selected_sources, before[0].metrics["eps_diluted"].selected_sources
        )
        self.assertEqual(periods, before)
        self.assertEqual(normalize_eps("AAPL", periods, policy), result)

    def test_later_filing_already_includes_earlier_splits(self):
        """Never split an already-restated SEC value a second time; still apply a later event."""
        periods = eps_period("1.13", filed=date(2022, 3, 18))
        policy = approved_policy(periods, (("2021-07-20", 4, 1), ("2024-06-10", 10, 1)))
        metric = normalize_eps("AAPL", periods, policy)[0].metrics["eps_diluted"]
        self.assertEqual(metric.value, Decimal("0.113"))
        self.assertEqual(
            [str(a.date) for a in metric.eps_share_basis.applied_splits], ["2024-06-10"]
        )

    def test_no_pending_split_preserves_reported_decimal_and_basic_basis(self):
        """An unchanged share count keeps source precision and never relabels basic EPS as diluted."""
        periods = eps_period("41.2700000000")
        periods[0].metrics["eps_diluted"].eps_basis = "basic"
        policy = approved_policy(periods)
        metric = normalize_eps("AAPL", periods, policy)[0].metrics["eps_diluted"]
        self.assertEqual(metric.value.as_tuple(), Decimal("41.2700000000").as_tuple())
        self.assertEqual(metric.eps_basis, "basic")
        self.assertEqual(metric.eps_share_basis.applied_splits, [])

    def test_unknown_or_changed_sources_require_review(self):
        """Changed values, dates, issuer, accession, definition or EPS basis invalidate approval."""
        periods = eps_period()
        policy = approved_policy(periods, (("2020-08-31", 4, 1),))
        for field, value in (
            ("value", Decimal("8.32")),
            ("filed", date(2020, 11, 1)),
            ("source_cik", "0000000001"),
            ("accession", "new-version"),
            ("tag", "EarningsPerShareBasic"),
        ):
            changed = copy.deepcopy(periods)
            metric = changed[0].metrics["eps_diluted"]
            setattr(metric.selected_sources[0], field, value)
            with self.subTest(field=field):
                result = normalize_eps("AAPL", changed, policy)[0].metrics[
                    "eps_diluted"
                ]
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)
                self.assertTrue(result.selected_sources)
        self.assertEqual(
            normalize_eps("UNKNOWN", periods, policy)[0].metrics["eps_diluted"].status,
            "needs_review",
        )
        changed = copy.deepcopy(periods)
        changed[0].metrics["eps_diluted"].eps_basis = "basic"
        self.assertEqual(
            normalize_eps("AAPL", changed, policy)[0].metrics["eps_diluted"].status,
            "needs_review",
        )

    def test_changed_selected_result_or_missing_source_invalidates_approval(self):
        """A changed calculation result or a ready metric without a source must fail closed."""
        periods = eps_period()
        policy = approved_policy(periods)
        for change in ("value", "source"):
            altered = copy.deepcopy(periods)
            metric = altered[0].metrics["eps_diluted"]
            if change == "value":
                metric.value = Decimal("99")
            else:
                metric.selected_sources = []
            with self.subTest(change=change):
                result = normalize_eps("AAPL", altered, policy)[0].metrics[
                    "eps_diluted"
                ]
                self.assertEqual(result.status, "needs_review")
                self.assertIsNone(result.value)

    def test_unavailable_and_other_metrics_are_unchanged(self):
        """Splits affect per-share values only; an absent EPS never becomes zero."""
        periods = annual_periods([reported_fact()])
        result = normalize_eps("AAPL", periods, {})
        self.assertEqual(result, periods)
        self.assertIsNone(result[0].metrics["eps_diluted"].value)

    def test_post_2024_split_and_json_metadata_are_exact(self):
        """The price snapshot includes later splits; response values remain decimal strings."""
        periods = eps_period("9.17")
        policy = approved_policy(periods, (("2025-06-10", 15, 1),))
        metric = normalize_eps("AAPL", periods, policy)[0].metrics["eps_diluted"]
        payload = metric.model_dump(mode="json")
        self.assertEqual(payload["value"], "0.6113333333")
        self.assertEqual(payload["eps_share_basis"]["reported_value"], "9.17")
        self.assertEqual(payload["eps_share_basis"]["as_of"], "2026-10-13")

    def test_missing_policy_fails_closed(self):
        """An unreadable policy cannot silently publish unnormalized EPS as comparable data."""
        with patch(
            "app.eps_splits.split_rules", side_effect=ValueError("changed prices")
        ):
            metric = normalize_eps("AAPL", eps_period())[0].metrics["eps_diluted"]
        self.assertEqual(metric.status, "needs_review")
        self.assertIsNone(metric.value)

    def test_invalid_share_counts_are_rejected(self):
        """Zero, negative and fractional counts cannot enter exact share-count arithmetic."""
        for count in (0, -1, 1.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                EpsSplit.model_validate(
                    {
                        "date": "2020-01-01",
                        "new_shares": count,
                        "old_shares": 1,
                        "source_url": "source",
                    }
                )

    def test_source_cache_checksum_and_location_are_checked(self):
        """An altered original or path escaping its cache invalidates the offline review."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.json"
            source.write_text("{}")
            ref = {"file": source.name, "sha256": sha256(source)}
            self.assertEqual(checked_reference(ref, root, set()), source)
            source.write_text("changed")
            with self.assertRaises(ValueError):
                checked_reference(ref, root, set())
            with self.assertRaises(ValueError):
                checked_reference(
                    {"file": "../escape.json", "sha256": "x"}, root, set()
                )

    def test_policy_rejects_changed_price_basis_and_unapproved_events(self):
        """A wrong snapshot, future/duplicate event or issuer cannot silently change the EPS scale."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "companies-200.json").write_text("{}")
            (root / "manifest.json").write_text(json.dumps({"sha256": "frozen-prices"}))
            path = root / "eps-split-rules.json"
            policy = approved_policy(eps_period(), (("2020-08-31", 4, 1),))
            policy.update(
                {
                    "selection_sha256": hashlib.sha256(b"{}").hexdigest(),
                    "price_manifest_sha256": "frozen-prices",
                }
            )
            policy["companies"]["AAPL"]["source_ciks"] = ["0000320193"]
            for change in ("price", "future", "duplicate", "issuer"):
                altered = copy.deepcopy(policy)
                if change == "price":
                    altered["price_manifest_sha256"] = "new-prices"
                elif change == "future":
                    altered["actions"][0]["date"] = "2027-01-01"
                elif change == "duplicate":
                    altered["actions"].append(copy.deepcopy(altered["actions"][0]))
                else:
                    altered["actions"][0]["source_cik"] = "0000000001"
                path.write_text(json.dumps(altered))
                load_rules.cache_clear()
                with (
                    self.subTest(change=change),
                    patch("app.eps_splits.DATA", root),
                    patch("app.eps_splits.RULES_PATH", path),
                    self.assertRaises(ValueError),
                ):
                    split_rules()
            load_rules.cache_clear()

    def test_reported_eps_controls_allow_rounding_but_reject_wrong_values_or_sources(
        self,
    ):
        """Independent rounded filing values must support the action and retain their exact provenance."""
        before = reported_fact(
            metric="eps_diluted", unit="USD/shares", value=Decimal("8.31")
        )
        after = dict(
            before, value=Decimal("2.08"), filed=date(2025, 2, 1), accession="later"
        )
        keys = (
            "source_cik",
            "tag",
            "unit",
            "value",
            "filed",
            "accession",
            "period_start",
            "period_end",
        )
        action = {
            "date": "2024-01-01",
            "new_shares": 4,
            "old_shares": 1,
            "retrospective_comparison": {
                "before": {key: str(before[key]) for key in keys},
                "after": {key: str(after[key]) for key in keys},
            },
        }
        self.assertEqual(verify_comparisons([action], [before, after]), 1)
        for change in ("value", "source"):
            broken = copy.deepcopy(action)
            broken["retrospective_comparison"]["after"][
                "value" if change == "value" else "accession"
            ] = "9.99" if change == "value" else "unseen"
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_comparisons([broken], [before, after])

    def test_frozen_honeywell_and_hilton_use_actual_reverse_splits(self):
        """Vendor separation factors must never replace the primary documented physical share ratios."""
        policy = split_rules()
        for ticker, day, expected in (
            ("HON", "2026-06-29", (1, 2)),
            ("HLT", "2017-01-04", (1, 3)),
        ):
            action = next(
                a
                for a in policy["actions"]
                if a["ticker"] == ticker and a["date"] == day
            )
            self.assertEqual((action["new_shares"], action["old_shares"]), expected)
            self.assertNotEqual(
                expected, (action["vendor_new_shares"], action["vendor_old_shares"])
            )
        self.assertFalse(any(a["ticker"] == "DHR" for a in policy["actions"]))
        self.assertEqual(
            sum(a["ticker"] == "DHR" for a in policy["excluded_vendor_events"]), 2
        )
