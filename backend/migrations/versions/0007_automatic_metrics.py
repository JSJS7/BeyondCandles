"""Allow automatically selected measures next to the reviewed ones.

Only constraints change: a source fact may now be stored under two metrics, because
preferred dividend tags are already stored as supporting positions of net income.
"""

from alembic import op

revision = "0007_automatic_metrics"
down_revision = "0006_reviewed_versions"
branch_labels = None
depends_on = None

AUTOMATIC_METRICS = (
    "'shares_diluted', 'assets', 'capex', 'operating_income', "
    "'preferred_value', 'preferred_dividends'"
)
REVIEWED_METRICS = (
    "'revenue', 'net_income', 'eps_diluted', 'equity', 'operating_cash_flow'"
)


def replace_check(name, condition):
    op.drop_constraint(name, "annual_metric_versions", type_="check")
    op.create_check_constraint(name, "annual_metric_versions", condition)


def replace_fact_key(columns):
    op.drop_constraint("uq_financial_fact", "financial_facts", type_="unique")
    op.create_unique_constraint("uq_financial_fact", "financial_facts", columns)


def upgrade():
    replace_fact_key(
        [
            "company_id",
            "source_cik",
            "metric",
            "taxonomy",
            "tag",
            "unit",
            "period_start",
            "period_end",
            "accession",
        ]
    )
    replace_check(
        "ck_version_metric", f"metric IN ({REVIEWED_METRICS}, {AUTOMATIC_METRICS})"
    )
    replace_check(
        "ck_version_confirmation",
        "confirmation IN ('approved', 'identical', 'rounding', 'split', 'reviewed', "
        "'automatic')",
    )


def downgrade():
    # The automatic rows cannot satisfy the old constraints. Facts come back from the
    # SEC cache with import-sec and versions with build-history on the newer code.
    op.execute(
        f"DELETE FROM annual_metric_versions WHERE metric IN ({AUTOMATIC_METRICS})"
    )
    op.execute(f"DELETE FROM financial_facts WHERE metric IN ({AUTOMATIC_METRICS})")
    replace_check(
        "ck_version_confirmation",
        "confirmation IN ('approved', 'identical', 'rounding', 'split', 'reviewed')",
    )
    replace_check("ck_version_metric", f"metric IN ({REVIEWED_METRICS})")
    replace_fact_key(
        [
            "company_id",
            "source_cik",
            "taxonomy",
            "tag",
            "unit",
            "period_start",
            "period_end",
            "accession",
        ]
    )
