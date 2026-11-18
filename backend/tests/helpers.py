"""Test data shared by several test modules (K2).

Kept outside the test modules, so a module that only needs a fact does not import
another module's tests and the application it starts. Discovery skips this file,
because its name does not start with "test".
"""

from datetime import date
from decimal import Decimal


def reported_fact(**changes):
    """One stored SEC fact as the import writes it: Apple's 2020 net income of 100."""
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
