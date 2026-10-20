"""Company catalogue, source provenance and daily prices."""
from alembic import op
import sqlalchemy as sa

revision = "0001_prices"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("sector", sa.String(128)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("cik", sa.String(10), unique=True))
    op.create_table("data_sources",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False))
    op.create_table("import_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.String(128), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False))
    op.create_table("daily_prices",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        *[sa.Column(n, sa.Numeric(32, 18), nullable=False) for n in ("open", "high", "low", "close", "adjusted_close")],
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.String(128), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.CheckConstraint("open > 0 AND high > 0 AND low > 0 AND close > 0 AND adjusted_close > 0", name="ck_prices_positive"),
        sa.CheckConstraint("volume >= 0", name="ck_prices_volume"),
        sa.CheckConstraint("high + 0.000002 >= low AND high + 0.000002 >= open AND high + 0.000002 >= close AND low - 0.000002 <= open AND low - 0.000002 <= close", name="ck_prices_ohlc"))


def downgrade():
    op.drop_table("daily_prices")
    op.drop_table("import_runs")
    op.drop_table("data_sources")
    op.drop_table("companies")
