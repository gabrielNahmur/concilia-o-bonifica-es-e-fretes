"""Explicit, auditable historical management adjustments.

These adjustments close a known historical balance by management decision. They
are deliberately distinct from a portal, bank or ERP credit and must never be
presented as one of those proofs.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ManualAdjustment, Reconciliation
from app.services.rules import money


FULL_MANAGEMENT_ADJUSTMENT_MARKER = "[historical_full_adjustment]"
CENT = Decimal("0.01")


def full_management_adjustment(db: Session, row: Reconciliation) -> ManualAdjustment | None:
    """Return an approved full adjustment that replaces automatic evidence."""
    adjustment = db.scalar(
        select(ManualAdjustment)
        .where(
            ManualAdjustment.reconciliation_id == row.id,
            ManualAdjustment.reason.startswith(FULL_MANAGEMENT_ADJUSTMENT_MARKER),
        )
        .order_by(ManualAdjustment.created_at.desc())
    )
    if not adjustment:
        return None
    if abs(money(Decimal(row.manual_adjustment or 0)) - money(Decimal(row.expected_value or 0))) > CENT:
        return None
    return adjustment


def full_management_adjustment_evidence(
    row: Reconciliation, adjustment: ManualAdjustment
) -> dict:
    """Build a visible explanation without misrepresenting it as a credit."""
    value = money(Decimal(row.manual_adjustment or 0))
    return {
        "source": "MANAGEMENT_ADJUSTMENT",
        "id": adjustment.id,
        "date": adjustment.created_at.date().isoformat(),
        "value": float(value),
        "allocated": 0.0,
        "counted": False,
        "reason": adjustment.reason.removeprefix(FULL_MANAGEMENT_ADJUSTMENT_MARKER).strip(),
        "match_basis": (
            "Decisão gerencial auditada. Este valor não é crédito emitido pela distribuidora "
            "nem lançamento identificado no ERP."
        ),
    }
