"""Freight reconciliation snapshot, rates and human reviews.

Revision ID: 0008_freight_reconciliation
Revises: 0007_ipiranga_supplemental_review
"""

import sqlalchemy as sa
from alembic import op


revision = "0008_freight_reconciliation"
down_revision = "0007_ipiranga_supplemental_review"
branch_labels = None
depends_on = None


UNIT_CNPJS = {
    "001": "90589698000115",
    "002": "90589698000204",
    "003": "90589698000387",
    "004": "90589698000468",
    "006": "90589698000700",
    "007": "90589698000620",
    "008": "90589698000891",
    "012": "90589698000972",
    "013": "90589698001197",
    "014": "90589698001278",
}


def upgrade() -> None:
    op.add_column("units", sa.Column("cnpj", sa.String(14), nullable=True))
    op.create_index("ix_units_cnpj", "units", ["cnpj"], unique=True)
    op.add_column("payable_documents", sa.Column("erp_cte_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_payable_documents_erp_cte_id", "payable_documents", ["erp_cte_id"])

    op.create_table(
        "freight_ctes",
        sa.Column("erp_cte_id", sa.BigInteger(), primary_key=True),
        sa.Column("cte_number", sa.Integer(), nullable=False),
        sa.Column("series", sa.String(5), nullable=True),
        sa.Column("access_key", sa.String(44), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("unit_code", sa.String(3), sa.ForeignKey("units.code"), nullable=False),
        sa.Column("destination_cnpj", sa.String(14), nullable=False),
        sa.Column("carrier_person_id", sa.Integer(), nullable=True),
        sa.Column("carrier_cnpj", sa.String(14), nullable=False),
        sa.Column("carrier_name", sa.String(200), nullable=True),
        sa.Column("sender_cnpj", sa.String(14), nullable=True),
        sa.Column("sender_name", sa.String(200), nullable=True),
        sa.Column("purpose", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_status", sa.String(10), nullable=True),
        sa.Column("is_canceled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("charged_service_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("charged_receivable_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("charged_net_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("cargo_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("cargo_liters", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("cargo_json", sa.Text(), nullable=True),
        sa.Column("erp_updated_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_freight_ctes_cte_number", ["cte_number"]),
        ("ix_freight_ctes_access_key", ["access_key"]),
        ("ix_freight_ctes_issue_date", ["issue_date"]),
        ("ix_freight_ctes_unit_code", ["unit_code"]),
        ("ix_freight_ctes_destination_cnpj", ["destination_cnpj"]),
        ("ix_freight_ctes_carrier_cnpj", ["carrier_cnpj"]),
        ("ix_freight_ctes_sender_cnpj", ["sender_cnpj"]),
        ("ix_freight_ctes_is_canceled", ["is_canceled"]),
        ("ix_freight_ctes_source_active", ["source_active"]),
        ("ix_freight_cte_month_unit", ["issue_date", "unit_code"]),
        ("ix_freight_cte_carrier_date", ["carrier_cnpj", "issue_date"]),
    ):
        op.create_index(name, "freight_ctes", columns)

    op.create_table(
        "freight_rates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("carrier_cnpj", sa.String(14), nullable=False),
        sa.Column("carrier_name", sa.String(200), nullable=True),
        sa.Column("origin_cnpj", sa.String(14), nullable=True),
        sa.Column("unit_code", sa.String(3), sa.ForeignKey("units.code"), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("rate_per_liter", sa.Numeric(12, 6), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_freight_rates_carrier_cnpj", ["carrier_cnpj"]),
        ("ix_freight_rates_origin_cnpj", ["origin_cnpj"]),
        ("ix_freight_rates_unit_code", ["unit_code"]),
        ("ix_freight_rates_effective_from", ["effective_from"]),
        ("ix_freight_rates_effective_to", ["effective_to"]),
        ("ix_freight_rates_active", ["active"]),
        ("ix_freight_rate_lookup", ["carrier_cnpj", "unit_code", "origin_cnpj", "effective_from"]),
    ):
        op.create_index(name, "freight_rates", columns)

    op.create_table(
        "freight_cte_invoices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("erp_cte_id", sa.BigInteger(), sa.ForeignKey("freight_ctes.erp_cte_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("reference_access_key", sa.String(44), nullable=True),
        sa.Column("resolved_purchase_entry_id", sa.BigInteger(), sa.ForeignKey("purchases.erp_entry_id", ondelete="SET NULL"), nullable=True),
        sa.Column("candidate_purchase_entry_id", sa.BigInteger(), sa.ForeignKey("purchases.erp_entry_id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolution_source", sa.String(20), nullable=False, server_default="none"),
        sa.Column("match_status", sa.String(30), nullable=False, server_default="unmatched"),
        sa.Column("match_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("erp_cte_id", "sequence", name="uq_freight_cte_invoice_sequence"),
    )
    for name, columns in (
        ("ix_freight_cte_invoices_erp_cte_id", ["erp_cte_id"]),
        ("ix_freight_cte_invoices_reference_access_key", ["reference_access_key"]),
        ("ix_freight_cte_invoices_resolved_purchase_entry_id", ["resolved_purchase_entry_id"]),
        ("ix_freight_cte_invoices_candidate_purchase_entry_id", ["candidate_purchase_entry_id"]),
        ("ix_freight_cte_invoices_resolution_source", ["resolution_source"]),
        ("ix_freight_cte_invoices_match_status", ["match_status"]),
    ):
        op.create_index(name, "freight_cte_invoices", columns)

    op.create_table(
        "freight_reconciliations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("erp_cte_id", sa.BigInteger(), sa.ForeignKey("freight_ctes.erp_cte_id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("rate_id", sa.Integer(), sa.ForeignKey("freight_rates.id"), nullable=True),
        sa.Column("matched_liters", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("expected_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("charged_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("payable_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("paid_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("difference_value", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("payable_difference", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("primary_status", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("issues_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("review_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("algorithm_version", sa.String(30), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_freight_reconciliations_erp_cte_id", ["erp_cte_id"]),
        ("ix_freight_reconciliations_rate_id", ["rate_id"]),
        ("ix_freight_reconciliations_primary_status", ["primary_status"]),
        ("ix_freight_reconciliations_severity", ["severity"]),
        ("ix_freight_reconciliations_review_status", ["review_status"]),
    ):
        op.create_index(name, "freight_reconciliations", columns)

    op.create_table(
        "freight_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reconciliation_id", sa.String(36), sa.ForeignKey("freight_reconciliations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("candidate_purchase_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_freight_reviews_reconciliation_id", ["reconciliation_id"]),
        ("ix_freight_reviews_action", ["action"]),
        ("ix_freight_reviews_created_by", ["created_by"]),
        ("ix_freight_reviews_created_at", ["created_at"]),
    ):
        op.create_index(name, "freight_reviews", columns)

    connection = op.get_bind()
    for unit_code, cnpj in UNIT_CNPJS.items():
        connection.execute(
            sa.text("UPDATE units SET cnpj = :cnpj WHERE code = :unit_code"),
            {"cnpj": cnpj, "unit_code": unit_code},
        )
    connection.execute(
        sa.text(
            """
            INSERT INTO freight_rates
                (carrier_cnpj, carrier_name, origin_cnpj, unit_code, effective_from,
                 effective_to, rate_per_liter, active, created_at, updated_at)
            SELECT CAST(:carrier_cnpj AS VARCHAR(14)), CAST(:carrier_name AS VARCHAR(200)),
                   NULL, NULL, CAST(:effective_from AS DATE),
                   NULL, CAST(:rate_per_liter AS NUMERIC(12, 6)), TRUE,
                   CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (
                SELECT 1 FROM freight_rates
                WHERE carrier_cnpj = CAST(:carrier_cnpj AS VARCHAR(14))
                  AND origin_cnpj IS NULL AND unit_code IS NULL
                  AND effective_from = CAST(:effective_from AS DATE)
            )
            """
        ),
        {
            "carrier_cnpj": "40080594000102",
            "carrier_name": "TRR PAMPA DIESEL LTDA",
            "effective_from": "2026-06-01",
            "rate_per_liter": 0.1525,
        },
    )


def downgrade() -> None:
    op.drop_table("freight_reviews")
    op.drop_table("freight_reconciliations")
    op.drop_table("freight_cte_invoices")
    op.drop_table("freight_rates")
    op.drop_table("freight_ctes")
    op.drop_index("ix_payable_documents_erp_cte_id", table_name="payable_documents")
    op.drop_column("payable_documents", "erp_cte_id")
    op.drop_index("ix_units_cnpj", table_name="units")
    op.drop_column("units", "cnpj")
