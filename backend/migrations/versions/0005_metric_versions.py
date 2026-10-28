"""Add point-in-time versions of annual metric cells.

The table is derived: import-sec and build-history rebuild it from the stored facts
and the tracked rules, so dropping it on downgrade loses no source data.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_metric_versions"
down_revision = "0004_sec_history"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "annual_metric_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("known_from", sa.Date(), nullable=False),
        sa.Column("accession", sa.String(32), nullable=False),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("confirmation", sa.String(16), nullable=True),
        sa.Column("value", sa.Numeric(38, 10), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint(
            "company_id",
            "period_start",
            "period_end",
            "metric",
            "known_from",
            name="uq_metric_version",
        ),
        sa.CheckConstraint("period_start <= period_end", name="ck_version_period"),
        sa.CheckConstraint(
            "metric IN ('revenue', 'net_income', 'eps_diluted', 'equity', "
            "'operating_cash_flow')",
            name="ck_version_metric",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'needs_review', 'not_available')",
            name="ck_version_status",
        ),
        sa.CheckConstraint(
            "confirmation IN ('approved', 'identical', 'rounding', 'split')",
            name="ck_version_confirmation",
        ),
        sa.CheckConstraint(
            "(status = 'ready') = (value IS NOT NULL AND confirmation IS NOT NULL)",
            name="ck_version_ready",
        ),
    )


def downgrade():
    op.drop_table("annual_metric_versions")
