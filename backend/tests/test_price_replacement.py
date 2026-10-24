"""Opt-in replacement tests run against a disposable migrated PostgreSQL database."""

import csv
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.data_pipeline import price_replacement as replacement
from app.data_pipeline.benjaminpo import atomic_json
from app.data_pipeline.source_files import read_json, sha256
from app.data_pipeline.validation import FIELDS


@unittest.skipUnless(
    os.environ.get("BC_RUN_DB_TESTS") == "1",
    "Set BC_RUN_DB_TESTS=1 to use an isolated test database",
)
class PriceReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Create and migrate a random test database; never target the application DB."""
        from psycopg import sql
        from sqlalchemy import create_engine

        from app.database import engine

        cls.name = "beyondcandles_test_" + uuid4().hex
        cls.admin = create_engine(engine.url, isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as connection:
            driver = connection.connection.driver_connection
            assert driver is not None
            with driver.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name))
                )
        cls.db = create_engine(engine.url.set(database=cls.name))
        result = subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, POSTGRES_DB=cls.name),
            capture_output=True,
            text=True,
        )
        if result.returncode:
            cls.tearDownClass()
            raise AssertionError("Could not migrate the isolated test database")

    @classmethod
    def tearDownClass(cls):
        """Remove only the randomly named database owned by this test class."""
        from psycopg import sql

        cls.db.dispose()
        assert cls.name.startswith("beyondcandles_test_") and len(cls.name) == 51
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

    def setUp(self):
        from sqlalchemy import text

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.active = self.root / "manifest.json"
        self.candidate = self.root / "candidate.json"
        self.path = self.root / "prices.csv"
        self.old_manifest = {"id": "old-source", "sha256": "a" * 64}
        atomic_json(self.active, self.old_manifest)
        atomic_json(self.candidate, {"id": "candidate-source", "sha256": "b" * 64})
        with self.db.begin() as connection:
            connection.execute(text("DELETE FROM financial_facts"))
            connection.execute(text("DELETE FROM daily_prices"))
            connection.execute(text("DELETE FROM companies"))
            connection.execute(
                text(
                    "INSERT INTO companies (ticker,name,exchange,currency,cik) VALUES ('AAPL','Apple Inc.','NMS','USD','0000320193')"
                )
            )
            connection.execute(
                text("""
                INSERT INTO daily_prices (company_id,date,open,high,low,close,adjusted_close,volume)
                SELECT id,day::date,10,12,9,11,5,100 FROM companies
                CROSS JOIN (VALUES ('2020-01-02'),('2020-01-03')) dates(day)
            """)
            )
            connection.execute(
                text("""
                INSERT INTO financial_facts (company_id,source_cik,metric,taxonomy,tag,unit,value,
                    period_start,period_end,filed,accession,form,fiscal_period,period_kind)
                SELECT id,'0000320193','net_income','us-gaap','NetIncomeLoss','USD',123,
                    '2019-01-01','2019-12-31','2020-02-01','fixture','10-K','FY','annual'
                FROM companies
            """)
            )
            self.old = replacement.database_fingerprint(connection)
            self.other = replacement.other_data_fingerprint(connection)
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(FIELDS)
            for day in ("2020-01-02", "2020-01-03"):
                writer.writerow(["AAPL", day, 20, 24, 18, 22, 21, 200])
        self.target = replacement.file_fingerprint(self.path)
        for target, attribute, value in (
            (replacement, "MANIFEST", self.active),
            (replacement, "CANDIDATE", self.candidate),
            (replacement, "ROOT", self.root),
            (replacement, "BACKUPS", self.root / "backups"),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("app.database.engine", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(
            replacement,
            "prepare_prices",
            return_value=(
                self.path,
                {},
                {
                    "prices": 2,
                    "manifest_sha256": sha256(self.candidate),
                    "processed_sha256": sha256(self.path),
                },
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_state(self, prices, manifest):
        with self.db.connect() as connection:
            self.assertEqual(replacement.database_fingerprint(connection), prices)
            self.assertEqual(replacement.other_data_fingerprint(connection), self.other)
        self.assertEqual(read_json(self.active), manifest)

    def test_replace_repeat_and_restore_preserve_company_ids_and_sec(self):
        """Full replacement is repeatable; its verified backup restores every old value."""
        result = replacement.replace_prices(self.old[1])
        self.assertEqual(result["updated"], 2)
        self.assert_state(self.target, read_json(self.candidate))
        repeated = replacement.replace_prices(self.old[1])
        self.assertEqual(repeated["updated"], 0)
        backup = self.root / result["backup"]
        self.assertEqual(replacement.file_fingerprint(backup), self.old)
        restored = replacement.restore_prices(backup, self.target[1])
        self.assertEqual(restored["updated"], 2)
        self.assert_state(self.old, self.old_manifest)
        repeated_restore = replacement.restore_prices(backup, self.target[1])
        self.assertEqual(repeated_restore["updated"], 0)

    def test_unexpected_fingerprint_rejects_replacement_before_backup(self):
        """A stale expected fingerprint cannot authorize replacing different stored data."""
        with self.assertRaisesRegex(ValueError, "changed since verification"):
            replacement.replace_prices("f" * 64)
        self.assert_state(self.old, self.old_manifest)
        self.assertFalse((self.root / "backups").exists())

    def test_failure_after_update_rolls_back_all_rows_and_manifest(self):
        """An error after UPDATE preserves the full old snapshot and a usable backup."""
        original_update = replacement.update_staged_prices

        def update_then_fail(connection):
            original_update(connection)
            raise ValueError("Simulated post-update failure")

        with patch.object(
            replacement, "update_staged_prices", side_effect=update_then_fail
        ):
            with self.assertRaisesRegex(ValueError, "post-update failure"):
                replacement.replace_prices(self.old[1])
        self.assert_state(self.old, self.old_manifest)
        backups = list((self.root / "backups").glob("*.csv"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(replacement.file_fingerprint(backups[0]), self.old)

    def test_manifest_activation_can_resume_after_database_commit(self):
        """Retry finishes activation after COMMIT without a second UPDATE or new backup."""
        with patch.object(
            replacement,
            "activate_manifest",
            side_effect=OSError("Simulated disk failure"),
        ):
            with self.assertRaisesRegex(OSError, "disk failure"):
                replacement.replace_prices(self.old[1])
        self.assert_state(self.target, self.old_manifest)
        result = replacement.replace_prices(self.old[1])
        self.assertEqual(result["updated"], 0)
        self.assert_state(self.target, read_json(self.candidate))

    def test_changed_date_keys_are_rejected_without_partial_replacement(self):
        """A missing session cannot silently delete an existing company/date key."""
        rows = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text("\n".join(rows[:2]) + "\n", encoding="utf-8")
        summary = {
            "prices": 2,
            "manifest_sha256": sha256(self.candidate),
            "processed_sha256": sha256(self.path),
        }
        with patch.object(
            replacement, "prepare_prices", return_value=(self.path, {}, summary)
        ):
            with self.assertRaisesRegex(ValueError, "exact stored company/date keys"):
                replacement.replace_prices(self.old[1])
        self.assert_state(self.old, self.old_manifest)

    def test_unreviewed_candidate_never_opens_replacement_transaction(self):
        """Economic review is mandatory even when the caller knows the stored hash."""
        with patch.object(
            replacement, "prepare_prices", side_effect=ValueError("Unresolved DHR")
        ):
            with self.assertRaisesRegex(ValueError, "Unresolved DHR"):
                replacement.replace_prices(self.old[1])
        self.assert_state(self.old, self.old_manifest)

    def test_modified_prepared_csv_is_rejected_before_opening_transaction(self):
        """Validated bytes cannot be swapped between preparation and replacement."""
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("unexpected changed bytes\n")
        with self.assertRaisesRegex(ValueError, "changed since validation"):
            replacement.replace_prices(self.old[1])
        self.assert_state(self.old, self.old_manifest)

    def test_corrupt_backup_cannot_overwrite_current_prices(self):
        """Restore verifies backup bytes before changing prices or the active manifest."""
        result = replacement.replace_prices(self.old[1])
        backup = self.root / result["backup"]
        with backup.open("a", encoding="utf-8") as handle:
            handle.write("changed backup bytes\n")
        with self.assertRaisesRegex(ValueError, "backup checksum mismatch"):
            replacement.restore_prices(backup, self.target[1])
        self.assert_state(self.target, read_json(self.candidate))
