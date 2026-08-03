"""Apply the approved historical scope exclusions for unit 005.

The ten documents remain in the application's purchase, payable and audit
history.  They are only removed from the *contractual bonus* work queue, as
approved by management after comparing the Texaco/Ipiranga portal statement.
This script writes exclusively to the application PostgreSQL database and
never changes the ERP.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Purchase, Reconciliation, ReconciliationException, ReconciliationItem
from app.services.audit import audit
from app.services.reconciliation import rebuild_reconciliations
from app.services.reconciliation_workspace import (
    CONTRACTUAL_EXCLUSION_REVIEW_STATUS,
    recompute_reconciliation_confirmation,
)


APPROVED_EXCLUSIONS = {
    "2941017": Decimal("1000.00"),
    "2942030": Decimal("1200.00"),
    "2944596": Decimal("1000.00"),
    "2946421": Decimal("800.00"),
    "2949620": Decimal("1000.00"),
    "2952887": Decimal("880.00"),
    "2955649": Decimal("1280.00"),
    "2958215": Decimal("1080.00"),
    "3049262": Decimal("1200.00"),
    "3052024": Decimal("1000.00"),
}

REASON = (
    "Exclusao historica aprovada pela gestao em 23/07/2026: NF fora da "
    "conciliacao contratual da unidade 005. A nota, o titulo, o pagamento e "
    "a ausencia de credito no portal permanecem preservados para auditoria; "
    "esta decisao nao altera o ERP nem reconhece bonificacao recebida."
)


def main() -> None:
    with SessionLocal() as db:
        # Materialize first so this works both on a fresh backfill and in a
        # running environment where the workspace has just been rebuilt.
        rebuild_reconciliations(db)
        purchases = db.scalars(
            select(Purchase).where(
                Purchase.unit_code == "005",
                Purchase.invoice_number.in_(APPROVED_EXCLUSIONS),
            )
        ).all()
        by_invoice = {row.invoice_number: row for row in purchases}
        missing = sorted(set(APPROVED_EXCLUSIONS) - set(by_invoice))
        if missing:
            raise SystemExit(f"Notas da exclusao nao encontradas na unidade 005: {', '.join(missing)}")

        rows = []
        now = datetime.now(timezone.utc)
        for invoice, expected in APPROVED_EXCLUSIONS.items():
            purchase = by_invoice[invoice]
            item = db.scalar(
                select(ReconciliationItem).where(
                    ReconciliationItem.source_key == f"purchase:{purchase.erp_entry_id}"
                )
            )
            if not item:
                raise SystemExit(f"Item conciliavel nao encontrado para NF {invoice}")
            if Decimal(item.expected_value) != expected:
                raise SystemExit(
                    f"Valor inesperado na NF {invoice}: {item.expected_value}; esperado {expected}"
                )
            if item.review_status != CONTRACTUAL_EXCLUSION_REVIEW_STATUS:
                item.review_status = CONTRACTUAL_EXCLUSION_REVIEW_STATUS
                item.status = "not_applicable"
                item.automatic_eligible = False
                item.automatic_confirmed_at = None
                item.reviewed_at = now
                item.review_notes = f"[historical_contract_scope] {REASON}"
                item.policy_reason = (
                    "Documento excluido da conciliacao contratual por decisao auditada; "
                    "a compra e a cadeia financeira permanecem preservadas."
                )
                audit(
                    db,
                    None,
                    "contractual_scope_exclusion",
                    "reconciliation_item",
                    item.id,
                    {
                        "unit_code": "005",
                        "invoice_number": invoice,
                        "purchase_entry_id": purchase.erp_entry_id,
                        "expected_value": str(expected),
                        "reason": REASON,
                    },
                )
            for exception in db.scalars(
                select(ReconciliationException).where(
                    ReconciliationException.item_id == item.id,
                    ReconciliationException.status.in_(("open", "in_review")),
                )
            ):
                exception.status = "resolved"
                exception.resolution_code = "historical_contract_scope"
                exception.resolution_notes = REASON
                exception.resolved_at = now
            rows.append(item)

        db.flush()
        reconciliation_ids = {item.reconciliation_id for item in rows}
        for reconciliation_id in reconciliation_ids:
            row = db.get(Reconciliation, reconciliation_id)
            recompute_reconciliation_confirmation(db, row)
        db.commit()
        print(
            {
                "unit": "005",
                "excluded_invoices": len(rows),
                "excluded_expected_value": str(sum(APPROVED_EXCLUSIONS.values(), Decimal("0.00"))),
                "reconciliations_recomputed": len(reconciliation_ids),
            }
        )


if __name__ == "__main__":
    main()
