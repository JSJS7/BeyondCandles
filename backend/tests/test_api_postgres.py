"""Opt-in: the screener and stock API tests on PostgreSQL built by the migrations (K17).

The regular API tests use SQLite, which stores numbers and JSON differently from the
JSONB and Numeric columns of the real database. These subclasses run the same tests on
a random test database that this module creates and removes.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

import test_screener
import test_stocks_api

TEST_DATABASE_PREFIX = "beyondcandles_test_"
RANDOM_NAME_LENGTH = 32


class MigratedPostgres:
    """One migrated database per test class, emptied before every test."""

    @classmethod
    def setUpClass(cls):
        from psycopg import sql
        from sqlalchemy import create_engine

        from app.database import engine

        cls.name = TEST_DATABASE_PREFIX + uuid4().hex
        cls.admin = create_engine(
            engine.url,
            isolation_level="AUTOCOMMIT",
            connect_args={"connect_timeout": 5},
        )
        with cls.admin.connect() as connection:
            driver = connection.connection.driver_connection
            assert driver is not None
            with driver.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name))
                )
        backend = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=backend,
            env=dict(os.environ, POSTGRES_DB=cls.name),
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            cls.tearDownClass()
            raise RuntimeError(result.stdout + result.stderr)
        cls.postgres = create_engine(
            engine.url.set(database=cls.name), connect_args={"connect_timeout": 5}
        )

    @classmethod
    def tearDownClass(cls):
        from psycopg import sql

        if hasattr(cls, "postgres"):
            cls.postgres.dispose()
        # Remove only the database this class created.
        assert cls.name.startswith(TEST_DATABASE_PREFIX)
        assert len(cls.name) == len(TEST_DATABASE_PREFIX) + RANDOM_NAME_LENGTH
        with cls.admin.connect() as connection:
            driver = connection.connection.driver_connection
            assert driver is not None
            with driver.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(cls.name)
                    )
                )
        cls.admin.dispose()

    def make_database(self):
        """The migrated schema with no rows, so each test starts as on SQLite."""
        from sqlalchemy import text

        from app.database import Base

        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        with self.postgres.begin() as connection:
            connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        return self.postgres

    def tearDown(self):
        # The parent disposes its engine; this one is shared by the class.
        self.client.close()
        from app.main import app

        app.dependency_overrides.clear()


RUN_DATABASE_TESTS = os.environ.get("BC_RUN_DB_TESTS") == "1"
SKIP_REASON = "Set BC_RUN_DB_TESTS=1 to create an isolated PostgreSQL test database"


@unittest.skipUnless(RUN_DATABASE_TESTS, SKIP_REASON)
class PostgresScreenerApiTests(MigratedPostgres, test_screener.ScreenerApiTests):
    pass


@unittest.skipUnless(RUN_DATABASE_TESTS, SKIP_REASON)
class PostgresStockApiTests(MigratedPostgres, test_stocks_api.StockApiTests):
    pass


if __name__ == "__main__":
    unittest.main()
