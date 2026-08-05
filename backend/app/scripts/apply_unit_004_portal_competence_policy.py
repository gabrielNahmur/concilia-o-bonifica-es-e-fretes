"""Apply the approved no-FIFO policy to Ipiranga credits of unit 004.

This modifies only the Contracts application database.  It keeps the original
manual adjustment record and creates an audit entry; it never writes to the
ERP SQL Server.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.models import BonusRule, Reconciliation
from app.services.audit import audit
from app.services.reconciliation import rebuild_reconciliations


REFERENCE_MONTH = date(2026, 5, 1)
POLICY_NOTE = (
    "Reaberta pela política aprovada de 04/08/2026: créditos do portal Ipiranga "
    "da unidade 004 sem competência explícita não podem ser distribuídos por FIFO. "
    "O ajuste manual anterior permanece no histórico/auditoria, mas não compõe mais "
    "a conciliação enquanto a competência do crédito não for confirmada."
)


def main() -> None:
    with SessionLocal() as db:
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "004",
                BonusRule.company_code == "IPIRANGA",
                BonusRule.kind == "distributor_credit",
            )
        )
        if not rule:
            raise SystemExit("Regra de crédito Ipiranga da unidade 004 não encontrada")
        row = db.scalar(
            select(Reconciliation).where(
                Reconciliation.rule_id == rule.id,
                Reconciliation.reference_month == REFERENCE_MONTH,
            )
        )
        if not row:
            raise SystemExit("Conciliação de maio/2026 da unidade 004 não encontrada")

        previous_adjustment = Decimal(row.manual_adjustment or 0)
        if previous_adjustment:
            row.manual_adjustment = Decimal("0.00")
            row.confirmed_at = None
            row.confirmed_by = None
            row.confirmation_mode = "none"
            audit(
                db,
                None,
                "reconciliation_manual_adjustment_reopened",
                "reconciliation",
                row.id,
                {
                    "unit_code": "004",
                    "reference_month": REFERENCE_MONTH.isoformat(),
                    "reopened_manual_adjustment": str(previous_adjustment),
                    "reason": POLICY_NOTE,
                },
            )

        # Rebuild after removing the former manual closure, so items, totals
        # and the work queue all receive the same no-FIFO policy.
        rebuild_reconciliations(db)
        row = db.scalar(
            select(Reconciliation).where(
                Reconciliation.rule_id == rule.id,
                Reconciliation.reference_month == REFERENCE_MONTH,
            )
        )
        row.notes = POLICY_NOTE
        db.commit()
        print(
            {
                "unit": "004",
                "reference_month": REFERENCE_MONTH.isoformat(),
                "reopened_manual_adjustment": str(previous_adjustment),
                "status": row.status,
                "expected": str(row.expected_value),
                "identified": str(row.observed_value),
                "difference": str(row.difference_value),
            }
        )


if __name__ == "__main__":
    main()
