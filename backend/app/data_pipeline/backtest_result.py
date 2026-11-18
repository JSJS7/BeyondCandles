"""Save the backtest result with the data it came from (backtest plan, section 2).

The file records every ratio's quintiles with their companies, the seed of the random
portfolios, the Python version and the fingerprints of the verified prices and metric
history, so it is known which data and draws a published number comes from. `--check`
computes the result again and compares it with the file without writing it.
"""

import platform
from datetime import datetime, timezone

from app.data_pipeline.source_files import ROOT, write_json

RESULT = ROOT / "data/processed/backtest-result.json"


def save_backtest():
    """Verify the database, run the backtest from scratch and store the result."""
    from app.backtest import run_backtest
    from app.backtest_api import (
        END_DATE,
        RANKING_DATES,
        backtest_response,
        load_periods,
    )
    from app.database import engine
    from app.sic_codes import financial_tickers

    from .verification import verify_db

    # Fails when the stored prices or history differ from the verified sources.
    verification = verify_db()
    with engine.connect() as connection:
        periods = load_periods(connection, RANKING_DATES, END_DATE)
    result = run_backtest(periods, financial_tickers())
    response = backtest_response(result, periods, with_members=True)

    data = {
        "prices": verification["prices"],
        "price_fingerprint": verification["price_fingerprint"],
        "metric_versions": verification["metric_versions"],
        "metric_history_fingerprint": verification["metric_history_fingerprint"],
    }
    document = {
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # The random draws repeat across versions (plan, D7); recorded to show it.
        "python": platform.python_version(),
        "data": data,
        "result": response.model_dump(mode="json"),
    }
    write_json(RESULT, document)
    return {
        "status": "saved",
        "file": RESULT.relative_to(ROOT).as_posix(),
        "python": document["python"],
        "data": data,
        "ratios": len(response.ratios),
        "periods": len(response.catalogue),
    }
