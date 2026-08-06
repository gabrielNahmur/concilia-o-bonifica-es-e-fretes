"""Cache registered origins for freight suppliers.

Revision ID: 0013_freight_origin_registry
Revises: 0012_reconciliation_information_requests
"""

import sqlalchemy as sa
from alembic import op


revision = "0013_freight_origin_registry"
down_revision = "0012_reconciliation_information_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "freight_origins",
        sa.Column("cnpj", sa.String(length=14), primary_key=True),
        sa.Column("legal_name", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=True),
        sa.Column("last_lookup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(cnpj) = 14 AND length(replace(replace(replace(replace(replace("
            "replace(replace(replace(replace(replace(cnpj, '0', ''), '1', ''), '2', ''), "
            "'3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', '')) = 0",
            name="ck_freight_origins_cnpj_digits",
        ),
    )
    op.create_index("ix_freight_origins_next_retry_at", "freight_origins", ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_freight_origins_next_retry_at", table_name="freight_origins")
    op.drop_table("freight_origins")
