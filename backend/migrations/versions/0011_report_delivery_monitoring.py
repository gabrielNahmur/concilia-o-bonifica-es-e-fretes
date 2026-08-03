"""Track provider delivery events and controlled report retries.

Revision ID: 0011_report_delivery_monitoring
Revises: 0010_supplemental_freight_ctes
"""

import sqlalchemy as sa
from alembic import op


revision = "0011_report_delivery_monitoring"
down_revision = "0010_supplemental_freight_ctes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_delivery_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("report_run_id", sa.String(36), sa.ForeignKey("report_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(120), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="sent"),
        sa.Column("provider_event", sa.String(60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_report_delivery_attempts_report_run_id", "report_delivery_attempts", ["report_run_id"])
    op.create_index("ix_report_delivery_attempts_provider_message_id", "report_delivery_attempts", ["provider_message_id"])
    op.create_index("ix_report_delivery_attempts_status", "report_delivery_attempts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_report_delivery_attempts_status", table_name="report_delivery_attempts")
    op.drop_index("ix_report_delivery_attempts_provider_message_id", table_name="report_delivery_attempts")
    op.drop_index("ix_report_delivery_attempts_report_run_id", table_name="report_delivery_attempts")
    op.drop_table("report_delivery_attempts")
