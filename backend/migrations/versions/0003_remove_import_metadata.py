"""Keep dataset provenance in the manifest; remove database import bookkeeping."""

from alembic import op

revision = "0003_remove_import_metadata"
down_revision = "0002_financial_facts"
branch_labels = None
depends_on = None


def upgrade():
    # Create the filing-based key first: unexpected duplicates abort the transaction.
    # No price or financial-fact records are deleted or rewritten.
    op.drop_constraint("uq_financial_fact", "financial_facts", type_="unique")
    op.create_unique_constraint(
        "uq_financial_fact",
        "financial_facts",
        [
            "company_id",
            "taxonomy",
            "tag",
            "unit",
            "period_start",
            "period_end",
            "accession",
        ],
    )
    op.drop_column("daily_prices", "source_id")
    op.drop_column("financial_facts", "source_id")
    op.drop_table("import_runs")
    op.drop_table("data_sources")


def downgrade():
    raise RuntimeError(
        "Import bookkeeping was intentionally removed and cannot be reconstructed. "
        "Restore a pre-migration backup to return to revision 0002_financial_facts."
    )
