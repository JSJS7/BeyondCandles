"""Verify the shared reading of tracked policy files used by every metric."""

import importlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.policy_files import load_policy


def write_policy(path, document, modified_ns):
    path.write_text(json.dumps(document), encoding="utf-8")
    # An explicit timestamp makes the cache key change even on coarse file clocks.
    os.utime(path, ns=(modified_ns, modified_ns))


class PolicyFileTests(unittest.TestCase):
    def test_missing_file_is_configuration_error(self):
        """A missing policy must fail loudly instead of turning every cell into needs_review."""
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-rules.json"

            with self.assertRaises(FileNotFoundError):
                load_policy(missing, "test")

    def test_every_metric_rejects_missing_policy(self):
        """Regression: EPS, equity and OCF used to return {} for a missing file."""
        accessors = (
            ("app.revenue", "revenue_rules"),
            ("app.net_income", "net_income_rules"),
            ("app.eps", "eps_rules"),
            ("app.equity", "equity_rules"),
            ("app.equity", "equity_issues"),
            ("app.operating_cash_flow", "cash_flow_rules"),
            ("app.fiscal_periods", "fiscal_rules"),
        )
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-rules.json"
            for module_name, accessor_name in accessors:
                module = importlib.import_module(module_name)
                accessor = getattr(module, accessor_name)
                with self.subTest(accessor=accessor_name):
                    with patch(f"{module_name}.RULES_PATH", missing):
                        with self.assertRaises(FileNotFoundError):
                            accessor()

    def test_unsupported_version_is_rejected(self):
        """Only the declared format versions are accepted."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            write_policy(path, {"version": 3, "periods": {}}, 1_000_000_000)

            with self.assertRaisesRegex(ValueError, "Unsupported test policy version"):
                load_policy(path, "test", versions=(1, 2))

    def test_replaced_file_is_read_again(self):
        """A rewritten policy (new modification time) must not be served from the cache."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            write_policy(path, {"version": 1, "periods": {"a": 1}}, 1_000_000_000)
            first = load_policy(path, "test")

            write_policy(path, {"version": 1, "periods": {"b": 2}}, 2_000_000_000)
            second = load_policy(path, "test")

        self.assertEqual(first["periods"], {"a": 1})
        self.assertEqual(second["periods"], {"b": 2})


if __name__ == "__main__":
    unittest.main()
