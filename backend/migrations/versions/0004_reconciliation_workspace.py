"""Granular reconciliation items, evidence allocations and exception queue.

Revision ID: 0004_reconciliation_workspace
Revises: 0003_reconciliation_sources
"""

import sqlalchemy as sa
from alembic import op


revision = "0004_reconciliation_workspace"
down_revision = "0003_reconciliation_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    reconciliation_columns = {column["name"] for column in inspector.get_columns("reconciliations")}
    if "confirmation_mode" not in reconciliation_columns:
        op.add_column("reconciliations", sa.Column("confirmation_mode", sa.String(20), nullable=False, server_default="none"))
    if "auto_confirmed_at" not in reconciliation_columns:
        op.add_column("reconciliations", sa.Column("auto_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    if "algorithm_version" not in reconciliation_columns:
        op.add_column("reconciliations", sa.Column("algorithm_version", sa.String(30), nullable=True))
    reconciliation_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("reconciliations")}
    if "ix_reconciliations_confirmation_mode" not in reconciliation_indexes:
        op.create_index("ix_reconciliations_confirmation_mode", "reconciliations", ["confirmation_mode"])

    if "reconciliation_items" not in inspector.get_table_names():
        op.create_table(
        "reconciliation_items",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("reconciliation_id", sa.String(36), nullable=False),
        sa.Column("item_type", sa.String(40), nullable=False),
        sa.Column("source_key", sa.String(180), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=True),
        sa.Column("source_document", sa.String(80), nullable=True),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("expected_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("observed_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("difference_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("automatic_eligible", sa.Boolean(), nullable=False),
        sa.Column("automatic_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("policy_reason", sa.Text(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reconciliation_id"], ["reconciliations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reconciliation_id", "source_key", name="uq_reconciliation_item_source"),
    )
        for name, columns in (
        ("ix_reconciliation_items_reconciliation_id", ["reconciliation_id"]),
        ("ix_reconciliation_items_item_type", ["item_type"]),
        ("ix_reconciliation_items_source_date", ["source_date"]),
        ("ix_reconciliation_items_source_document", ["source_document"]),
        ("ix_reconciliation_items_status", ["status"]),
        ("ix_reconciliation_items_automatic_eligible", ["automatic_eligible"]),
        ("ix_reconciliation_items_review_status", ["review_status"]),
        ("ix_reconciliation_items_reviewed_by", ["reviewed_by"]),
        ("ix_reconciliation_item_rec_status", ["reconciliation_id", "status"]),
    ):
            op.create_index(name, "reconciliation_items", columns)

    if "reconciliation_evidence" not in inspector.get_table_names():
        op.create_table(
        "reconciliation_evidence",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_key", sa.String(220), nullable=False),
        sa.Column("evidence_date", sa.Date(), nullable=True),
        sa.Column("unit_code", sa.String(3), nullable=True),
        sa.Column("document_id", sa.String(80), nullable=True),
        sa.Column("value", sa.Numeric(18, 2), nullable=False),
        sa.Column("counted", sa.Boolean(), nullable=False),
        sa.Column("identity_json", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_key", name="uq_reconciliation_evidence_source"),
    )
        for name, columns in (
        ("ix_reconciliation_evidence_source_type", ["source_type"]),
        ("ix_reconciliation_evidence_evidence_date", ["evidence_date"]),
        ("ix_reconciliation_evidence_unit_code", ["unit_code"]),
        ("ix_reconciliation_evidence_document_id", ["document_id"]),
    ):
            op.create_index(name, "reconciliation_evidence", columns)

    if "reconciliation_allocations" not in inspector.get_table_names():
        op.create_table(
        "reconciliation_allocations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("evidence_id", sa.String(36), nullable=False),
        sa.Column("allocated_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("match_status", sa.String(30), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("match_basis", sa.Text(), nullable=True),
        sa.Column("algorithm_version", sa.String(30), nullable=False),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["reconciliation_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["reconciliation_evidence.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "evidence_id", name="uq_reconciliation_allocation"),
    )
        op.create_index("ix_reconciliation_allocations_item_id", "reconciliation_allocations", ["item_id"])
        op.create_index("ix_reconciliation_allocations_evidence_id", "reconciliation_allocations", ["evidence_id"])
        op.create_index("ix_reconciliation_allocations_match_status", "reconciliation_allocations", ["match_status"])

    if "reconciliation_exceptions" not in inspector.get_table_names():
        op.create_table(
        "reconciliation_exceptions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("reconciliation_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=True),
        sa.Column("source_key", sa.String(220), nullable=False),
        sa.Column("exception_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("title", sa.String(220), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expected_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("observed_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("difference_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("assigned_to", sa.String(36), nullable=True),
        sa.Column("resolution_code", sa.String(50), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(36), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reconciliation_id"], ["reconciliations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["reconciliation_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reconciliation_id", "source_key", name="uq_reconciliation_exception_source"),
    )
        for name, columns in (
        ("ix_reconciliation_exceptions_reconciliation_id", ["reconciliation_id"]),
        ("ix_reconciliation_exceptions_item_id", ["item_id"]),
        ("ix_reconciliation_exceptions_exception_type", ["exception_type"]),
        ("ix_reconciliation_exceptions_severity", ["severity"]),
        ("ix_reconciliation_exceptions_status", ["status"]),
        ("ix_reconciliation_exceptions_assigned_to", ["assigned_to"]),
        ("ix_reconciliation_exception_queue", ["status", "severity", "exception_type"]),
    ):
            op.create_index(name, "reconciliation_exceptions", columns)


def downgrade() -> None:
    op.drop_table("reconciliation_exceptions")
    op.drop_table("reconciliation_allocations")
    op.drop_table("reconciliation_evidence")
    op.drop_table("reconciliation_items")
    op.drop_index("ix_reconciliations_confirmation_mode", table_name="reconciliations")
    op.drop_column("reconciliations", "algorithm_version")
    op.drop_column("reconciliations", "auto_confirmed_at")
    op.drop_column("reconciliations", "confirmation_mode")
