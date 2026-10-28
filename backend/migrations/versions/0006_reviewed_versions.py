"""Allow point-in-time versions confirmed by a statement review of their own filing.

Only the check constraint changes; the derived rows are rebuilt by build-history.
"""

from alembic import op

revision = "0006_reviewed_versions"
down_revision = "0005_metric_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "ck_version_confirmation", "annual_metric_versions", type_="check"
    )
    op.create_check_constraint(
        "ck_version_confirmation",
        "annual_metric_versions",
        "confirmation IN ('approved', 'identical', 'rounding', 'split', 'reviewed')",
    )


def downgrade():
    # Reviewed versions cannot satisfy the old constraint; they become unchecked
    # again (needs_review) after the next build-history on the old code.
    op.execute("DELETE FROM annual_metric_versions WHERE confirmation = 'reviewed'")
    op.drop_constraint(
        "ck_version_confirmation", "annual_metric_versions", type_="check"
    )
    op.create_check_constraint(
        "ck_version_confirmation",
        "annual_metric_versions",
        "confirmation IN ('approved', 'identical', 'rounding', 'split')",
    )
