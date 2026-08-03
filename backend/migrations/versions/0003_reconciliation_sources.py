"""Native payable movements and accounting journal identity.

Revision ID: 0003_reconciliation_sources
Revises: 0002_reconciliation_reviews
"""

import sqlalchemy as sa
from alembic import op


revision = "0003_reconciliation_sources"
down_revision = "0002_reconciliation_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "payable_movements" not in inspector.get_table_names():
        op.create_table(
        "payable_movements",
        sa.Column("erp_key", sa.String(length=220), nullable=False),
        sa.Column("unit_code", sa.String(length=3), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("title_type", sa.String(length=2), nullable=False),
        sa.Column("document_id", sa.String(length=60), nullable=False),
        sa.Column("document_sequence", sa.String(length=2), nullable=False),
        sa.Column("movement_type", sa.String(length=2), nullable=False),
        sa.Column("movement_date", sa.Date(), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("payment_sequence", sa.Integer(), nullable=False),
        sa.Column("reversal_sequence", sa.Integer(), nullable=False),
        sa.Column("financial_launch_id", sa.BigInteger(), nullable=True),
        sa.Column("financial_sequence", sa.Integer(), nullable=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=True),
        sa.Column("payment_method", sa.String(length=12), nullable=True),
        sa.Column("operation_id", sa.String(length=12), nullable=True),
        sa.Column("center_code", sa.String(length=12), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("erp_key"),
    )
        op.create_index("ix_payable_movements_unit_code", "payable_movements", ["unit_code"])
        op.create_index("ix_payable_movements_document_id", "payable_movements", ["document_id"])
        op.create_index("ix_payable_movements_movement_type", "payable_movements", ["movement_type"])
        op.create_index("ix_payable_movements_movement_date", "payable_movements", ["movement_date"])
        op.create_index("ix_payable_movements_financial_launch_id", "payable_movements", ["financial_launch_id"])
        op.create_index(
            "ix_payable_movement_title",
            "payable_movements",
            ["unit_code", "person_id", "title_type", "document_id", "document_sequence"],
        )

    accounting_columns = {column["name"] for column in inspector.get_columns("accounting_entries")}
    for name, column_type in (
        ("journal_lot", sa.BigInteger()),
        ("journal_entry", sa.BigInteger()),
        ("journal_sequence", sa.Integer()),
        ("source_unit_code", sa.String(length=3)),
        ("account_name", sa.String(length=180)),
        ("history_name", sa.String(length=180)),
    ):
        if name not in accounting_columns:
            op.add_column("accounting_entries", sa.Column(name, column_type, nullable=True))
    existing_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("accounting_entries")}
    if "ix_accounting_entries_journal_lot" not in existing_indexes:
        op.create_index("ix_accounting_entries_journal_lot", "accounting_entries", ["journal_lot"])
    if "ix_accounting_entries_journal_entry" not in existing_indexes:
        op.create_index("ix_accounting_entries_journal_entry", "accounting_entries", ["journal_entry"])
    if "ix_accounting_journal" not in existing_indexes:
        op.create_index("ix_accounting_journal", "accounting_entries", ["journal_lot", "journal_entry", "unit_code"])

    op.execute(
        """
        UPDATE bonus_rules
        SET applies_to = 'fuel_codes:1,3,5'
        WHERE kind = 'invoice_discount' AND unit_code IN ('005','007','014')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE bonus_rules
        SET applies_to = 'all_fuel'
        WHERE kind = 'invoice_discount' AND unit_code IN ('005','007','014')
        """
    )
    op.drop_index("ix_accounting_journal", table_name="accounting_entries")
    op.drop_index("ix_accounting_entries_journal_entry", table_name="accounting_entries")
    op.drop_index("ix_accounting_entries_journal_lot", table_name="accounting_entries")
    op.drop_column("accounting_entries", "history_name")
    op.drop_column("accounting_entries", "account_name")
    op.drop_column("accounting_entries", "source_unit_code")
    op.drop_column("accounting_entries", "journal_sequence")
    op.drop_column("accounting_entries", "journal_entry")
    op.drop_column("accounting_entries", "journal_lot")
    op.drop_index("ix_payable_movement_title", table_name="payable_movements")
    op.drop_index("ix_payable_movements_financial_launch_id", table_name="payable_movements")
    op.drop_index("ix_payable_movements_movement_date", table_name="payable_movements")
    op.drop_index("ix_payable_movements_movement_type", table_name="payable_movements")
    op.drop_index("ix_payable_movements_document_id", table_name="payable_movements")
    op.drop_index("ix_payable_movements_unit_code", table_name="payable_movements")
    op.drop_table("payable_movements")
