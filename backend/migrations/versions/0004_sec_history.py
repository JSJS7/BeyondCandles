"""Retain source issuers and separate raw facts from the selected financial history."""

import sqlalchemy as sa
from alembic import op

revision = "0004_sec_history"
down_revision = "0003_remove_import_metadata"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "companies", sa.Column("financial_history_note", sa.Text(), nullable=True)
    )
    op.add_column(
        "financial_facts", sa.Column("source_cik", sa.String(10), nullable=True)
    )
    # Existing facts were imported from the company's single primary SEC issuer.
    op.execute("""
        UPDATE financial_facts AS fact
        SET source_cik = company.cik
        FROM companies AS company
        WHERE fact.company_id = company.id
    """)
    # Missing issuer identities abort the migration; they must not be guessed.
    op.alter_column("financial_facts", "source_cik", nullable=False)
    op.add_column(
        "financial_facts",
        sa.Column(
            "history_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.drop_constraint("uq_financial_fact", "financial_facts", type_="unique")
    op.create_unique_constraint(
        "uq_financial_fact",
        "financial_facts",
        [
            "company_id",
            "source_cik",
            "taxonomy",
            "tag",
            "unit",
            "period_start",
            "period_end",
            "accession",
        ],
    )


def downgrade():
    # Reinstating the old key fails safely if different issuers would collide.
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
    op.drop_column("financial_facts", "history_eligible")
    op.drop_column("financial_facts", "source_cik")
    op.drop_column("companies", "financial_history_note")
