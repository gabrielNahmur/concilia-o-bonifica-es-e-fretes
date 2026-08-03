"""Map CT-es received as purchase entries outside MCTe.

Revision ID: 0010_supplemental_freight_ctes
Revises: 0009_freight_reference_dates
"""

import sqlalchemy as sa
from alembic import op


revision = "0010_supplemental_freight_ctes"
down_revision = "0009_freight_reference_dates"
branch_labels = None
depends_on = None


UNIT_CNPJS = {
    "005": "90589698000549",
    "050": "12564276000181",
    "051": "17311148000140",
    "052": "17311148000220",
    "054": "12564276000262",
}


def upgrade() -> None:
    op.add_column(
        "freight_ctes",
        sa.Column("source_kind", sa.String(24), nullable=False, server_default="mcte"),
    )
    op.add_column("freight_ctes", sa.Column("source_entry_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_freight_ctes_source_kind", "freight_ctes", ["source_kind"])
    op.create_index("ix_freight_ctes_source_entry_id", "freight_ctes", ["source_entry_id"])

    connection = op.get_bind()
    for unit_code, cnpj in UNIT_CNPJS.items():
        connection.execute(
            sa.text("UPDATE units SET cnpj = :cnpj WHERE code = :unit_code"),
            {"cnpj": cnpj, "unit_code": unit_code},
        )


def downgrade() -> None:
    op.drop_index("ix_freight_ctes_source_entry_id", table_name="freight_ctes")
    op.drop_index("ix_freight_ctes_source_kind", table_name="freight_ctes")
    op.drop_column("freight_ctes", "source_entry_id")
    op.drop_column("freight_ctes", "source_kind")
