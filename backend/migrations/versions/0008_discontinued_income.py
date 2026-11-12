"""Allow the automatic measure of net income from discontinued operations (M46).

Only the list of metric names a cell version may have changes; facts have no such list.
"""

from alembic import op

revision = "0008_discontinued_income"
down_revision = "0007_automatic_metrics"
branch_labels = None
depends_on = None

EARLIER_METRICS = (
    "'revenue', 'net_income', 'eps_diluted', 'equity', 'operating_cash_flow', "
    "'shares_diluted', 'assets', 'capex', 'operating_income', "
    "'preferred_value', 'preferred_dividends'"
)
NEW_METRIC = "'discontinued_income'"


def replace_metric_check(condition):
    op.drop_constraint("ck_version_metric", "annual_metric_versions", type_="check")
    op.create_check_constraint("ck_version_metric", "annual_metric_versions", condition)


def upgrade():
    replace_metric_check(f"metric IN ({EARLIER_METRICS}, {NEW_METRIC})")


def downgrade():
    # Its rows cannot satisfy the old constraint. Facts come back from the SEC cache
    # with import-sec and versions with build-history on the newer code.
    op.execute(f"DELETE FROM annual_metric_versions WHERE metric = {NEW_METRIC}")
    op.execute(f"DELETE FROM financial_facts WHERE metric = {NEW_METRIC}")
    replace_metric_check(f"metric IN ({EARLIER_METRICS})")
