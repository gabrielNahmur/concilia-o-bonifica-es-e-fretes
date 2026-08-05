"""Persist internal reconciliation information requests and answers.

Revision ID: 0012_reconciliation_information_requests
Revises: 0011_report_delivery_monitoring
"""

import sqlalchemy as sa
from alembic import op


revision = "0012_reconciliation_information_requests"
down_revision = "0011_report_delivery_monitoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_information_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reconciliation_id", sa.String(36), sa.ForeignKey("reconciliations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("reconciliation_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("reason_code", sa.String(50), nullable=False),
        sa.Column("request_notes", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(36), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_notes", sa.Text(), nullable=True),
        sa.Column("responded_by", sa.String(36), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(36), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("reconciliation_id", "item_id", "status", "requested_by", "requested_at", "responded_by", "closed_by", "closed_at"):
        op.create_index(
            f"ix_reconciliation_information_requests_{column}",
            "reconciliation_information_requests",
            [column],
        )
    op.create_index(
        "ix_reconciliation_information_request_item_status",
        "reconciliation_information_requests",
        ["item_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_reconciliation_information_request_item_status", table_name="reconciliation_information_requests")
    for column in ("closed_at", "closed_by", "responded_by", "requested_at", "requested_by", "status", "item_id", "reconciliation_id"):
        op.drop_index(f"ix_reconciliation_information_requests_{column}", table_name="reconciliation_information_requests")
    op.drop_table("reconciliation_information_requests")
