"""Apply the approved full historical adjustment for unit 001 / February 2025.

This script changes only the Contracts application database. It never writes
to the ERP and preserves the prior adjustment as an auditable entry.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import BonusRule, ManualAdjustment, Reconciliation
from app.services.audit import audit
from app.services.management_adjustments import FULL_MANAGEMENT_ADJUSTMENT_MARKER
from app.services.reconciliation import rebuild_reconciliations
from app.services.rules import money


TARGET_MONTH = date(2025, 2, 1)
TARGET_EXPECTED = Decimal("9570.00")
ADJUSTMENT_REASON = (
    f"{FULL_MANAGEMENT_ADJUSTMENT_MARKER} Complemento da regularização histórica aprovada pela gestão: "
    "a Ipiranga cortou a bonificação em mês de baixo volume e o valor foi recebido fora do fluxo "
    "regular. Este ajuste encerra a competência integralmente e não representa crédito do portal ou ERP."
)


def apply_unit_001_feb_2025_management_adjustment(
    db: Session, *, rebuild: bool = True
) -> dict[str, Decimal]:
    rule = db.scalar(
        select(BonusRule).where(
            BonusRule.unit_code == "001",
            BonusRule.company_code == "IPIRANGA",
            BonusRule.kind == "distributor_credit",
            BonusRule.active.is_(True),
        )
    )
    if not rule:
        raise ValueError("Regra ativa de crédito Ipiranga da unidade 001 não encontrada.")
    row = db.scalar(
        select(Reconciliation).where(
            Reconciliation.rule_id == rule.id,
            Reconciliation.reference_month == TARGET_MONTH,
        )
    )
    if not row:
        raise ValueError("Conciliação 001 / fevereiro de 2025 não encontrada.")
    expected = money(Decimal(row.expected_value or 0))
    if expected != TARGET_EXPECTED:
        raise ValueError(f"Valor esperado inesperado: R$ {expected:.2f}.")
    existing = db.scalar(
        select(ManualAdjustment).where(
            ManualAdjustment.reconciliation_id == row.id,
            ManualAdjustment.reason.startswith(FULL_MANAGEMENT_ADJUSTMENT_MARKER),
        )
    )
    current_adjustment = money(Decimal(row.manual_adjustment or 0))
    added = Decimal("0.00")
    if not existing:
        added = money(expected - current_adjustment)
        if added <= 0:
            raise ValueError("O ajuste existente não permite completar a regularização histórica.")
        db.add(
            ManualAdjustment(
                reconciliation_id=row.id,
                amount=added,
                reason=ADJUSTMENT_REASON,
                created_by="system-historical-adjustment",
            )
        )
        current_adjustment = money(current_adjustment + added)
    if current_adjustment != expected:
        raise ValueError(
            f"Ajuste histórico inconsistente: R$ {current_adjustment:.2f}; esperado R$ {expected:.2f}."
        )
    row.observed_value = Decimal("0.00")
    row.manual_adjustment = expected
    row.difference_value = Decimal("0.00")
    row.status = "confirmed"
    row.confirmation_mode = "manual"
    row.confidence = "none"
    row.confirmed_at = row.confirmed_at or datetime.now(timezone.utc)
    row.notes = (
        "Competência encerrada por ajuste gerencial histórico integral; "
        "não representa crédito emitido pela Ipiranga ou identificado no ERP."
    )
    db.flush()
    if rebuild:
        rebuild_reconciliations(db)
    return {"added": added, "total_adjustment": expected}


def main() -> None:
    with SessionLocal() as db:
        result = apply_unit_001_feb_2025_management_adjustment(db)
        audit(
            db,
            None,
            "unit_001_feb_2025_full_management_adjustment",
            "reconciliation",
            "001:2025-02",
            {key: str(value) for key, value in result.items()},
        )
        db.commit()
        print(result)


if __name__ == "__main__":
    main()
