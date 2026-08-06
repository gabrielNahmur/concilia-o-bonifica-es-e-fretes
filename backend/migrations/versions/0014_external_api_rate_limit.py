"""Persist the rolling lookup window for external registry APIs.

Revision ID: 0014_external_api_rate_limit
Revises: 0013_freight_origin_registry
"""

import sqlalchemy as sa
from alembic import op


revision = "0014_external_api_rate_limit"
down_revision = "0013_freight_origin_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_api_rate_limits",
        sa.Column("source", sa.String(length=40), primary_key=True),
        sa.Column("call_1_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("call_2_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("call_3_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(sa.text(
        "INSERT INTO external_api_rate_limits (source, updated_at) "
        "VALUES ('cnpj_ws', CURRENT_TIMESTAMP)"
    ))


def downgrade() -> None:
    op.drop_table("external_api_rate_limits")
