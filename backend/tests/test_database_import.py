"""Opt-in integration test; creates and removes only its own random test database."""
import csv
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4


@unittest.skipUnless(os.environ.get("BC_RUN_DB_TESTS") == "1", "Set BC_RUN_DB_TESTS=1 to create an isolated PostgreSQL test database")
class DatabaseImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Create a uniquely named test database using the local PostgreSQL server."""
        from sqlalchemy import create_engine
        from psycopg import sql
        from app.database import engine
        cls.name = "beyondcandles_test_" + uuid4().hex
        cls.admin = create_engine(engine.url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
        with cls.admin.connect() as c:
            with c.connection.driver_connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name)))
        cls.db = create_engine(engine.url.set(database=cls.name), connect_args={"connect_timeout": 5})
        cls.env = dict(os.environ, POSTGRES_DB=cls.name)
        cls.backend = Path(__file__).resolve().parents[1]

    @classmethod
    def tearDownClass(cls):
        """Close connections and remove only the database created by this test class."""
        from psycopg import sql
        cls.db.dispose()
        assert cls.name.startswith("beyondcandles_test_") and len(cls.name) == len("beyondcandles_test_") + 32
        with cls.admin.connect() as c:
            with c.connection.driver_connection.cursor() as cursor:
                cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(cls.name)))
        cls.admin.dispose()

    def alembic(self, *args):
        """Run a migration command against the test database and require success."""
        import sys
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=self.backend, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_migrations_idempotence_conflicts_and_rollback(self):
        """Preserve data through migrations and repeated imports; roll back conflicts."""
        from sqlalchemy import inspect, text
        from app.data_pipeline.prices import FIELDS, import_prices
        from app.data_pipeline.sec import import_sec
        from test_data_pipeline import SecTests
        self.alembic("upgrade", "0002_financial_facts")
        self.alembic("downgrade", "base")
        self.alembic("upgrade", "0002_financial_facts")
        # Exercise the removal on populated legacy tables, not just an empty schema.
        with self.db.begin() as c:
            c.execute(text("INSERT INTO data_sources (id,url,version,sha256,details) VALUES ('legacy','https://example.com','test',:sha,'{}')"), {"sha": "a" * 64})
            c.execute(text("INSERT INTO companies (ticker,name,exchange,sector,currency) VALUES ('AAPL','Apple Inc.','NMS','Technology','USD')"))
            c.execute(text("INSERT INTO daily_prices (company_id,date,open,high,low,close,adjusted_close,volume,source_id) SELECT id,'2020-01-02',10,12,9,11,5,100,'legacy' FROM companies"))
            c.execute(text("""INSERT INTO financial_facts (company_id,metric,taxonomy,tag,unit,value,period_start,period_end,filed,accession,form,fiscal_period,period_kind,source_id)
                SELECT id,'net_income','us-gaap','NetIncomeLoss','USD',100,'2020-01-01','2020-12-31','2021-02-01','original','10-K','FY','annual','legacy' FROM companies"""))
            c.execute(text("INSERT INTO import_runs (source_id,result) VALUES ('legacy','{}')"))
            before = {table: c.execute(text(f"SELECT to_jsonb(t) - 'source_id' FROM {table} t ORDER BY 1")).scalars().all() for table in ("companies", "daily_prices", "financial_facts")}
        self.alembic("upgrade", "head")
        self.alembic("check")
        with self.db.begin() as c:
            self.assertEqual(set(inspect(c).get_table_names()), {"alembic_version", "companies", "daily_prices", "financial_facts"})
            after = {table: c.execute(text(f"SELECT to_jsonb(t) - 'source_id' FROM {table} t ORDER BY 1")).scalars().all() for table in before}
            self.assertEqual(before, after)
            for table in ("daily_prices", "financial_facts"):
                self.assertNotIn("source_id", {col["name"] for col in inspect(c).get_columns(table)})
            # Remove only test fixtures before exercising the real importers.
            for table in ("financial_facts", "daily_prices", "companies"):
                c.execute(text(f"DELETE FROM {table}"))
        with tempfile.TemporaryDirectory(prefix="beyondcandles-test-") as folder:
            path = Path(folder) / "prices.csv"
            rows = [["AAPL", "2020-01-02", "10", "12", "9", "11", "5", "100"], ["AAPL", "2020-01-03", "10", "12", "9", "11", "5", "100"]]
            def write(values):
                with path.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(FIELDS)
                    writer.writerows(values)
            write(rows)
            prepared = (path, {"AAPL": {"Longname": "Apple Inc.", "Exchange": "NMS", "Sector": "Technology"}}, {"prices": 2})
            with patch("app.database.engine", self.db), patch("app.data_pipeline.prices.validate_prices", return_value=prepared):
                # The first import inserts two prices; repeating it must insert none.
                self.assertEqual(import_prices()["inserted"], 2)
                self.assertEqual(import_prices()["inserted"], 0)

                # A changed price must be reported instead of silently overwriting it.
                changed = [list(row) for row in rows]
                changed[0][5] = "10"
                write(changed)
                with self.assertRaisesRegex(ValueError, "Existing prices differ"):
                    import_prices()
                # A failed COPY must leave existing prices untouched.
                invalid = [list(row) for row in rows]
                invalid[1][2] = "-1"
                write(invalid)
                with self.assertRaises(Exception):
                    import_prices()
            entry = {"cik": "0000320193", "facts": {"sha256": "a" * 64, "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"}, "submissions": {"sha256": "b" * 64}}
            with patch("app.database.engine", self.db), patch("app.data_pipeline.sec.load_pilot", return_value=({}, {"AAPL": (entry, SecTests().body())})):
                # Both filing versions are retained, and repeating the import adds no copies.
                self.assertEqual(import_sec()["AAPL"]["inserted"], 2)
                self.assertEqual(import_sec()["AAPL"]["inserted"], 0)
            # A different cache file must not duplicate facts from the same SEC filing.
            refreshed = dict(entry, facts={"sha256": "c" * 64})
            with patch("app.database.engine", self.db), patch("app.data_pipeline.sec.load_pilot", return_value=({}, {"AAPL": (refreshed, SecTests().body())})):
                self.assertEqual(import_sec()["AAPL"]["inserted"], 0)
            changed_facts = SecTests().body()
            # An existing filing's value must never be silently replaced.
            for fact in changed_facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]:
                fact["val"] = 1
            with patch("app.database.engine", self.db), patch("app.data_pipeline.sec.load_pilot", return_value=({}, {"AAPL": (entry, changed_facts)})):
                with self.assertRaisesRegex(ValueError, "Conflicting stored SEC fact"):
                    import_sec()
            with self.db.connect() as c:
                # Verify actual stored data as well as the counts returned by the importers.
                self.assertEqual(c.execute(text("SELECT count(*) FROM daily_prices")).scalar_one(), 2)
                self.assertEqual(c.execute(text("SELECT close FROM daily_prices ORDER BY date LIMIT 1")).scalar_one(), 11)
                self.assertEqual(c.execute(text("SELECT count(*) FROM financial_facts")).scalar_one(), 2)


if __name__ == "__main__":
    unittest.main()
