"""Ipiranga portal statements and invoice issue date.

Revision ID: 0005_ipiranga_portal
Revises: 0004_reconciliation_workspace
"""

import sqlalchemy as sa
from alembic import op


revision = "0005_ipiranga_portal"
down_revision = "0004_reconciliation_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    purchase_columns = {column["name"] for column in inspector.get_columns("purchases")}
    if "invoice_issue_date" not in purchase_columns:
        op.add_column("purchases", sa.Column("invoice_issue_date", sa.Date(), nullable=True))
    purchase_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("purchases")}
    if "ix_purchases_invoice_issue_date" not in purchase_indexes:
        op.create_index("ix_purchases_invoice_issue_date", "purchases", ["invoice_issue_date"])
    if "ix_purchase_unit_issue_company" not in purchase_indexes:
        op.create_index(
            "ix_purchase_unit_issue_company",
            "purchases",
            ["unit_code", "invoice_issue_date", "mapped_company_code"],
        )

    tables = set(inspector.get_table_names())
    if "portal_statement_imports" not in tables:
        op.create_table(
            "portal_statement_imports",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("unit_code", sa.String(3), nullable=False),
            sa.Column("company_code", sa.String(20), nullable=False),
            sa.Column("category", sa.String(40), nullable=False),
            sa.Column("client_cnpj", sa.String(14), nullable=True),
            sa.Column("period_start", sa.Date(), nullable=True),
            sa.Column("period_end", sa.Date(), nullable=True),
            sa.Column("original_filename", sa.String(255), nullable=False),
            sa.Column("content_type", sa.String(120), nullable=True),
            sa.Column("content_sha256", sa.String(64), nullable=False, unique=True),
            sa.Column("source_file", sa.LargeBinary(), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False),
            sa.Column("imported_count", sa.Integer(), nullable=False),
            sa.Column("duplicate_count", sa.Integer(), nullable=False),
            sa.Column("uploaded_by", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name, columns in (
            ("ix_portal_statement_imports_unit_code", ["unit_code"]),
            ("ix_portal_statement_imports_company_code", ["company_code"]),
            ("ix_portal_statement_imports_category", ["category"]),
            ("ix_portal_statement_imports_content_sha256", ["content_sha256"]),
            ("ix_portal_statement_imports_uploaded_by", ["uploaded_by"]),
            ("ix_portal_statement_imports_created_at", ["created_at"]),
        ):
            op.create_index(name, "portal_statement_imports", columns)

    if "portal_bonus_events" not in tables:
        op.create_table(
            "portal_bonus_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("unit_code", sa.String(3), nullable=False),
            sa.Column("company_code", sa.String(20), nullable=False),
            sa.Column("category", sa.String(40), nullable=False),
            sa.Column("portal_date", sa.Date(), nullable=False),
            sa.Column("value", sa.Numeric(18, 2), nullable=False),
            sa.Column("product", sa.String(120), nullable=True),
            sa.Column("description", sa.String(160), nullable=True),
            sa.Column("reference", sa.String(160), nullable=True),
            sa.Column("client_cnpj", sa.String(14), nullable=True),
            sa.Column("event_key", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name, columns in (
            ("ix_portal_bonus_events_unit_code", ["unit_code"]),
            ("ix_portal_bonus_events_company_code", ["company_code"]),
            ("ix_portal_bonus_events_category", ["category"]),
            ("ix_portal_bonus_events_portal_date", ["portal_date"]),
            ("ix_portal_bonus_events_event_key", ["event_key"]),
        ):
            op.create_index(name, "portal_bonus_events", columns)

    if "portal_bonus_event_sources" not in tables:
        op.create_table(
            "portal_bonus_event_sources",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("import_id", sa.String(36), nullable=False),
            sa.Column("event_id", sa.String(36), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("raw_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["import_id"], ["portal_statement_imports.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["event_id"], ["portal_bonus_events.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("import_id", "event_id", name="uq_portal_import_event"),
        )
        op.create_index("ix_portal_bonus_event_sources_import_id", "portal_bonus_event_sources", ["import_id"])
        op.create_index("ix_portal_bonus_event_sources_event_id", "portal_bonus_event_sources", ["event_id"])

    if "portal_bonus_matches" not in tables:
        op.create_table(
            "portal_bonus_matches",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("event_id", sa.String(36), nullable=False, unique=True),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("movement_key", sa.String(220), nullable=True),
            sa.Column("payable_document_id", sa.Integer(), nullable=True),
            sa.Column("purchase_entry_id", sa.BigInteger(), nullable=True),
            sa.Column("financial_entry_id", sa.Integer(), nullable=True),
            sa.Column("date_difference_days", sa.Integer(), nullable=True),
            sa.Column("match_basis", sa.Text(), nullable=False),
            sa.Column("details_json", sa.Text(), nullable=False),
            sa.Column("algorithm_version", sa.String(30), nullable=False),
            sa.Column("matched_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["event_id"], ["portal_bonus_events.id"], ondelete="CASCADE"),
        )
        for name, columns in (
            ("ix_portal_bonus_matches_event_id", ["event_id"]),
            ("ix_portal_bonus_matches_status", ["status"]),
            ("ix_portal_bonus_matches_movement_key", ["movement_key"]),
            ("ix_portal_bonus_matches_payable_document_id", ["payable_document_id"]),
            ("ix_portal_bonus_matches_purchase_entry_id", ["purchase_entry_id"]),
            ("ix_portal_bonus_matches_financial_entry_id", ["financial_entry_id"]),
        ):
            op.create_index(name, "portal_bonus_matches", columns)


def downgrade() -> None:
    op.drop_table("portal_bonus_matches")
    op.drop_table("portal_bonus_event_sources")
    op.drop_table("portal_bonus_events")
    op.drop_table("portal_statement_imports")
    op.drop_index("ix_purchase_unit_issue_company", table_name="purchases")
    op.drop_index("ix_purchases_invoice_issue_date", table_name="purchases")
    op.drop_column("purchases", "invoice_issue_date")
