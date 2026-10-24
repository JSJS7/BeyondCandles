"""HTTP contracts and annual grouping; test fixtures never use the application database."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app.annual_financials import annual_periods
from app.database import Base, get_connection
from app.main import app
from app.models import Company, DailyPrice, FinancialFact


def reported_fact(**changes):
    fact = {
        "source_cik": "0000320193",
        "metric": "net_income",
        "taxonomy": "us-gaap",
        "tag": "NetIncomeLoss",
        "unit": "USD",
        "value": Decimal("100"),
        "period_start": date(2020, 1, 1),
        "period_end": date(2020, 12, 31),
        "filed": date(2021, 2, 1),
        "accession": "original",
        "form": "10-K",
        "period_kind": "annual",
    }
    fact.update(changes)
    return fact


class AnnualFinancialTests(unittest.TestCase):
    def test_latest_revision_preserves_source_and_missing_values(self):
        """Show the latest revision, retain its filing date and leave missing metrics empty."""
        original = reported_fact()
        revised = reported_fact(
            value=Decimal("95"), filed=date(2022, 2, 1), accession="revised"
        )
        period = annual_periods([original, revised])[0]
        metric = period.metrics["net_income"]
        self.assertEqual(metric.value, Decimal("95"))
        self.assertEqual(metric.variants[0].accession, "revised")
        self.assertEqual(period.metrics["revenue"].status, "not_available")
        self.assertIsNone(period.metrics["revenue"].value)

    def test_same_day_parent_conflicts_require_review(self):
        """Do not silently choose between conflicting same-day parent-income filing values."""
        for alternative in (reported_fact(accession="another", value=Decimal("99")),):
            with self.subTest(tag=alternative["tag"]):
                metric = annual_periods([reported_fact(), alternative])[0].metrics[
                    "net_income"
                ]
                self.assertEqual(metric.status, "needs_review")
                self.assertIsNone(metric.value)
                self.assertEqual(len(metric.variants), 2)

    def test_balance_matches_period_end_without_using_quarterly_facts(self):
        """Attach an instant balance to its year end and exclude quarterly/YTD values."""
        balance = reported_fact(
            metric="equity",
            tag="StockholdersEquity",
            period_kind="instant",
            period_start=date(2020, 12, 31),
            value=Decimal("0"),
        )
        quarter = reported_fact(
            form="10-Q",
            period_kind="quarter",
            period_start=date(2020, 10, 1),
            filed=date(2022, 2, 1),
            value=Decimal("999"),
        )
        interim = reported_fact(
            form="10-Q",
            period_kind="nine_months_ytd",
            period_end=date(2020, 9, 30),
            filed=date(2022, 2, 1),
            value=Decimal("999"),
        )
        result = annual_periods([reported_fact(), balance, quarter, interim])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metrics["equity"].variants[0].value, Decimal("0"))
        self.assertEqual(result[0].metrics["equity"].status, "needs_review")
        self.assertEqual(result[0].metrics["net_income"].value, Decimal("100"))

    def test_quarterly_filings_can_revise_an_existing_full_year(self):
        """Use a later full-year value from 10-Q or 10-Q/A and retain its source."""
        original = reported_fact()
        for form in ("10-Q", "10-Q/A"):
            with self.subTest(form=form):
                revised = reported_fact(
                    form=form,
                    value=Decimal("95"),
                    filed=date(2022, 2, 1),
                    accession="quarterly-revision",
                )

                periods = annual_periods([revised, original])
                metric = periods[0].metrics["net_income"]

                self.assertEqual(len(periods), 1)
                self.assertEqual(metric.status, "ready")
                self.assertEqual(metric.value, Decimal("95"))
                self.assertEqual(metric.variants[0].form, form)
                self.assertEqual(metric.variants[0].filed, date(2022, 2, 1))
                self.assertEqual(metric.variants[0].accession, "quarterly-revision")

    def test_later_annual_report_takes_precedence_over_older_quarterly_revision(self):
        """Choose by filing date rather than giving quarterly revisions permanent priority."""
        original = reported_fact()
        quarterly_revision = reported_fact(
            form="10-Q/A",
            value=Decimal("95"),
            filed=date(2021, 8, 1),
            accession="quarterly-revision",
        )
        later_annual = reported_fact(
            value=Decimal("90"),
            filed=date(2022, 2, 1),
            accession="later-annual",
        )

        period = annual_periods([original, later_annual, quarterly_revision])[0]

        self.assertEqual(period.metrics["net_income"].value, Decimal("90"))
        self.assertEqual(
            period.metrics["net_income"].variants[0].accession, "later-annual"
        )

    def test_quarterly_revision_updates_only_the_matching_year_end_balance(self):
        """Revise the annual instant balance without substituting an interim balance."""
        original = reported_fact(
            metric="equity",
            tag="StockholdersEquity",
            period_kind="instant",
            period_start=date(2020, 12, 31),
            value=Decimal("100"),
        )
        revised = dict(
            original,
            form="10-Q/A",
            value=Decimal("0"),
            filed=date(2021, 8, 1),
            accession="balance-revision",
        )
        interim = dict(
            original,
            form="10-Q",
            value=Decimal("999"),
            period_start=date(2021, 6, 30),
            period_end=date(2021, 6, 30),
            filed=date(2021, 8, 1),
            accession="interim-balance",
        )

        periods = annual_periods([reported_fact(), original, revised, interim])
        metric = periods[0].metrics["equity"]

        self.assertEqual(len(periods), 1)
        self.assertEqual(metric.variants[0].value, Decimal("0"))
        self.assertEqual(metric.status, "needs_review")
        self.assertEqual(metric.variants[0].accession, "balance-revision")

    def test_quarterly_facts_do_not_create_annual_periods_or_change_their_dates(self):
        """Keep annual-report periods even when a 10-Q contains other full-year ranges."""
        quarter_only_year = reported_fact(
            form="10-Q",
            period_start=date(2019, 1, 1),
            period_end=date(2019, 12, 31),
            value=Decimal("999"),
            accession="quarter-only-year",
        )
        different_start = reported_fact(
            form="10-Q",
            period_start=date(2020, 1, 2),
            value=Decimal("999"),
            filed=date(2022, 2, 1),
            accession="different-start",
        )

        self.assertEqual(annual_periods([quarter_only_year]), [])
        periods = annual_periods([reported_fact(), quarter_only_year, different_start])

        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0].period_start, date(2020, 1, 1))
        self.assertEqual(periods[0].period_end, date(2020, 12, 31))
        self.assertEqual(periods[0].metrics["net_income"].value, Decimal("100"))

    def test_quarterly_revisions_preserve_definition_and_same_day_conflicts(self):
        """Retain distinct tags and conflicting values instead of silently resolving them."""
        original = reported_fact()
        revised = reported_fact(
            form="10-Q",
            value=Decimal("95"),
            filed=date(2022, 2, 1),
            accession="quarterly-revision",
        )
        for alternative in (
            dict(revised, value=Decimal("99"), accession="same-day-conflict"),
        ):
            with self.subTest(tag=alternative["tag"], form=alternative["form"]):
                period = annual_periods([original, revised, alternative])[0]
                metric = period.metrics["net_income"]

                self.assertEqual(metric.status, "needs_review")
                self.assertIsNone(metric.value)
                self.assertEqual(
                    {v.value for v in metric.variants}, {Decimal("95"), Decimal("99")}
                )
                self.assertEqual(len(metric.variants), 2)

    def test_distinct_fiscal_periods_and_foreign_annual_forms_are_retained(self):
        """Keep actual fiscal dates and supported foreign annual forms instead of calendar-year assumptions."""
        fiscal = reported_fact(
            period_start=date(2020, 7, 1), period_end=date(2021, 6, 30), form="20-F"
        )
        periods = annual_periods([reported_fact(), fiscal])
        self.assertEqual(periods[0].period_end, date(2021, 6, 30))
        self.assertEqual(len(periods), 2)


class StockApiTests(unittest.TestCase):
    def setUp(self):
        self.database = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.database)
        with self.database.begin() as connection:
            connection.execute(
                Company.__table__.insert(),
                [
                    {
                        "id": 1,
                        "ticker": "AAPL",
                        "name": "Apple",
                        "exchange": "NMS",
                        "sector": "Technology",
                        "currency": "USD",
                        "cik": "0000320193",
                    },
                    {
                        "id": 2,
                        "ticker": "OTHER",
                        "name": "Not published",
                        "exchange": "NMS",
                        "sector": None,
                        "currency": "USD",
                        "cik": None,
                    },
                ],
            )
            for day in (2, 3):
                connection.execute(
                    DailyPrice.__table__.insert(),
                    {
                        "company_id": 1,
                        "date": date(2020, 1, day),
                        "open": Decimal("10"),
                        "high": Decimal("12"),
                        "low": Decimal("9"),
                        "close": Decimal("11"),
                        "adjusted_close": Decimal("5"),
                        "volume": 100,
                    },
                )

        def test_connection():
            with self.database.connect() as connection:
                yield connection

        app.dependency_overrides[get_connection] = test_connection
        self.client = TestClient(app)
        self.split_actions = []

        def fixture_split_policy():
            # Artificial filings have their own approval; real cached source integrity is checked offline.
            from app.eps_splits import sources_fingerprint

            policy = {
                "basis_date": "2026-10-13",
                "actions": self.split_actions,
                "companies": {},
            }
            with self.database.connect() as connection:
                for company in connection.execute(
                    Company.__table__.select()
                ).mappings():
                    facts = (
                        connection.execute(
                            FinancialFact.__table__.select().where(
                                FinancialFact.company_id == company["id"]
                            )
                        )
                        .mappings()
                        .all()
                    )
                    periods = annual_periods(facts)
                    policy["companies"][company["ticker"]] = {
                        "observed_sha256": sources_fingerprint(periods)
                    }
            return policy

        self.split_patch = patch(
            "app.eps_splits.split_rules", side_effect=fixture_split_policy
        )
        self.split_patch.start()
        self.addCleanup(self.split_patch.stop)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.database.dispose()

    def test_catalogue_preserves_contract_and_hides_unimported_companies(self):
        """Expose imported companies with their original stock fields and stored sector."""
        response = self.client.get("/stocks")
        self.assertEqual(response.status_code, 200)
        expected = {
            "ticker": "AAPL",
            "name": "Apple",
            "exchange": "NMS",
            "sector": "Technology",
            "financial_history_note": None,
        }
        self.assertEqual(response.json(), [expected])
        self.assertEqual(self.client.get("/stocks/aapl").json(), expected)

    def test_unknown_company_returns_404_for_every_stock_endpoint(self):
        """Return 404 for unknown or unpublished companies across all read endpoints."""
        for ticker in ("MISSING", "OTHER"):
            for suffix in ("", "/prices", "/financials/annual"):
                with self.subTest(ticker=ticker, suffix=suffix):
                    self.assertEqual(
                        self.client.get(f"/stocks/{ticker}{suffix}").status_code, 404
                    )

    def test_price_range_sorting_and_decimal_strings(self):
        """Return sorted decimal strings, apply inclusive bounds and allow empty ranges."""
        rows = self.client.get("/stocks/AAPL/prices").json()
        self.assertEqual([row["date"] for row in rows], ["2020-01-02", "2020-01-03"])
        self.assertIsInstance(rows[0]["close"], str)
        bounded = self.client.get(
            "/stocks/AAPL/prices?start_date=2020-01-03&end_date=2020-01-03"
        )
        self.assertEqual(len(bounded.json()), 1)
        self.assertEqual(
            self.client.get("/stocks/AAPL/prices?start_date=2025-01-01").json(), []
        )

    def test_invalid_ranges_and_missing_financials(self):
        """Reject malformed/reversed dates and return an empty list for unavailable financials."""
        self.assertEqual(
            self.client.get("/stocks/AAPL/prices?start_date=bad").status_code, 422
        )
        self.assertEqual(
            self.client.get(
                "/stocks/AAPL/prices?start_date=2021-01-01&end_date=2020-01-01"
            ).status_code,
            422,
        )
        self.assertEqual(self.client.get("/stocks/AAPL/financials/annual").json(), [])

    def test_annual_endpoint_returns_a_quarterly_revision_as_a_decimal_string(self):
        """Read the revised full-year fact through HTTP with its original filing details."""
        original = reported_fact()
        revised = reported_fact(
            form="10-Q/A",
            value=Decimal("95"),
            filed=date(2022, 2, 1),
            accession="quarterly-revision",
        )
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(),
                [
                    dict(original, company_id=1),
                    dict(revised, company_id=1),
                ],
            )

        response = self.client.get("/stocks/AAPL/financials/annual")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        metric = response.json()[0]["metrics"]["net_income"]
        self.assertEqual(metric["status"], "ready")
        self.assertEqual(metric["value"], "95.0000000000")
        self.assertEqual(metric["variants"][0]["form"], "10-Q/A")
        self.assertEqual(metric["variants"][0]["filed"], "2022-02-01")
        self.assertEqual(metric["variants"][0]["accession"], "quarterly-revision")
        self.assertEqual(metric["variants"][0]["source_cik"], "0000320193")

    def test_operating_cash_flow_endpoint_exposes_calculation_and_original_variants(
        self,
    ):
        """HTTP returns total OCF 80 as a decimal string, retaining continuing 100 and discontinued -20."""
        from app.equity import fact_identity, variants_fingerprint
        from app.operating_cash_flow import CONTINUING_TAG, DISCONTINUED_TAG

        continuing = reported_fact(metric="operating_cash_flow", tag=CONTINUING_TAG)
        discontinued = reported_fact(
            metric="operating_cash_flow", tag=DISCONTINUED_TAG, value=Decimal(-20)
        )
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(),
                [dict(continuing, company_id=1), dict(discontinued, company_id=1)],
            )
        # The imported component is also a reported variant in this fixture.
        rule = {
            "observed_sha256": variants_fingerprint([continuing, discontinued]),
            "components": [
                {"fact": fact_identity(fact), "coefficient": 1}
                for fact in (continuing, discontinued)
            ],
            "value": "80",
            "calculation": "Continuing operating cash flow + discontinued operating cash flow",
            "note": "Verified against the same annual statement.",
        }
        with patch(
            "app.operating_cash_flow.cash_flow_rules",
            return_value={"0000320193:2020-01-01:2020-12-31": rule},
        ):
            response = self.client.get("/stocks/AAPL/financials/annual")
        self.assertEqual(response.status_code, 200)
        metric = response.json()[0]["metrics"]["operating_cash_flow"]
        self.assertEqual(metric["status"], "ready")
        self.assertEqual(Decimal(metric["value"]), Decimal(80))
        self.assertIsInstance(metric["value"], str)
        self.assertEqual(len(metric["variants"]), 2)
        self.assertEqual(len(metric["selected_sources"]), 2)
        self.assertIn("+", metric["calculation"])
        self.assertIn("whole company", metric["selection_note"])

    def test_operating_cash_flow_known_gap_is_unavailable_with_reported_variant(self):
        """An audited disclosure gap returns null while preserving the available continuing amount and reason."""
        from app.equity import variants_fingerprint
        from app.operating_cash_flow import CONTINUING_TAG

        fact = reported_fact(metric="operating_cash_flow", tag=CONTINUING_TAG)
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(), dict(fact, company_id=1)
            )
        rule = {
            "status": "not_available",
            "observed_sha256": variants_fingerprint([fact]),
            "note": "Discontinued operating cash flow is not separately disclosed.",
        }
        with patch(
            "app.operating_cash_flow.cash_flow_rules",
            return_value={"0000320193:2020-01-01:2020-12-31": rule},
        ):
            response = self.client.get("/stocks/AAPL/financials/annual")
        self.assertEqual(response.status_code, 200)
        metric = response.json()[0]["metrics"]["operating_cash_flow"]
        self.assertEqual(metric["status"], "not_available")
        self.assertIsNone(metric["value"])
        self.assertEqual(Decimal(metric["variants"][0]["value"]), Decimal(100))
        self.assertIn("not separately disclosed", metric["selection_note"])

    def test_reported_continuing_cash_flow_is_ready_with_machine_readable_scope(self):
        """Publish a negative reported subtotal with its source and scope, without claiming a whole-company total."""
        from app.equity import fact_identity, variants_fingerprint
        from app.operating_cash_flow import TOTAL_TAG

        fact = reported_fact(
            metric="operating_cash_flow", tag=TOTAL_TAG, value=Decimal(-120)
        )
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(), dict(fact, company_id=1)
            )
        rule = {
            "scope": "continuing",
            "observed_sha256": variants_fingerprint([fact]),
            "components": [
                {"fact": fact_identity(fact), "coefficient": 1, "scope": "continuing"}
            ],
            "value": "-120",
            "note": "A whole-company total is not separately available.",
        }
        with patch(
            "app.operating_cash_flow.cash_flow_rules",
            return_value={"0000320193:2020-01-01:2020-12-31": rule},
        ):
            response = self.client.get("/stocks/AAPL/financials/annual")
        self.assertEqual(response.status_code, 200)
        metrics = response.json()[0]["metrics"]
        cell = metrics["operating_cash_flow"]
        self.assertEqual(cell["status"], "ready")
        self.assertEqual(cell["scope"], "continuing")
        self.assertEqual(Decimal(cell["value"]), Decimal(-120))
        self.assertIsInstance(cell["value"], str)
        self.assertEqual(cell["selected_sources"][0]["accession"], "original")
        self.assertEqual(cell["selected_tag"], TOTAL_TAG)
        self.assertIsNone(metrics["net_income"]["scope"])

    def test_reviewed_eps_keeps_decimal_source_and_explains_basic_only_gap(self):
        """Expose one EPS field with exact sources; an unchanged legacy unavailable approval stays unavailable."""
        from app.equity import fact_identity, variants_fingerprint
        from app.schemas.stock import FilingValue

        for tag, status in (
            ("EarningsPerShareBasicAndDiluted", "ready"),
            ("EarningsPerShareBasic", "not_available"),
        ):
            with self.subTest(tag=tag):
                fact = reported_fact(
                    metric="eps_diluted",
                    tag=tag,
                    unit="USD/shares",
                    value=Decimal("-2.52"),
                )
                with self.database.begin() as connection:
                    connection.execute(FinancialFact.__table__.delete())
                    connection.execute(
                        FinancialFact.__table__.insert(), dict(fact, company_id=1)
                    )
                source = FilingValue.model_validate(fact)
                rule = {
                    "status": status,
                    "observed_sha256": variants_fingerprint([source]),
                    "fact": fact_identity(source),
                    "note": "Verified source definition.",
                    "statement": {
                        "url": "https://www.sec.gov/Archives/verified/R4.htm"
                    },
                    "label": "Reported EPS",
                    "column": "12 months ended 2020-12-31",
                }
                with patch(
                    "app.eps.eps_rules",
                    return_value={"0000320193:2020-01-01:2020-12-31": rule},
                ):
                    response = self.client.get("/stocks/AAPL/financials/annual")
                self.assertEqual(response.status_code, 200)
                cell = response.json()[0]["metrics"]["eps_diluted"]
                self.assertEqual(cell["status"], status)
                self.assertIn("Verified source definition.", cell["selection_note"])
                self.assertEqual(
                    cell["variants"][0]["statement_url"], rule["statement"]["url"]
                )
                self.assertEqual(
                    Decimal(cell["variants"][0]["value"]), Decimal("-2.52")
                )
                if status == "ready":
                    self.assertEqual(Decimal(cell["value"]), Decimal("-2.52"))
                    self.assertIsInstance(cell["value"], str)
                    self.assertEqual(cell["selected_sources"], cell["variants"])
                else:
                    self.assertIsNone(cell["value"])
                    self.assertEqual(cell["selected_sources"], [])

    def test_berkshire_basic_fallback_is_ready_with_basis_and_exact_original_source(
        self,
    ):
        """Publish reviewed Class B EPS in the existing metric, keeping its basic basis machine-readable."""
        from app.equity import fact_identity, variants_fingerprint
        from app.schemas.stock import FilingValue

        fact = reported_fact(
            source_cik="0001067983",
            metric="eps_diluted",
            tag="EarningsPerShareBasic",
            unit="USD/shares",
            value=Decimal("41.27"),
        )
        with self.database.begin() as connection:
            connection.execute(
                Company.__table__.insert(),
                dict(
                    id=3,
                    ticker="BRK-B",
                    name="Berkshire Hathaway",
                    exchange="NYQ",
                    currency="USD",
                    cik="0001067983",
                ),
            )
            connection.execute(
                FinancialFact.__table__.insert(), dict(fact, company_id=3)
            )
        source = FilingValue.model_validate(fact)
        rule = {
            "status": "ready",
            "eps_basis": "basic",
            "observed_sha256": variants_fingerprint([source]),
            "fact": fact_identity(source),
            "note": "Reviewed basic EPS for Class B.",
            "statement": {"url": "https://www.sec.gov/Archives/verified/R4.htm"},
            "label": "Net earnings per average equivalent",
            "column": "12 months ended 2020-12-31; Class B",
        }
        with patch(
            "app.eps.eps_rules", return_value={"0001067983:2020-01-01:2020-12-31": rule}
        ):
            response = self.client.get("/stocks/BRK-B/financials/annual")
        self.assertEqual(response.status_code, 200)
        metrics = response.json()[0]["metrics"]
        cell = metrics["eps_diluted"]
        self.assertEqual(cell["status"], "ready")
        self.assertEqual(cell["eps_basis"], "basic")
        self.assertEqual(Decimal(cell["value"]), Decimal("41.27"))
        self.assertIsInstance(cell["value"], str)
        self.assertEqual(cell["selected_tag"], "EarningsPerShareBasic")
        self.assertEqual(cell["selected_sources"], cell["variants"])
        self.assertEqual(cell["selected_sources"][0]["accession"], "original")
        self.assertIsNone(metrics["net_income"]["eps_basis"])
        self.assertIsNone(cell["scope"])

    def test_database_failure_returns_503_without_driver_details(self):
        """Expose a fixed 503 message rather than connection strings or credentials."""

        def unavailable():
            raise OperationalError(
                "private connection details", {}, Exception("private password")
            )

        app.dependency_overrides[get_connection] = unavailable
        response = self.client.get("/stocks")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"detail": "Database is temporarily unavailable."}
        )

    def test_revenue_endpoint_selects_verified_total_and_preserves_component_sources(
        self,
    ):
        """Expose a verified decimal total with its definition and both original candidates."""
        total = reported_fact(metric="revenue", tag="Revenues", value=Decimal("100"))
        goods = reported_fact(
            metric="revenue", tag="SalesRevenueGoodsNet", value=Decimal("30")
        )
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(),
                [
                    dict(total, company_id=1),
                    dict(goods, company_id=1),
                ],
            )
        rules = {
            "0000320193:2020-01-01:2020-12-31": {
                "tag": "Revenues",
                "label": "Total revenues",
                "value": "100",
                "filed": "2021-02-01",
                "accession": "original",
            }
        }
        with patch("app.revenue.revenue_rules", return_value=rules):
            response = self.client.get("/stocks/AAPL/financials/annual")

        self.assertEqual(response.status_code, 200)
        revenue = response.json()[0]["metrics"]["revenue"]
        self.assertEqual(revenue["status"], "ready")
        self.assertEqual(revenue["value"], "100.0000000000")
        self.assertEqual(revenue["selected_tag"], "Revenues")
        self.assertEqual(len(revenue["variants"]), 2)
        self.assertIn("Other candidates are retained", revenue["selection_note"])

    def test_annual_endpoint_exposes_verified_fiscal_year_separately_from_dates(self):
        """Return FY2015 for a January-2016 year end without rewriting the source dates."""
        fact = reported_fact(
            period_start=date(2014, 12, 29),
            period_end=date(2016, 1, 3),
            filed=date(2016, 2, 1),
        )
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(), dict(fact, company_id=1)
            )
        rules = {
            "periods": {"0000320193:2014-12-29:2016-01-03": {"fiscal_year": 2015}},
            "aliases": {},
        }
        with patch("app.annual_financials.fiscal_rules", return_value=rules):
            response = self.client.get("/stocks/AAPL/financials/annual")
        self.assertEqual(response.status_code, 200)
        period = response.json()[0]
        self.assertEqual(period["fiscal_year"], 2015)
        self.assertEqual(period["period_end"], "2016-01-03")
        self.assertEqual(
            period["metrics"]["net_income"]["variants"][0]["period_end"], "2016-01-03"
        )

    def test_net_income_endpoint_selects_parent_and_preserves_group_value(self):
        """Return parent income as a decimal string with its selected source and group alternative."""
        parent = reported_fact()
        group = reported_fact(tag="ProfitLoss", value=Decimal("130"))
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(),
                [
                    dict(parent, company_id=1),
                    dict(group, company_id=1),
                ],
            )

        response = self.client.get("/stocks/AAPL/financials/annual")
        self.assertEqual(response.status_code, 200)
        cell = response.json()[0]["metrics"]["net_income"]
        self.assertEqual(cell["value"], "100.0000000000")
        self.assertEqual(cell["selected_tag"], "NetIncomeLoss")
        self.assertEqual(len(cell["variants"]), 2)
        self.assertEqual(cell["selected_sources"][0]["accession"], "original")

    def test_net_income_endpoint_exposes_same_filing_calculation_sources(self):
        """Expose computed parent income without pretending it is one reported SEC fact."""
        group = reported_fact(tag="ProfitLoss")
        minority = reported_fact(
            tag="NetIncomeLossAttributableToNoncontrollingInterest",
            value=Decimal("20"),
        )
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(),
                [
                    dict(group, company_id=1),
                    dict(minority, company_id=1),
                ],
            )
        rule = {
            "0000320193:2020-01-01:2020-12-31": {
                "filed": "2021-02-01",
                "accession": "original",
                "value": "80",
                "components": [
                    {"tag": "ProfitLoss", "coefficient": 1},
                    {"tag": minority["tag"], "coefficient": -1},
                ],
                "calculation": "Group net income - allocations to other owners",
                "note": "Verified in the income statement.",
            },
        }
        with patch("app.net_income.net_income_rules", return_value=rule):
            response = self.client.get("/stocks/AAPL/financials/annual")
        self.assertEqual(response.status_code, 200)
        cell = response.json()[0]["metrics"]["net_income"]
        self.assertEqual(cell["value"], "80.0000000000")
        self.assertEqual(len(cell["selected_sources"]), 2)
        self.assertIn("other owners", cell["calculation"])
        self.assertIsNone(cell["selected_tag"])

    def test_equity_endpoint_preserves_an_original_report_column(self):
        """Expose a verified parent column without inventing a Companyfacts record or GAAP tag."""
        from app.equity import fact_identity, variants_fingerprint

        annual = reported_fact()
        group = reported_fact(
            metric="equity",
            tag="StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            period_kind="instant",
            period_start=date(2020, 12, 31),
        )
        parent = dict(
            group,
            value=Decimal("80"),
            statement_url="https://www.sec.gov/Archives/R7.htm",
            statement_label="Balance at Dec. 31, 2020",
            statement_column="Parent [Member]",
        )
        rule = {
            "observed_sha256": variants_fingerprint([group]),
            "value": "80",
            "anchor": fact_identity(group),
            "statement": {"url": parent["statement_url"]},
            "note": "Verified reported parent column.",
            "components": [
                {
                    "fact": fact_identity(parent),
                    "coefficient": 1,
                    "statement_position": True,
                }
            ],
        }
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(),
                [dict(annual, company_id=1), dict(group, company_id=1)],
            )
        with patch(
            "app.equity.equity_rules", return_value={"0000320193:2020-12-31": rule}
        ):
            response = self.client.get("/stocks/AAPL/financials/annual")
        self.assertEqual(response.status_code, 200)
        cell = response.json()[0]["metrics"]["equity"]
        self.assertEqual(cell["value"], "80")
        self.assertEqual(cell["selected_sources"][0]["tag"], group["tag"])
        self.assertEqual(
            cell["selected_sources"][0]["statement_column"], "Parent [Member]"
        )
        self.assertEqual(len(cell["variants"]), 2)
        with self.database.connect() as connection:
            self.assertEqual(
                len(connection.execute(FinancialFact.__table__.select()).all()), 2
            )

    def test_api_normalizes_eps_and_preserves_original_source(self):
        """HTTP EPS uses the approved share basis while returning the unmodified filed value."""
        fact = reported_fact(
            metric="eps_diluted",
            tag="EarningsPerShareDiluted",
            unit="USD/shares",
            value=Decimal("8.31"),
        )
        with self.database.begin() as connection:
            connection.execute(
                FinancialFact.__table__.insert(), dict(fact, company_id=1)
            )
        self.split_actions = [
            {
                "ticker": "AAPL",
                "source_cik": "0000320193",
                "date": "2024-01-01",
                "new_shares": 4,
                "old_shares": 1,
                "source_url": "https://www.sec.gov/Archives/test.htm",
            }
        ]
        payload = self.client.get("/stocks/AAPL/financials/annual").json()
        metric = payload[0]["metrics"]["eps_diluted"]
        self.assertEqual(metric["value"], "2.0775000000")
        self.assertEqual(
            Decimal(metric["selected_sources"][0]["value"]), Decimal("8.31")
        )
        self.assertEqual(metric["eps_share_basis"]["new_shares"], 4)
        self.assertEqual(metric["eps_basis"], "diluted")
        with patch("app.eps_splits.split_rules", return_value={}):
            unresolved = self.client.get("/stocks/AAPL/financials/annual").json()[0][
                "metrics"
            ]["eps_diluted"]
        self.assertEqual(unresolved["status"], "needs_review")
        self.assertIsNone(unresolved["value"])
        self.assertEqual(unresolved["selected_sources"], metric["selected_sources"])
