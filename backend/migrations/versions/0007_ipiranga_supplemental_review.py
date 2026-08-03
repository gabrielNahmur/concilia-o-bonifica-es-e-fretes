"""Supplemental Ipiranga review fields.

Revision ID: 0007_ipiranga_supplemental_review
Revises: 0006_invoice_boleto_evidence
"""

import sqlalchemy as sa
from alembic import op


revision = "0007_ipiranga_supplemental_review"
down_revision = "0006_invoice_boleto_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Instalações iniciais criaram o controle do Alembic com VARCHAR(32),
    # menor que o identificador desta própria revisão. Em PostgreSQL a
    # alteração é transacional, portanto ela precisa ocorrer antes de o
    # Alembic gravar a nova versão no fim da revisão.
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("portal_bonus_events")}
    if "business_classification" not in columns:
        op.add_column("portal_bonus_events", sa.Column("business_classification", sa.String(40), nullable=True))
    if "review_notes" not in columns:
        op.add_column("portal_bonus_events", sa.Column("review_notes", sa.Text(), nullable=True))
    if "reviewed_by" not in columns:
        op.add_column("portal_bonus_events", sa.Column("reviewed_by", sa.String(36), nullable=True))
    if "reviewed_at" not in columns:
        op.add_column("portal_bonus_events", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))

    indexes = {index["name"] for index in inspector.get_indexes("portal_bonus_events")}
    if "ix_portal_bonus_events_business_classification" not in indexes:
        op.create_index(
            "ix_portal_bonus_events_business_classification",
            "portal_bonus_events",
            ["business_classification"],
        )
    if "ix_portal_bonus_events_reviewed_by" not in indexes:
        op.create_index(
            "ix_portal_bonus_events_reviewed_by",
            "portal_bonus_events",
            ["reviewed_by"],
        )


def downgrade() -> None:
    with op.batch_alter_table("portal_bonus_events") as batch_op:
        batch_op.drop_index("ix_portal_bonus_events_reviewed_by")
        batch_op.drop_index("ix_portal_bonus_events_business_classification")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("review_notes")
        batch_op.drop_column("business_classification")
