from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BonusRule, Reconciliation
from app.services.reconciliation import rebuild_reconciliations
from app.services.seed import seed_reference_data


def test_deactivated_rule_supersedes_operational_reconciliations_without_deleting_history():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "001",
                BonusRule.kind == "distributor_credit",
            )
        )
        rebuild_reconciliations(db, today=date(2025, 1, 31))
        rows = db.scalars(select(Reconciliation).where(Reconciliation.rule_id == rule.id)).all()
        assert rows

        rule.active = False
        rebuild_reconciliations(db, today=date(2025, 1, 31), commit=False)

        assert all(row.status == "superseded" for row in rows)
        assert all("desativada" in row.notes for row in rows)
