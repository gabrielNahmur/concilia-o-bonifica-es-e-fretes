"""Register the approved unit 004 Ipiranga portal usage details.

This utility writes only to the Contracts application database.  It never
opens an ERP connection and it validates the ERP snapshot before linking a
portal credit to the NF explicitly named by Ipiranga.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import PortalBonusEvent, PortalBonusMatch, Purchase
from app.services.audit import audit
from app.services.reconciliation import rebuild_reconciliations
from app.services.rules import money
from app.services.unit_004_portal_usage import DIRECT_PORTAL_USAGE_STATUS


UNIT_004_PORTAL_USAGE_EVIDENCE = (
    {
        "portal_date": date(2025, 10, 17), "credit_value": Decimal("8400.00"),
        "purchase_entry_id": 900086416, "invoice_number": "2970475", "gross_value": Decimal("53619.00"),
        "access_key": "43251033337122015906550030029704751960853771",
    },
    {
        "portal_date": date(2025, 11, 18), "credit_value": Decimal("8750.00"),
        "purchase_entry_id": 900087647, "invoice_number": "2983393", "gross_value": Decimal("79357.50"),
        "access_key": "43251133337122015906550030029833931200309642",
    },
    {
        "portal_date": date(2025, 12, 16), "credit_value": Decimal("9660.00"),
        "purchase_entry_id": 900088975, "invoice_number": "2996341", "gross_value": Decimal("52845.00"),
        "access_key": "43251233337122015906550030029963411138437446",
    },
    {
        "portal_date": date(2026, 1, 16), "credit_value": Decimal("10360.01"),
        "purchase_entry_id": 900090260, "invoice_number": "3008303", "gross_value": Decimal("108650.00"),
        "access_key": "43260133337122015906550030030083031951343959",
    },
    {
        "portal_date": date(2026, 2, 18), "credit_value": Decimal("9450.00"),
        "purchase_entry_id": 900091389, "invoice_number": "3020812", "gross_value": Decimal("53515.00"),
        "access_key": "43260233337122015906550030030208121960346427",
    },
    {
        "portal_date": date(2026, 3, 16), "credit_value": Decimal("7700.00"),
        "purchase_entry_id": 900092349, "invoice_number": "3031409", "gross_value": Decimal("54967.00"),
        "access_key": "43260333337122015906550030030314091467333474",
    },
    {
        "portal_date": date(2026, 4, 20), "credit_value": Decimal("8750.00"),
        "purchase_entry_id": 900093605, "invoice_number": "3048889", "gross_value": Decimal("27953.00"),
        "access_key": "43260433337122015906550030030488891385366710",
    },
    {
        "portal_date": date(2026, 5, 18), "credit_value": Decimal("8050.00"),
        "purchase_entry_id": 900094648, "invoice_number": "3060385", "gross_value": Decimal("26934.00"),
        "access_key": "43260533337122015906550030030603851127279864",
    },
    {
        "portal_date": date(2026, 7, 16), "credit_value": Decimal("7350.00"),
        "purchase_entry_id": 900097005, "invoice_number": "3087503", "gross_value": Decimal("26348.00"),
        "access_key": "43260733337122015906550030030875031189176084",
    },
)


def _single_event(db: Session, row: dict) -> PortalBonusEvent:
    events = db.scalars(
        select(PortalBonusEvent).where(
            PortalBonusEvent.unit_code == "004",
            PortalBonusEvent.company_code == "IPIRANGA",
            PortalBonusEvent.category == "postpaid",
            PortalBonusEvent.portal_date == row["portal_date"],
            PortalBonusEvent.value == money(Decimal(row["credit_value"])),
        )
    ).all()
    if len(events) != 1:
        raise ValueError(
            f"Esperado um evento Ipiranga 004 para {row['portal_date']} no valor "
            f"R$ {money(Decimal(row['credit_value'])):.2f}; encontrados {len(events)}."
        )
    return events[0]


def _validated_purchase(db: Session, row: dict) -> Purchase:
    purchase = db.get(Purchase, row["purchase_entry_id"])
    if not purchase:
        raise ValueError(f"Entrada ERP {row['purchase_entry_id']} não encontrada.")
    checks = {
        "unidade": (purchase.unit_code, "004"),
        "companhia": (purchase.mapped_company_code, "IPIRANGA"),
        "NF": (str(purchase.invoice_number), str(row["invoice_number"])),
        "valor bruto": (money(Decimal(purchase.gross_value or 0)), money(Decimal(row["gross_value"]))),
        "chave de acesso": (str(purchase.access_key or ""), str(row["access_key"])),
    }
    mismatches = [name for name, (actual, expected) in checks.items() if actual != expected]
    if mismatches:
        raise ValueError(
            f"Entrada ERP {row['purchase_entry_id']} não valida o detalhe do portal: "
            + ", ".join(mismatches)
        )
    return purchase


def apply_unit_004_portal_usage_evidence(
    db: Session, *, evidence_rows: Sequence[dict] = UNIT_004_PORTAL_USAGE_EVIDENCE
) -> dict:
    """Upsert direct portal-use matches, refusing an incompatible prior detail."""
    linked = 0
    preserved = 0
    for row in evidence_rows:
        event = _single_event(db, row)
        purchase = _validated_purchase(db, row)
        details = {
            "source_kind": "portal_detail_transcription",
            "portal_credit_value": str(money(Decimal(row["credit_value"]))),
            "usage_invoice_number": str(row["invoice_number"]),
            "usage_invoice_gross": str(money(Decimal(row["gross_value"]))),
            "usage_access_key": str(row["access_key"]),
        }
        match = db.scalar(select(PortalBonusMatch).where(PortalBonusMatch.event_id == event.id))
        if match and match.status == DIRECT_PORTAL_USAGE_STATUS:
            try:
                existing = json.loads(match.details_json or "{}")
            except json.JSONDecodeError:
                existing = {}
            same_match = (
                match.purchase_entry_id == purchase.erp_entry_id
                and existing.get("usage_invoice_number") == details["usage_invoice_number"]
                and existing.get("usage_access_key") == details["usage_access_key"]
                and existing.get("portal_credit_value") == details["portal_credit_value"]
            )
            if not same_match:
                raise ValueError(f"Evento do portal {event.id} já possui detalhe direto incompatível.")
            preserved += 1
            continue
        if not match:
            match = PortalBonusMatch(event_id=event.id)
            db.add(match)
        match.status = DIRECT_PORTAL_USAGE_STATUS
        match.movement_key = None
        match.payable_document_id = None
        match.purchase_entry_id = purchase.erp_entry_id
        match.financial_entry_id = None
        match.date_difference_days = None
        match.match_basis = (
            "Detalhe do portal Ipiranga declara a Nota Fiscal Utilizada e os Créditos Associados; "
            "a NF comprova o uso do crédito, sem inferir qual compra originou a bonificação."
        )
        match.details_json = json.dumps(details, ensure_ascii=False, sort_keys=True)
        match.algorithm_version = "portal-usage-v1"
        match.matched_at = datetime.now(timezone.utc)
        event.reference = f"NF - {purchase.invoice_number}"
        event.review_notes = "Detalhe de uso transcrito do portal Ipiranga e validado contra a entrada ERP."
        linked += 1
    db.flush()
    return {"linked": linked, "preserved": preserved, "total": len(evidence_rows)}


def main() -> None:
    with SessionLocal() as db:
        result = apply_unit_004_portal_usage_evidence(db)
        rebuild_reconciliations(db)
        audit(
            db,
            None,
            "unit_004_portal_usage_evidence_applied",
            "portal_bonus_match",
            "004",
            {**result, "source": "approved_ipiranga_portal_details"},
        )
        db.commit()
        print(result)


if __name__ == "__main__":
    main()
