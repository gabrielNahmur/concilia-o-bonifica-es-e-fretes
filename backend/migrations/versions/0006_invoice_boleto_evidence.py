"""Invoice boleto evidence for exact per-invoice discount reconciliation.

Revision ID: 0006_invoice_boleto_evidence
Revises: 0005_ipiranga_portal
"""

import sqlalchemy as sa
from alembic import op


revision = "0006_invoice_boleto_evidence"
down_revision = "0005_ipiranga_portal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "invoice_boleto_evidence" in inspector.get_table_names():
        return
    op.create_table(
        "invoice_boleto_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reconciliation_id", sa.String(36), nullable=False),
        sa.Column("unit_code", sa.String(3), nullable=False),
        sa.Column("company_code", sa.String(20), nullable=False),
        sa.Column("purchase_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("payable_document_id", sa.Integer(), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("source_file", sa.LargeBinary(), nullable=False),
        sa.Column("cedent_name", sa.String(200), nullable=True),
        sa.Column("payer_name", sa.String(200), nullable=True),
        sa.Column("payer_cnpj", sa.String(14), nullable=True),
        sa.Column("boleto_document_number", sa.String(80), nullable=True),
        sa.Column("invoice_number", sa.String(30), nullable=True),
        sa.Column("title_document_id", sa.String(60), nullable=True),
        sa.Column("nosso_numero", sa.String(40), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("gross_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("net_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("expected_discount_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("match_status", sa.String(30), nullable=False),
        sa.Column("match_basis", sa.Text(), nullable=False),
        sa.Column("parsed_text", sa.Text(), nullable=False),
        sa.Column("uploaded_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reconciliation_id"], ["reconciliations.id"], ondelete="CASCADE"),
    )
    for name, columns in (
        ("ix_invoice_boleto_evidence_reconciliation_id", ["reconciliation_id"]),
        ("ix_invoice_boleto_evidence_unit_code", ["unit_code"]),
        ("ix_invoice_boleto_evidence_company_code", ["company_code"]),
        ("ix_invoice_boleto_evidence_purchase_entry_id", ["purchase_entry_id"]),
        ("ix_invoice_boleto_evidence_payable_document_id", ["payable_document_id"]),
        ("ix_invoice_boleto_evidence_content_sha256", ["content_sha256"]),
        ("ix_invoice_boleto_evidence_payer_cnpj", ["payer_cnpj"]),
        ("ix_invoice_boleto_evidence_boleto_document_number", ["boleto_document_number"]),
        ("ix_invoice_boleto_evidence_invoice_number", ["invoice_number"]),
        ("ix_invoice_boleto_evidence_title_document_id", ["title_document_id"]),
        ("ix_invoice_boleto_evidence_match_status", ["match_status"]),
        ("ix_invoice_boleto_evidence_uploaded_by", ["uploaded_by"]),
        ("ix_invoice_boleto_evidence_created_at", ["created_at"]),
    ):
        op.create_index(name, "invoice_boleto_evidence", columns)


def downgrade() -> None:
    op.drop_table("invoice_boleto_evidence")
