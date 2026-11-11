import os
import unittest

RUN_DATABASE_TESTS = os.environ.get("BC_RUN_DB_TESTS") == "1"
SKIP_REASON = "Set BC_RUN_DB_TESTS=1 to test against the local PostgreSQL"


@unittest.skipUnless(RUN_DATABASE_TESTS, SKIP_REASON)
class DroppedConnectionTests(unittest.TestCase):
    def test_request_after_a_dropped_connection_succeeds(self):
        """N3: after PostgreSQL drops a pooled connection (as on a database restart),
        the next request gets a working connection instead of an error. The test only
        runs `SELECT 1` and ends its own connection, so no data is touched."""
        from sqlalchemy import create_engine, text

        from app.database import create_database_engine, database_url

        engine = create_database_engine(database_url)
        admin = create_engine(database_url)
        self.addCleanup(engine.dispose)
        self.addCleanup(admin.dispose)
        with engine.connect() as connection:
            backend_pid = connection.execute(text("SELECT pg_backend_pid()")).scalar()
        with admin.connect() as connection:
            connection.execute(
                text("SELECT pg_terminate_backend(:pid)"), {"pid": backend_pid}
            )

        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1")).scalar()

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
