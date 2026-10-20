import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from app.data_pipeline.prices import sha256, validate_prices


class ArchiveTests(unittest.TestCase):
    def run_archive(self, fault=None):
        """Build a small source archive and verify successful output or safe rejection."""
        with tempfile.TemporaryDirectory(prefix="beyondcandles-archive-test-") as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            selection = {"company_count": 200, "companies": [{"Symbol": ticker} for ticker in ["AAPL"] + [f"T{i:03}" for i in range(199)]]}
            selection_path = data / "companies-200.json"
            selection_path.write_text(json.dumps(selection), encoding="utf-8")
            archive = root / "source.zip"
            stock_csv, company_csv = io.StringIO(), io.StringIO()
            prices, companies = csv.writer(stock_csv), csv.writer(company_csv)
            prices.writerow(["Date", "Symbol", "Open", "High", "Low", "Close", "Adj Close", "Volume"])
            companies.writerow(["Symbol", "Longname", "Exchange", "Sector"])
            for c in selection["companies"]:
                ticker = c["Symbol"]
                companies.writerow([ticker, ticker + " Inc.", "NMS", "Technology"])
                for day in ("2020-01-02", "2020-01-03"):
                    if fault == "missing" and ticker == "T198" and day == "2020-01-03":
                        continue
                    prices.writerow([day, ticker, "10", "12", "9", "11", "5", "100"])
            if fault == "duplicate":
                prices.writerow(["2020-01-02", "AAPL", "10", "12", "9", "11", "5", "100"])
            with ZipFile(archive, "w") as z:
                z.writestr("sp500_stocks.csv", stock_csv.getvalue())
                z.writestr("sp500_companies.csv", company_csv.getvalue())
            manifest = dict(archive="source.zip", sha256=sha256(archive), selection_sha256=sha256(selection_path), period_start="2020-01-02", period_end="2020-01-03", trading_dates=2, price_rows=400, calendar_sha256=hashlib.sha256(b"2020-01-02\n2020-01-03").hexdigest())
            if fault == "checksum":
                manifest["sha256"] = "0" * 64
            manifest_path = data / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = data / "processed/prices-200.csv"
            output.parent.mkdir()
            output.write_text("previous valid artifact", encoding="utf-8")
            with patch("app.data_pipeline.prices.ROOT", root), patch("app.data_pipeline.prices.MANIFEST", manifest_path), patch("app.data_pipeline.prices.SELECTION", selection_path):
                if fault:
                    with self.assertRaises(ValueError):
                        validate_prices()
                    self.assertEqual(output.read_text(), "previous valid artifact")
                else:
                    _, _, result = validate_prices()
                    self.assertEqual(result["prices"], 400)

    def test_valid_archive(self):
        """Prepare all 400 prices when every selected company has both expected dates."""
        self.run_archive()

    def test_duplicate_rejected_before_publishing(self):
        """Reject a repeated company/date row without replacing the previous valid CSV."""
        self.run_archive("duplicate")

    def test_missing_date_rejected_before_publishing(self):
        """Reject an incomplete company history and preserve the previous valid CSV."""
        self.run_archive("missing")

    def test_checksum_mismatch_rejected(self):
        """Reject an archive whose checksum differs from the pinned manifest."""
        self.run_archive("checksum")
