"""Immutable reconciliation review snapshots.

Revision ID: 0002_reconciliation_reviews
Revises: 0001_initial
"""
import sqlalchemy as sa
from alembic import op


revision = "0002_reconciliation_reviews"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 usa Base.metadata; em instalações novas ele pode já refletir o modelo
    # atual. Mantém a cadeia compatível tanto com bancos antigos quanto novos.
    if "reconciliation_reviews" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "reconciliation_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("reconciliation_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("expected_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("observed_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("difference_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reconciliation_id"], ["reconciliations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reconciliation_reviews_reconciliation_id", "reconciliation_reviews", ["reconciliation_id"])
    op.create_index("ix_reconciliation_reviews_action", "reconciliation_reviews", ["action"])
    op.create_index("ix_reconciliation_reviews_created_by", "reconciliation_reviews", ["created_by"])
    op.create_index("ix_reconciliation_reviews_created_at", "reconciliation_reviews", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_reconciliation_reviews_created_at", table_name="reconciliation_reviews")
    op.drop_index("ix_reconciliation_reviews_created_by", table_name="reconciliation_reviews")
    op.drop_index("ix_reconciliation_reviews_action", table_name="reconciliation_reviews")
    op.drop_index("ix_reconciliation_reviews_reconciliation_id", table_name="reconciliation_reviews")
    op.drop_table("reconciliation_reviews")
