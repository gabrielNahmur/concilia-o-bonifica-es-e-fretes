"""Use transported invoice dates as freight competence evidence.

Revision ID: 0009_freight_reference_dates
Revises: 0008_freight_reconciliation
"""

import sqlalchemy as sa
from alembic import op


revision = "0009_freight_reference_dates"
down_revision = "0008_freight_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("freight_cte_invoices", sa.Column("reference_invoice_number", sa.String(30), nullable=True))
    op.add_column("freight_cte_invoices", sa.Column("reference_issue_date", sa.Date(), nullable=True))
    op.create_index(
        "ix_freight_cte_invoices_reference_invoice_number",
        "freight_cte_invoices",
        ["reference_invoice_number"],
    )
    op.create_index(
        "ix_freight_cte_invoices_reference_issue_date",
        "freight_cte_invoices",
        ["reference_issue_date"],
    )

    op.add_column("freight_reconciliations", sa.Column("reference_date", sa.Date(), nullable=True))
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE freight_reconciliations AS r
               SET reference_date = c.issue_date
              FROM freight_ctes AS c
             WHERE c.erp_cte_id = r.erp_cte_id
            """
        )
    )
    op.alter_column("freight_reconciliations", "reference_date", nullable=False)
    op.create_index(
        "ix_freight_reconciliations_reference_date",
        "freight_reconciliations",
        ["reference_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_freight_reconciliations_reference_date", table_name="freight_reconciliations")
    op.drop_column("freight_reconciliations", "reference_date")
    op.drop_index("ix_freight_cte_invoices_reference_issue_date", table_name="freight_cte_invoices")
    op.drop_index("ix_freight_cte_invoices_reference_invoice_number", table_name="freight_cte_invoices")
    op.drop_column("freight_cte_invoices", "reference_issue_date")
    op.drop_column("freight_cte_invoices", "reference_invoice_number")
