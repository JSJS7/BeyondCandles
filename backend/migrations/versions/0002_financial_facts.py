"""Original SEC facts, retaining filing and retrieval versions."""
from alembic import op
import sqlalchemy as sa

revision = "0002_financial_facts"
down_revision = "0001_prices"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("financial_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("taxonomy", sa.String(32), nullable=False),
        sa.Column("tag", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("value", sa.Numeric(38, 10), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("filed", sa.Date(), nullable=False),
        sa.Column("accession", sa.String(32), nullable=False),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("fiscal_period", sa.String(16)),
        sa.Column("period_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.UniqueConstraint("company_id", "taxonomy", "tag", "unit", "period_start", "period_end", "accession", "source_id", name="uq_financial_fact"),
        sa.CheckConstraint("period_start <= period_end", name="ck_fact_period"))
    op.create_index("ix_financial_facts_company_id", "financial_facts", ["company_id"])


def downgrade():
    op.drop_table("financial_facts")
