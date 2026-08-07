from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AccountingEntry,
    BankEntry,
    BonusRule,
    FinancialEntry,
    ManualAdjustment,
    PayableDocument,
    PayableMovement,
    PortalBonusEvent,
    PortalBonusMatch,
    Purchase,
    Reconciliation,
    ReconciliationException,
)
from app.services.rules import (
    UmbrellaPurchase,
    add_months,
    allocate_umbrella,
    br_milestones,
    due_date_for_month,
    money,
    month_end,
    month_start,
    reconciliation_status,
)
from app.services.management_adjustments import (
    full_management_adjustment,
    full_management_adjustment_evidence,
)


ZERO = Decimal("0")
RAIZEN_RECEIPT_CATEGORY = "raizen_bank_receipt"
NEXT_MONTH_IPIRANGA_PORTAL_UNITS = frozenset({"003", "004"})
COMPANY_TERMS = {
    "BR": ("VIBRA", "PETROBRAS", "BR DISTRIBUIDORA"),
    "SHELL": ("RAIZEN", "RAÍZEN", "SHELL"),
    "IPIRANGA": ("IPIRANGA", "ULTRA"),
    "TEXACO": ("IPIRANGA", "TEXACO", "CHEVRON", "ULTRA"),
}


def _months(start: date, end: date):
    current = month_start(start)
    last = month_start(end)
    while current <= last:
        yield current
        current = add_months(current, 1)


def _eligible_item_codes(rule: BonusRule) -> set[str] | None:
    prefix = "fuel_codes:"
    applies_to = (rule.applies_to or "all_fuel").strip().lower()
    if not applies_to.startswith(prefix):
        return None
    return {code.strip() for code in applies_to[len(prefix):].split(",") if code.strip()}


def _eligible_liters(purchase: Purchase, rule: BonusRule) -> Decimal:
    codes = _eligible_item_codes(rule)
    if codes is None:
        return Decimal(purchase.total_liters or 0)
    return sum(
        (Decimal(item.quantity or 0) for item in purchase.items if str(item.item_code).strip() in codes),
        ZERO,
    )


def invoice_discount_forfeited_by_late_payment(
    rule: BonusRule, documents: list[PayableDocument]
) -> bool:
    """Return whether a proven late Texaco settlement voids its invoice credit.

    The portal/ERP evidence shows the same commercial condition in the Texaco
    units: titles settled after their due date receive no contractual discount.
    This never applies to other suppliers or bonus modalities, and requires one
    uniquely linked paid title with both dates available.
    """
    if not (
        rule.company_code == "TEXACO"
        and rule.kind == "invoice_discount"
        and len(documents) == 1
    ):
        return False
    document = documents[0]
    return bool(
        document.payment_date
        and document.due_date
        and document.payment_date > document.due_date
    )


def contractual_purchase_date(rule: BonusRule):
    """Return the purchase date basis defined by the applicable contract rule."""
    if rule.kind == "distributor_credit" and rule.company_code == "IPIRANGA":
        return func.coalesce(Purchase.invoice_issue_date, Purchase.purchase_date)
    return Purchase.purchase_date


def _purchase_totals(db: Session, rule: BonusRule, reference_month: date) -> tuple[Decimal, Decimal, list[int]]:
    end = add_months(reference_month, 1)
    contractual_date = contractual_purchase_date(rule)
    filters = [
        Purchase.unit_code == rule.unit_code,
        Purchase.mapped_company_code == rule.company_code,
        contractual_date >= max(reference_month, rule.effective_from),
        contractual_date < end,
    ]
    if rule.effective_to:
        filters.append(contractual_date <= rule.effective_to)
    rows = db.scalars(select(Purchase).options(selectinload(Purchase.items)).where(*filters)).all()
    entry_ids = [row.erp_entry_id for row in rows]
    documents_by_entry: dict[int, list[PayableDocument]] = defaultdict(list)
    if rule.kind == "invoice_discount" and entry_ids:
        for document in db.scalars(
            select(PayableDocument).where(PayableDocument.erp_entry_id.in_(entry_ids))
        ).all():
            if document.erp_entry_id is not None:
                documents_by_entry[document.erp_entry_id].append(document)
    eligible_rows = [
        row
        for row in rows
        if not invoice_discount_forfeited_by_late_payment(
            rule, documents_by_entry.get(row.erp_entry_id, [])
        )
    ]
    return (
        sum((_eligible_liters(row, rule) for row in eligible_rows), ZERO),
        sum((Decimal(row.s10_liters) for row in rows), ZERO),
        [row.erp_entry_id for row in eligible_rows],
    )


def _document_discounts(db: Session, entry_ids: list[int]) -> tuple[Decimal, list[dict]]:
    if not entry_ids:
        return ZERO, []
    documents = db.scalars(select(PayableDocument).where(PayableDocument.erp_entry_id.in_(entry_ids))).all()
    evidence = [
        {
            "source": "MDCDP.Vlr_OutAbt",
            "document": row.document_id,
            "payment_date": row.payment_date.isoformat() if row.payment_date else None,
            "informative_value": float(row.other_discount or 0),
        }
        for row in documents
        if Decimal(row.other_discount or 0) != ZERO
    ]
    document_ids = [row.document_id for row in documents if row.document_id]
    if not document_ids:
        return ZERO, []
    launches = db.scalars(
        select(FinancialEntry).where(
            FinancialEntry.document_id.in_(document_ids),
            FinancialEntry.unit_code == documents[0].unit_code,
            FinancialEntry.history_code == 6204,
        )
    ).all()
    financial_evidence = [
            {
                "source": "MLANF",
                "launch": row.erp_launch_id,
                "date": row.entry_date.isoformat(),
                "value": float(abs(Decimal(row.value))),
            }
            for row in launches
        ]
    # No backfill das regras Texaco, Vlr_OutAbt foi sempre zero. O campo
    # permanece apenas informativo; o desconto operacional vem do histórico 6204.
    return sum((abs(Decimal(row.value)) for row in launches), ZERO), evidence + financial_evidence


def _credit_pool(db: Session, rule: BonusRule, until: date) -> list[dict]:
    rows = db.scalars(
        select(FinancialEntry)
        .where(
            FinancialEntry.unit_code == rule.unit_code,
            FinancialEntry.history_code == 6204,
            FinancialEntry.entry_date >= rule.effective_from,
            FinancialEntry.entry_date <= until,
        )
        .order_by(FinancialEntry.entry_date, FinancialEntry.id)
    ).all()
    return [
        {
            "id": row.id,
            "date": row.entry_date,
            "remaining": abs(Decimal(row.value)),
            "document": row.document_id,
        }
        for row in rows
    ]


def _consume_fifo(pool: list[dict], expected: Decimal, due: date) -> tuple[Decimal, list[dict]]:
    observed = ZERO
    evidence: list[dict] = []
    # A utilização pode ocorrer depois do mês de referência; o saldo é abatido
    # sempre do crédito esperado mais antigo.
    for item in pool:
        if item["remaining"] <= 0:
            continue
        amount = min(item["remaining"], max(ZERO, expected - observed))
        if amount <= 0:
            break
        item["remaining"] -= amount
        observed += amount
        evidence.append(
            {
                "source": "MLANF",
                "id": item["id"],
                "date": item["date"].isoformat(),
                "document": item["document"],
                "allocated": float(money(amount)),
                "after_due_date": item["date"] > due,
            }
        )
    return money(observed), evidence


def _purchase_financial_links(
    db: Session, unit_code: str, entry_ids: list[int]
) -> tuple[list[PayableDocument], dict[int, list[FinancialEntry]]]:
    """Return only history-6204 entries linked through a contractual purchase document."""
    if not entry_ids:
        return [], {}
    documents = db.scalars(
        select(PayableDocument).where(
            PayableDocument.unit_code == unit_code,
            PayableDocument.erp_entry_id.in_(entry_ids),
        )
    ).all()
    document_ids = sorted({row.document_id for row in documents if row.document_id})
    launches = db.scalars(
        select(FinancialEntry)
        .where(
            FinancialEntry.unit_code == unit_code,
            FinancialEntry.document_id.in_(document_ids) if document_ids else False,
            FinancialEntry.history_code == 6204,
        )
        .order_by(FinancialEntry.entry_date, FinancialEntry.id)
    ).all()
    entries_by_document: dict[str, set[int]] = defaultdict(set)
    for document in documents:
        if document.document_id and document.erp_entry_id is not None:
            entries_by_document[document.document_id].add(document.erp_entry_id)
    by_purchase: dict[int, list[FinancialEntry]] = defaultdict(list)
    seen: dict[int, set[int]] = defaultdict(set)
    for launch in launches:
        targets = entries_by_document.get(launch.document_id, set())
        # Documento repetido em mais de uma entrada é ambíguo e não deve ser
        # confirmado automaticamente; permanece visível na conferência detalhada.
        if len(targets) != 1:
            continue
        for entry_id in targets:
            if launch.id not in seen[entry_id]:
                by_purchase[entry_id].append(launch)
                seen[entry_id].add(launch.id)
    return documents, by_purchase


def _purchase_movement_links(
    db: Session, unit_code: str, entry_ids: list[int]
) -> tuple[list[PayableDocument], dict[int, list[PayableMovement]]]:
    """Link native MDCMP discounts through the full payable-title identity."""
    if not entry_ids:
        return [], {}
    documents = db.scalars(
        select(PayableDocument).where(
            PayableDocument.unit_code == unit_code,
            PayableDocument.erp_entry_id.in_(entry_ids),
        )
    ).all()
    title_to_entries: dict[tuple, set[int]] = defaultdict(set)
    for document in documents:
        title_to_entries[
            (
                document.unit_code,
                document.person_id,
                document.title_type,
                document.document_id,
                document.sequence,
            )
        ].add(document.erp_entry_id)
    if not title_to_entries:
        return documents, {}
    document_ids = sorted({key[3] for key in title_to_entries})
    movements = db.scalars(
        select(PayableMovement)
        .where(
            PayableMovement.unit_code == unit_code,
            PayableMovement.document_id.in_(document_ids),
            PayableMovement.movement_type == "D",
            PayableMovement.reversal_sequence == 0,
        )
        .order_by(PayableMovement.movement_date, PayableMovement.erp_key)
    ).all()
    by_purchase: dict[int, list[PayableMovement]] = defaultdict(list)
    for movement in movements:
        key = (
            movement.unit_code,
            movement.person_id,
            movement.title_type,
            movement.document_id,
            movement.document_sequence,
        )
        targets = title_to_entries.get(key, set())
        if len(targets) == 1:
            by_purchase[next(iter(targets))].append(movement)
    return documents, by_purchase


def _movement_corroboration(db: Session, movement: PayableMovement) -> FinancialEntry | None:
    if not movement.financial_launch_id:
        return None
    matches = db.scalars(
        select(FinancialEntry).where(
            FinancialEntry.erp_launch_id == movement.financial_launch_id,
            FinancialEntry.unit_code == movement.unit_code,
            FinancialEntry.document_id == movement.document_id,
            FinancialEntry.history_code == 6204,
        )
    ).all()
    exact = [
        item for item in matches
        if money(abs(Decimal(item.value))) == money(abs(Decimal(movement.amount)))
    ]
    return exact[0] if len(exact) == 1 else None


def _movement_evidence(
    db: Session,
    movement: PayableMovement,
    allocated: Decimal,
    expected_for_purchase: Decimal,
    reason: str,
) -> dict:
    corroboration = _movement_corroboration(db, movement)
    raw = abs(Decimal(movement.amount))
    return {
        "source": "MDCMP",
        "id": movement.erp_key,
        "date": movement.movement_date.isoformat(),
        "document": movement.document_id,
        "document_sequence": movement.document_sequence,
        "financial_launch": movement.financial_launch_id,
        "financial_sequence": movement.financial_sequence,
        "batch": movement.batch_id,
        "payment_sequence": movement.payment_sequence,
        "payment_method": movement.payment_method,
        "value": float(money(raw)),
        "allocated": float(money(allocated)),
        "expected_for_purchase": float(money(expected_for_purchase)),
        "allocation_reason": reason,
        "corroborated_by_6204": bool(corroboration),
        "mlanf_id": corroboration.id if corroboration else None,
    }


def _portal_invoice_credit_evidence(
    db: Session,
    rule: BonusRule,
    purchase: Purchase,
    expected_for_purchase: Decimal,
    *,
    require_settlement: bool = True,
) -> dict | None:
    """Return a first-party Texaco credit when the portal allocation is exact.

    The consolidated statement may contain one ``Nota Propria`` for multiple
    invoices.  Group allocations are accepted only when the portal matcher
    proved an exact non-reused set and marked the invoice allocation invariant
    across all valid solutions for that temporal block.
    """
    rows = db.execute(
        select(PortalBonusEvent, PortalBonusMatch)
        .join(PortalBonusMatch, PortalBonusMatch.event_id == PortalBonusEvent.id)
        .where(
            PortalBonusEvent.unit_code == rule.unit_code,
            PortalBonusEvent.company_code == rule.company_code,
            PortalBonusEvent.category == "postpaid",
            PortalBonusMatch.status.in_(
                ("portal_exact", "portal_group_exact", "portal_issued_awaiting_payment")
            ),
        )
    ).all()
    matches = []
    for event, match in rows:
        try:
            details = json.loads(match.details_json or "{}")
        except json.JSONDecodeError:
            continue
        allocations = details.get("allocations") or []
        if not allocations and details.get("chosen"):
            allocations = [{
                **details["chosen"],
                "purchase_entry_id": details["chosen"].get("purchase_entry_id") or match.purchase_entry_id,
                "allocated": str(money(event.value)),
                "allocation_invariant": True,
                "event_allocation_invariant": True,
            }]
        for allocation in allocations:
            if str(allocation.get("purchase_entry_id")) != str(purchase.erp_entry_id):
                continue
            if money(allocation.get("allocated", 0)) != money(expected_for_purchase):
                continue
            if not allocation.get("allocation_invariant", match.status == "portal_exact"):
                continue
            if require_settlement and not allocation.get("settlement_confirmed", True):
                continue
            matches.append((event, match, allocation, details))
    if len(matches) != 1:
        return None
    event, match, allocation, details = matches[0]
    grouped = match.status == "portal_group_exact" or bool(
        allocation.get("portal_product_anchor_exact")
    )
    return {
        "source": "IPIRANGA_PORTAL",
        "id": event.id,
        "date": event.portal_date.isoformat(),
        "document": allocation.get("title_document"),
        "value": float(money(event.value)),
        "source_value": float(money(event.value)),
        "allocated": float(money(expected_for_purchase)),
        "portal_event_key": event.event_key,
        "portal_match_status": match.status,
        "portal_match_basis": match.match_basis,
        "portal_group_allocation_exact": grouped,
        "portal_product_anchor_exact": bool(allocation.get("portal_product_anchor_exact")),
        "portal_settlement_confirmed": bool(allocation.get("settlement_confirmed", True)),
        "portal_component_event_ids": details.get("component_event_ids") if grouped else None,
        "portal_component_solution_count": details.get("component_solution_count") if grouped else None,
        "company_code": rule.company_code,
        "purchase_entry_id": purchase.erp_entry_id,
        "invoice_number": purchase.invoice_number,
        "chain_exact": True,
        "allocation_reason": (
            "Crédito Nota Própria do portal Texaco e total de Notas Fiscais de Produto fecham a fórmula "
            "contratual da NF sem reutilizar crédito."
            if allocation.get("portal_product_anchor_exact")
            else "Credito Nota Propria agrupado do portal Texaco: o bloco de valores fecha a formula contratual "
            "da NF sem reutilizar credito."
            if grouped
            else "Credito Nota Propria do portal Texaco vinculado unicamente a NF paga pela formula contratual."
        ),
    }


def _portal_invoice_credit_issued_evidence(
    db: Session,
    rule: BonusRule,
    purchase: Purchase,
    expected_for_purchase: Decimal,
) -> dict | None:
    """Return an exact first-party portal proof when the ERP title is still open.

    A portal statement is issued by the distributor itself and its matcher has
    already proved a unique, non-reused allocation to the invoice.  It proves
    the contractual credit independently from the later ERP settlement.  The
    settlement flag remains in the audit payload, but cannot keep an exact
    portal proof open as if it were a financial divergence.
    """
    evidence = _portal_invoice_credit_evidence(
        db,
        rule,
        purchase,
        expected_for_purchase,
        require_settlement=False,
    )
    if not evidence or evidence.get("portal_settlement_confirmed"):
        return None
    evidence.update(
        {
            "counted": True,
            "credit_issued": True,
            "allocation_reason": (
                "Crédito contratual emitido no portal Texaco e vinculado por valor exato à NF; "
                "o extrato do portal é a prova primária da bonificação."
            ),
        }
    )
    return evidence


def _document_discounts_capped(
    db: Session, rule: BonusRule, entry_ids: list[int]
) -> tuple[Decimal, list[dict]]:
    """Recognize only exact contract discounts from the native payable chain."""
    if not entry_ids:
        return ZERO, []
    purchases = {
        row.erp_entry_id: row
        for row in db.scalars(
            select(Purchase).options(selectinload(Purchase.items)).where(Purchase.erp_entry_id.in_(entry_ids))
        ).all()
    }
    documents, movements_by_purchase = _purchase_movement_links(db, rule.unit_code, entry_ids)
    documents_by_entry: dict[int, list[PayableDocument]] = defaultdict(list)
    for document in documents:
        if document.erp_entry_id is not None:
            documents_by_entry[document.erp_entry_id].append(document)
    evidence = [
        {
            "source": "MDCDP.Vlr_OutAbt",
            "document": row.document_id,
            "payment_date": row.payment_date.isoformat() if row.payment_date else None,
            "informative_value": float(row.other_discount or 0),
        }
        for row in documents
        if Decimal(row.other_discount or 0) != ZERO
    ]
    observed = ZERO
    for entry_id in entry_ids:
        purchase = purchases.get(entry_id)
        if not purchase:
            continue
        if invoice_discount_forfeited_by_late_payment(
            rule, documents_by_entry.get(entry_id, [])
        ):
            continue
        eligible_liters = _eligible_liters(purchase, rule)
        expected_for_purchase = money(eligible_liters * Decimal(rule.rate_per_liter))
        purchase_movements = movements_by_purchase.get(entry_id, [])
        raw_purchase_discount = money(sum((abs(Decimal(item.amount)) for item in purchase_movements), ZERO))
        # Um desconto financeiro genérico não prova a bonificação do contrato.
        # Em todas as modalidades por nota, somente a igualdade centavo a
        # centavo é apropriada; diferenças permanecem visíveis para cobrança.
        portal_credit = (
            _portal_invoice_credit_evidence(db, rule, purchase, expected_for_purchase)
            if raw_purchase_discount != expected_for_purchase
            else None
        )
        portal_credit_issued = (
            _portal_invoice_credit_issued_evidence(db, rule, purchase, expected_for_purchase)
            if raw_purchase_discount != expected_for_purchase and not portal_credit
            else None
        )
        if portal_credit:
            observed += expected_for_purchase
            evidence.append(portal_credit)
            for movement in purchase_movements:
                evidence.append(
                    {
                        "source": "unclassified_credit",
                        "date": movement.movement_date.isoformat(),
                        "document": movement.document_id,
                        "value": float(money(abs(Decimal(movement.amount)))),
                        "reason": (
                            "Desconto MDCMP preservado como uso parcial do credito; a concessao integral esta "
                            "comprovada pela Nota Propria exata do portal Texaco."
                        ),
                        "mdcmp_id": movement.erp_key,
                        "purchase_entry_id": entry_id,
                        "expected_for_purchase": float(expected_for_purchase),
                        "raw_purchase_discount": float(raw_purchase_discount),
                        "counted": False,
                    }
                )
            continue
        if portal_credit_issued:
            observed += expected_for_purchase
            evidence.append(portal_credit_issued)
            # The portal proof is already unique and exact for this invoice.
            # Do not display unrelated or partial native movements alongside it
            # as if they were additional contractual proofs.
            continue
        if raw_purchase_discount != expected_for_purchase:
            for movement in purchase_movements:
                evidence.append(
                    {
                        "source": "unclassified_credit",
                        "date": movement.movement_date.isoformat(),
                        "document": movement.document_id,
                        "value": float(money(abs(Decimal(movement.amount)))),
                        "reason": (
                            "Desconto MDCMP ligado à nota, mas o total não fecha a regra contratual: "
                            f"identificado {raw_purchase_discount} e esperado {expected_for_purchase}."
                        ),
                        "mdcmp_id": movement.erp_key,
                        "purchase_entry_id": entry_id,
                        "expected_for_purchase": float(expected_for_purchase),
                        "raw_purchase_discount": float(raw_purchase_discount),
                    }
                )
            continue
        remaining_expected = expected_for_purchase
        for movement in purchase_movements:
            raw_value = abs(Decimal(movement.amount))
            allocated = min(remaining_expected, raw_value)
            if allocated > ZERO:
                remaining_expected -= allocated
                observed += allocated
                evidence.append(
                    _movement_evidence(
                        db,
                        movement,
                        allocated,
                        expected_for_purchase,
                        "Desconto MDCMP tipo D exatamente igual à regra contratual da nota",
                    )
                )
            residual = raw_value - allocated
            if residual > ZERO:
                evidence.append(
                    {
                        "source": "unclassified_credit",
                        "date": movement.movement_date.isoformat(),
                        "document": movement.document_id,
                        "value": float(money(residual)),
                        "reason": "Excedente do desconto MDCMP após a regra postecipada conhecida.",
                        "mdcmp_id": movement.erp_key,
                    }
                )
    return money(observed), evidence


def _contract_credit_pool(db: Session, rule: BonusRule, until: date) -> list[dict]:
    purchases = db.scalars(
        select(Purchase).where(
            Purchase.unit_code == rule.unit_code,
            Purchase.mapped_company_code == rule.company_code,
            Purchase.purchase_date >= rule.effective_from,
            Purchase.purchase_date <= until,
        )
    ).all()
    _, movements_by_purchase = _purchase_movement_links(
        db, rule.unit_code, [row.erp_entry_id for row in purchases]
    )
    unique = {
        movement.erp_key: movement
        for movements in movements_by_purchase.values()
        for movement in movements
    }
    result = []
    for row in sorted(unique.values(), key=lambda item: (item.movement_date, item.erp_key)):
        corroboration = _movement_corroboration(db, row)
        result.append({
            "id": row.erp_key,
            "date": row.movement_date,
            "remaining": abs(Decimal(row.amount)),
            "document": row.document_id,
            "movement": row,
            "source_reason": "Desconto MDCMP ligado ao título de uma compra contratual",
            "raw_value": abs(Decimal(row.amount)),
            "corroborated_by_6204": bool(corroboration),
            "mlanf_id": corroboration.id if corroboration else None,
        })
    return result


def _portal_credit_pool(db: Session, rule: BonusRule, until: date) -> list[dict]:
    """Postpaid credit issuance proven directly by an Ipiranga portal statement.

    The portal proves that the supplier granted the credit. It intentionally
    replaces, instead of adding to, native MDCMP discounts: those discounts
    only prove consumption of the same distributor balance.
    """
    events = db.scalars(
        select(PortalBonusEvent)
        .where(
            PortalBonusEvent.unit_code == rule.unit_code,
            PortalBonusEvent.company_code == rule.company_code,
            PortalBonusEvent.category == "postpaid",
            PortalBonusEvent.portal_date <= until,
        )
        .order_by(PortalBonusEvent.portal_date, PortalBonusEvent.id)
    ).all()
    if not events:
        return []
    matches = {
        row.event_id: row
        for row in db.scalars(
            select(PortalBonusMatch).where(
                PortalBonusMatch.event_id.in_([event.id for event in events])
            )
        ).all()
    }
    pool = []
    for event in events:
        match = matches.get(event.id)
        chosen = {}
        if match and match.details_json:
            try:
                chosen = json.loads(match.details_json or "{}").get("chosen") or {}
            except json.JSONDecodeError:
                chosen = {}
        pool.append(
            {
                "id": event.id,
                "date": event.portal_date,
                "remaining": money(event.value),
                "raw_value": money(event.value),
                "document": chosen.get("title_document"),
                "source": "IPIRANGA_PORTAL",
                "source_reason": "Credito de Bonificacao Postecipada registrado explicitamente no extrato do portal Ipiranga.",
                "portal_event_key": event.event_key,
                "portal_match_status": match.status if match else "unmatched",
                "match_basis": match.match_basis if match else "Credito explicitamente informado pelo portal; sem cadeia ERP exata.",
                "chain_exact": bool(match and match.status in {"exact", "portal_exact", "portal_group_exact"}),
                "invoice_number": chosen.get("invoice_number"),
                "purchase_entry_id": chosen.get("purchase_entry_id"),
                "financial_entry_id": chosen.get("financial_entry_id"),
            }
        )
    return pool


def uses_next_month_ipiranga_portal_credit(rule: BonusRule) -> bool:
    """Return whether the portal issue month defines this rule's competence.

    Units 003 and 004 have an approved operational rule: a postpaid Ipiranga
    credit issued in calendar month M+1 is compared with purchases from M.
    The association is date-based, so it must never use FIFO, an exact-value
    reservation, or an inferred purchase cycle.
    """
    return (
        rule.kind == "distributor_credit"
        and rule.company_code == "IPIRANGA"
        and rule.unit_code in NEXT_MONTH_IPIRANGA_PORTAL_UNITS
    )


def _next_month_ipiranga_portal_credit_evidence(
    pool: list[dict], reference_month: date, due: date
) -> tuple[Decimal, list[dict]]:
    """Return every postpaid credit issued in the month after a competence."""
    credit_month = add_months(month_start(reference_month), 1)
    credit_month_end = add_months(credit_month, 1)
    credits = [item for item in pool if credit_month <= item["date"] < credit_month_end]
    observed = money(sum((money(item.get("raw_value", ZERO)) for item in credits), ZERO))
    evidence = [
        {
            "source": "IPIRANGA_PORTAL",
            "id": item["id"],
            "date": item["date"].isoformat(),
            "document": item.get("document"),
            "value": float(money(item.get("raw_value", ZERO))),
            "source_value": float(money(item.get("raw_value", ZERO))),
            "allocated": float(money(item.get("raw_value", ZERO))),
            "after_due_date": item["date"] > due,
            "allocation_reason": (
                "Regra mensal das unidades 003/004: crédito postecipado emitido no mês seguinte "
                f"em {credit_month.strftime('%m/%Y')} é confrontado com a competência "
                f"de {month_start(reference_month).strftime('%m/%Y')}, sem rateio por valor ou NF."
            ),
            "portal_event_key": item.get("portal_event_key"),
            "portal_match_status": item.get("portal_match_status"),
            "invoice_number": item.get("invoice_number"),
            "purchase_entry_id": item.get("purchase_entry_id"),
            "financial_entry_id": item.get("financial_entry_id"),
            "match_basis": (
                "Extrato Ipiranga: Bonificação Postecipada emitida no mês seguinte "
                "à competência contratual."
            ),
        }
        for item in credits
    ]
    return observed, evidence


def _unit_004_unique_portal_cycle_allocations(
    db: Session, rule: BonusRule, until: date
) -> dict[int, list[dict]]:
    """Return only unique, chronological portal-credit cycles for unit 004.

    The Ipiranga statement for unit 004 identifies the issued credit and its
    date, but not the competence or invoice numbers.  Its values nevertheless
    follow the contractual R$0.07/L exactly.  The approved rule therefore
    treats a credit as primary proof only when there is *one* contiguous group
    of contractual purchase NFs, between this credit and the preceding portal
    credit, whose litres reproduce the issued amount (one-cent rounding is
    allowed).  Ambiguous or non-matching events remain unallocated.
    """
    if not (
        rule.unit_code == "004"
        and rule.company_code == "IPIRANGA"
        and rule.kind == "distributor_credit"
    ):
        return {}

    purchases = db.scalars(
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(
            Purchase.unit_code == rule.unit_code,
            Purchase.mapped_company_code == rule.company_code,
            Purchase.purchase_date >= rule.effective_from,
            Purchase.purchase_date <= until,
        )
        .order_by(Purchase.purchase_date, Purchase.erp_entry_id)
    ).all()
    events = db.scalars(
        select(PortalBonusEvent)
        .where(
            PortalBonusEvent.unit_code == rule.unit_code,
            PortalBonusEvent.company_code == rule.company_code,
            PortalBonusEvent.category == "postpaid",
            PortalBonusEvent.portal_date <= until,
        )
        .order_by(PortalBonusEvent.portal_date, PortalBonusEvent.id)
    ).all()
    allocations: dict[int, list[dict]] = defaultdict(list)
    previous_credit_date = rule.effective_from - timedelta(days=1)
    rate = Decimal(rule.rate_per_liter)

    for event in events:
        window = [
            purchase
            for purchase in purchases
            if previous_credit_date <= purchase.purchase_date <= event.portal_date
        ]
        candidates: list[tuple[list[Purchase], Decimal, Decimal]] = []
        for start_index in range(len(window)):
            liters = ZERO
            for end_index in range(start_index, len(window)):
                purchase = window[end_index]
                liters += _eligible_liters(purchase, rule)
                expected_value = money(liters * rate)
                if abs(expected_value - money(event.value)) <= Decimal("0.01"):
                    candidates.append((window[start_index:end_index + 1], liters, expected_value))
                    break
                if expected_value > money(event.value) + Decimal("0.01"):
                    break

        # The next portal event starts a new observed cycle regardless of
        # whether this one was deterministically assignable.  This protects
        # against using an older ambiguous credit to fabricate a later match.
        previous_credit_date = event.portal_date
        if len(candidates) != 1:
            continue

        cycle_purchases, cycle_liters, calculated_value = candidates[0]
        invoice_numbers = [
            str(purchase.invoice_number or purchase.erp_entry_id)
            for purchase in cycle_purchases
        ]
        cycle_start = cycle_purchases[0].purchase_date
        cycle_end = cycle_purchases[-1].purchase_date
        for purchase in cycle_purchases:
            allocated = money(_eligible_liters(purchase, rule) * rate)
            if allocated <= ZERO:
                continue
            allocations[purchase.erp_entry_id].append(
                {
                    "source": "IPIRANGA_PORTAL_CYCLE",
                    "id": event.id,
                    "date": event.portal_date.isoformat(),
                    "value": float(money(event.value)),
                    "source_value": float(money(event.value)),
                    "allocated": float(allocated),
                    "document": None,
                    "company_code": "IPIRANGA",
                    "purchase_entry_id": purchase.erp_entry_id,
                    "invoice_number": purchase.invoice_number,
                    "portal_event_key": event.event_key,
                    "portal_cycle_match_status": "unique_contiguous_nf_cycle",
                    "portal_cycle_exact": True,
                    "cycle_start": cycle_start.isoformat(),
                    "cycle_end": cycle_end.isoformat(),
                    "cycle_liters": float(cycle_liters),
                    "cycle_purchase_count": len(cycle_purchases),
                    "cycle_invoices": invoice_numbers,
                    "cycle_credit_value": float(money(event.value)),
                    "cycle_calculated_value": float(calculated_value),
                    "match_basis": (
                        "Ciclo Ipiranga único: NFs contínuas de "
                        f"{cycle_start.strftime('%d/%m/%Y')} a {cycle_end.strftime('%d/%m/%Y')} "
                        f"somam {cycle_liters:,.3f} L x R$ {rate:.6f}/L = "
                        f"R$ {calculated_value:,.2f}; crédito do portal em "
                        f"{event.portal_date.strftime('%d/%m/%Y')} = R$ {money(event.value):,.2f}."
                    ),
                }
            )
    return allocations


def _s10_residual_credit_pool(db: Session, rule: BonusRule, until: date) -> list[dict]:
    """Expose residual credits after the base discount for the S10 rule."""
    purchases = db.scalars(
        select(Purchase).where(
            Purchase.unit_code == rule.unit_code,
            Purchase.mapped_company_code == rule.company_code,
            Purchase.purchase_date >= rule.effective_from,
            Purchase.purchase_date <= until,
        )
    ).all()
    _, movements_by_purchase = _purchase_movement_links(
        db, rule.unit_code, [row.erp_entry_id for row in purchases]
    )
    base_rule = db.scalar(
        select(BonusRule).where(
            BonusRule.unit_code == rule.unit_code,
            BonusRule.kind == "invoice_discount",
            BonusRule.active.is_(True),
        )
    )
    base_rate = Decimal(base_rule.rate_per_liter) if base_rule else Decimal("0.04")
    pool = []
    for purchase in purchases:
        remaining_base = money(Decimal(purchase.total_liters) * base_rate)
        for movement in movements_by_purchase.get(purchase.erp_entry_id, []):
            raw_value = abs(Decimal(movement.amount))
            base_allocated = min(remaining_base, raw_value)
            remaining_base -= base_allocated
            residual = raw_value - base_allocated
            if residual > 0:
                corroboration = _movement_corroboration(db, movement)
                pool.append(
                    {
                        "id": movement.erp_key,
                        "date": movement.movement_date,
                        "remaining": residual,
                        "raw_value": residual,
                        "document": movement.document_id,
                        "movement": movement,
                        "corroborated_by_6204": bool(corroboration),
                        "mlanf_id": corroboration.id if corroboration else None,
                        "source_reason": "Crédito residual após o desconto-base da compra contratual.",
                    }
                )
    return sorted(pool, key=lambda item: (item["date"], item["id"]))


def _consume_fifo_windowed(
    pool: list[dict], expected: Decimal, due: date, available_from: date
) -> tuple[Decimal, list[dict]]:
    observed = ZERO
    evidence: list[dict] = []
    for item in pool:
        if item["remaining"] <= 0 or item["date"] < available_from:
            continue
        amount = min(item["remaining"], max(ZERO, expected - observed))
        if amount <= 0:
            break
        item["remaining"] -= amount
        observed += amount
        evidence.append(
            {
                "source": item.get("source") or ("MDCMP" if item.get("movement") else "MLANF"),
                "id": item["id"],
                "date": item["date"].isoformat(),
                "document": item["document"],
                "allocated": float(money(amount)),
                "after_due_date": item["date"] > due,
                "allocation_reason": item.get("source_reason"),
                "financial_launch": (
                    item["movement"].financial_launch_id if item.get("movement") else None
                ),
                "value": float(money(item.get("raw_value", amount))),
                "corroborated_by_6204": item.get("corroborated_by_6204"),
                "mlanf_id": item.get("mlanf_id"),
                "portal_event_key": item.get("portal_event_key"),
                "match_status": item.get("portal_match_status"),
                "chain_exact": item.get("chain_exact"),
                "invoice_number": item.get("invoice_number"),
                "purchase_entry_id": item.get("purchase_entry_id"),
                "financial_entry_id": item.get("financial_entry_id"),
                "match_basis": item.get("match_basis"),
            }
        )
    return money(observed), evidence


def _reserve_exact_postpaid_portal_credits(
    pool: list[dict], month_specs: list[dict], rule: BonusRule
) -> dict[date, tuple[Decimal, list[dict]]]:
    """Reserve unambiguous portal credits for the preceding competence.

    Units 003 and 004 receive Ipiranga credit postpaid. A portal credit issued
    in the following calendar month with the exact expected value is stronger
    than a generic chronological-wallet allocation. Reserve it before the
    remaining balance is consumed by FIFO, otherwise an older open competence
    could split the evidence and hide the direct competence -> credit link.
    """
    if not (
        rule.kind == "distributor_credit"
        and rule.unit_code in {"003", "004"}
        and rule.company_code == "IPIRANGA"
    ):
        return {}

    reservations: dict[date, tuple[Decimal, list[dict]]] = {}
    for spec in month_specs:
        expected = money(spec["expected"])
        if expected <= ZERO:
            continue
        available_from = add_months(month_start(spec["reference_month"]), 1)
        candidates = [
            item
            for item in pool
            if item["remaining"] > ZERO
            and item["date"] >= available_from
            and item["date"] <= spec["due"]
            and money(item["remaining"]) == expected
        ]
        if len(candidates) != 1:
            continue
        item = candidates[0]
        item["remaining"] -= expected
        reservations[spec["reference_month"]] = (
            expected,
            [
                {
                    "source": item.get("source") or "IPIRANGA_PORTAL",
                    "id": item["id"],
                    "date": item["date"].isoformat(),
                    "document": item["document"],
                    "allocated": float(expected),
                    "after_due_date": False,
                    "allocation_reason": (
                        "Crédito postecipado de valor exato atribuído à competência "
                        "do mês anterior, dentro da janela contratual."
                    ),
                    "value": float(money(item.get("raw_value", expected))),
                    "corroborated_by_6204": item.get("corroborated_by_6204"),
                    "mlanf_id": item.get("mlanf_id"),
                    "portal_event_key": item.get("portal_event_key"),
                    "match_status": item.get("portal_match_status"),
                    "chain_exact": item.get("chain_exact"),
                    "invoice_number": item.get("invoice_number"),
                    "purchase_entry_id": item.get("purchase_entry_id"),
                    "financial_entry_id": item.get("financial_entry_id"),
                    "match_basis": item.get("match_basis"),
                }
            ],
        )
    return reservations


def _unassigned_portal_credit_evidence(
    pool: list[dict], available_from: date, due: date
) -> list[dict]:
    """Expose portal credits without assigning them to a competence.

    Unit 004's statement contains the credit amount and issue date but not the
    competence it settles.  A generic FIFO allocation is useful for exploring
    a ledger, but is not documentary proof.  These records therefore remain
    visible as an unassigned portal balance and never compose ``observed``.
    """
    evidence: list[dict] = []
    for item in pool:
        if item["remaining"] <= ZERO or not (available_from <= item["date"] <= due):
            continue
        evidence.append(
            {
                "source": "unassigned_portal_credit",
                "id": item["id"],
                "date": item["date"].isoformat(),
                "document": item.get("document"),
                "value": float(money(item.get("raw_value", item["remaining"]))),
                "source_value": float(money(item.get("raw_value", item["remaining"]))),
                "counted": False,
                "portal_event_key": item.get("portal_event_key"),
                "portal_match_status": item.get("portal_match_status"),
                "invoice_number": item.get("invoice_number"),
                "purchase_entry_id": item.get("purchase_entry_id"),
                "match_basis": item.get("match_basis"),
                "reason": (
                    "Crédito postecipado listado no extrato Ipiranga dentro da janela "
                    "da competência, preservado sem apropriação automática porque o "
                    "portal não informa qual competência ele liquida."
                ),
            }
        )
    return evidence


def _consume_exact_credit_windowed(
    pool: list[dict], expected: Decimal, due: date, available_from: date
) -> tuple[Decimal, list[dict]]:
    """Use one concrete credit only when its remaining value equals the expected value."""
    expected = money(expected)
    if expected <= ZERO:
        return ZERO, []
    for item in pool:
        if item["remaining"] <= ZERO or item["date"] < available_from:
            continue
        if money(item["remaining"]) != expected:
            continue
        item["remaining"] -= expected
        return expected, [
            {
                "source": "MDCMP" if item.get("movement") else "MLANF",
                "id": item["id"],
                "date": item["date"].isoformat(),
                "document": item.get("document"),
                "allocated": float(expected),
                "value": float(money(item.get("raw_value", expected))),
                "after_due_date": item["date"] > due,
                "allocation_reason": "Crédito de valor exato atribuído à competência mais antiga compatível.",
                "financial_launch": item["movement"].financial_launch_id if item.get("movement") else None,
                "corroborated_by_6204": item.get("corroborated_by_6204"),
                "mlanf_id": item.get("mlanf_id"),
            }
        ]
    return ZERO, []


def _accounting_groups(rows: list[AccountingEntry]) -> dict[tuple, list[AccountingEntry]]:
    groups: dict[tuple, list[AccountingEntry]] = defaultdict(list)
    for row in rows:
        groups[(row.journal_lot, row.journal_entry, row.unit_code, row.entry_date)].append(row)
    return groups


def _raizen_settlement_match(
    db: Session,
    unit_code: str,
    window_start: date,
    window_end: date,
    expected: Decimal,
    tolerance: Decimal,
    used_evidence: set[str] | None,
) -> tuple[Decimal, str, list[dict]] | None:
    rows = db.scalars(
        select(AccountingEntry).where(
            AccountingEntry.unit_code == unit_code,
            AccountingEntry.entry_date >= window_start,
            AccountingEntry.entry_date <= window_end,
        )
    ).all()
    for journal_rows in _accounting_groups(rows).values():
        bank_debits = [
            row for row in journal_rows
            if (row.account_code or "").startswith("111") and row.operation == "D"
        ]
        receivable_credits = [
            row for row in journal_rows if row.account_code == "1170810" and row.operation == "C"
        ]
        for bank in bank_debits:
            for receivable in receivable_credits:
                value = abs(Decimal(bank.value))
                if money(value) != money(abs(Decimal(receivable.value))):
                    continue
                if abs(value - expected) > tolerance:
                    continue
                evidence_key = f"accounting-journal:{bank.journal_lot}:{bank.journal_entry}:{unit_code}"
                if used_evidence is not None and evidence_key in used_evidence:
                    continue
                if used_evidence is not None:
                    used_evidence.add(evidence_key)
                return money(value), "direct", [
                    {
                        "source": "accounting_settlement",
                        "id": evidence_key,
                        "date": bank.entry_date.isoformat(),
                        "value": float(money(value)),
                        "bank_account": bank.account_code,
                        "bank_account_name": bank.account_name,
                        "receivable_account": receivable.account_code,
                        "receivable_account_name": receivable.account_name,
                        "debit_entry": bank.erp_key,
                        "credit_entry": receivable.erp_key,
                        "history": bank.history_text,
                        "match_basis": (
                            "Par contábil exato: baixa do crédito Raízen (1170810) "
                            "contra conta bancária no mesmo lançamento."
                        ),
                    }
                ]
    return None


def _raizen_accrual_evidence(
    db: Session, reference_month: date, expected: Decimal
) -> list[dict]:
    """Return only contractual Raízen credits recognized in accounting.

    Account 1170810 also carries historical balances and may be moved for
    reasons other than the current contract. A credit is useful as evidence
    only when the same journal records the approved appropriation pair:
    D 1170810 / C 3111001000037, history 6513 (Bonificações Distribuidoras).
    It remains non-counted because recognition of a receivable is not, by
    itself, a cash receipt or use of the credit at the distributor.
    """
    rows = db.scalars(
        select(AccountingEntry).where(
            AccountingEntry.unit_code == "054",
            AccountingEntry.entry_date >= month_start(reference_month),
            AccountingEntry.entry_date <= month_end(reference_month),
        )
    ).all()
    evidence: list[dict] = []
    for journal_rows in _accounting_groups(rows).values():
        receivable_debits = [
            row
            for row in journal_rows
            if row.account_code == "1170810"
            and row.operation == "D"
            and row.history_code == 6513
        ]
        contractual_credits = [
            row
            for row in journal_rows
            if row.account_code == "3111001000037" and row.operation == "C"
        ]
        for receivable in receivable_debits:
            value = money(abs(Decimal(receivable.value)))
            counterpart = next(
                (
                    row
                    for row in contractual_credits
                    if money(abs(Decimal(row.value))) == value
                ),
                None,
            )
            if not counterpart:
                continue
            exact_competence = value == money(expected)
            evidence.append(
                {
                    "source": "accounting_accrual",
                    "id": receivable.erp_key,
                    "date": receivable.entry_date.isoformat(),
                    "value": float(value),
                    "expected": float(money(expected)),
                    "counted": False,
                    "history": receivable.history_text or counterpart.history_text,
                    "receivable_account": receivable.account_code,
                    "receivable_account_name": receivable.account_name,
                    "counterpart_entry": counterpart.erp_key,
                    "counterpart_account": counterpart.account_code,
                    "counterpart_account_name": counterpart.account_name,
                    "history_code": receivable.history_code,
                    "allocation_status": "exact_competence" if exact_competence else "allocation_pending",
                    "reason": (
                        "Crédito contratual Raízen reconhecido na contabilidade; "
                        "o valor fecha exatamente a competência, mas ainda não comprova liquidação."
                        if exact_competence
                        else
                        "Crédito contratual Raízen reconhecido na contabilidade; "
                        "o valor não fecha esta competência e exige alocação antes de vinculá-lo a notas."
                    ),
                    "match_basis": (
                        "Par contábil de bonificação: D 1170810 / C 3111001000037 "
                        "no mesmo lote, histórico 6513 (Bonificações Distribuidoras)."
                    ),
                }
            )
    return evidence


def _bank_or_accounting_match(
    db: Session,
    company_code: str,
    unit_code: str,
    window_start: date,
    window_end: date,
    expected: Decimal,
    used_evidence: set[str] | None = None,
) -> tuple[Decimal, str, list[dict]]:
    if expected <= 0:
        return ZERO, "none", []
    terms = COMPANY_TERMS.get(company_code, ())
    # Uma aproximação de 2% é útil como candidato visual, mas não é prova.
    # Somente igualdade em centavos pode alimentar o valor automaticamente.
    tolerance = Decimal("0.00")
    bank_rows = db.scalars(
        select(BankEntry).where(BankEntry.entry_date >= window_start, BankEntry.entry_date <= window_end)
    ).all()
    candidates = []
    for row in bank_rows:
        evidence_key = f"bank:{row.id}"
        if used_evidence is not None and evidence_key in used_evidence:
            continue
        text = (row.history_text or "").upper()
        value = abs(Decimal(row.value))
        named = any(term in text for term in terms)
        close = abs(value - expected) <= tolerance
        if named or close:
            candidates.append((row, named, close, value))
    direct = [item for item in candidates if item[1] and item[2]]
    chosen = direct[:1]
    if chosen:
        row, named, close, value = chosen[0]
        if used_evidence is not None:
            used_evidence.add(f"bank:{row.id}")
        return money(value), "direct", [
            {
                "source": "MExtratoBancoLanc",
                "id": row.id,
                "date": row.entry_date.isoformat(),
                "value": float(value),
                "history": (row.history_text or "")[:240],
            }
        ]

    # Política aprovada: uma ocorrência única com valor exato, documento ou
    # histórico e janela própria da competência é suficiente para concluir a
    # conciliação. A origem e a observação permanecem expostas no painel.
    exact_candidates = [item for item in candidates if item[2] and (item[0].history_text or item[0].document)]
    if len(exact_candidates) == 1:
        row, _, _, value = exact_candidates[0]
        if used_evidence is not None:
            used_evidence.add(f"bank:{row.id}")
        return money(value), "value_exact", [
            {
                "source": "MExtratoBancoLanc",
                "id": row.id,
                "date": row.entry_date.isoformat(),
                "value": float(money(value)),
                "history": (row.history_text or "")[:240],
                "bank_document": row.document,
                "match_basis": "Valor exato, ocorrência única na janela da competência e histórico/documento bancário preservados.",
            }
        ]

    if company_code == "SHELL":
        settlement = _raizen_settlement_match(
            db, unit_code, window_start, window_end, expected, tolerance, used_evidence
        )
        if settlement:
            return settlement
    return ZERO, "none", []


def _raizen_receipt_match(
    db: Session,
    rule: BonusRule,
    reference_month: date,
    due_date: date,
    expected: Decimal,
) -> tuple[Decimal, str, list[dict]]:
    """Use an imported Raízen bank receipt only when its declared allocation is exact.

    The source PDF is retained through ``PortalStatementImport`` and every
    receipt event carries its original bank commitments plus an explicit map of
    the contractual competences it settles.  This is deliberately limited to
    unit 054: a same-value credit in another Shell unit must never be reused.
    """
    if not (
        rule.kind == "bank_deposit"
        and rule.company_code == "SHELL"
        and rule.unit_code == "054"
        and expected > ZERO
    ):
        return ZERO, "none", []

    events = db.scalars(
        select(PortalBonusEvent)
        .where(
            PortalBonusEvent.unit_code == "054",
            PortalBonusEvent.company_code == "SHELL",
            PortalBonusEvent.category == RAIZEN_RECEIPT_CATEGORY,
        )
        .order_by(PortalBonusEvent.portal_date, PortalBonusEvent.id)
    ).all()
    month_key = month_start(reference_month).isoformat()
    for event in events:
        try:
            metadata = json.loads(event.review_notes or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        allocations = metadata.get("allocations") or {}
        try:
            declared_total = money(sum((Decimal(str(value)) for value in allocations.values()), ZERO))
        except (AttributeError, ValueError, ArithmeticError):
            continue
        source_total = money(Decimal(event.value or 0))
        # A consolidated receipt may serve several competences, but its
        # declared allocations must always exhaust the real bank credit.
        if declared_total != source_total:
            continue
        allocation_value = allocations.get(month_key)
        if allocation_value is None:
            continue
        allocated = money(Decimal(str(allocation_value)))
        if allocated != money(expected):
            # ERP corrections can change a competence after the receipt has
            # been imported. Do not silently use an older allocation value.
            continue
        days_after_due = (event.portal_date - due_date).days
        commitment_bank = metadata.get("bank_commitment")
        commitment_client = metadata.get("client_commitment")
        receipt_label = (
            f"TED Raízen {commitment_bank or 'sem compromisso bancário'}"
            f" / cliente {commitment_client or 'sem compromisso cliente'}"
        )
        return allocated, "direct", [
            {
                "source": "RAIZEN_PAYMENT_RECEIPT",
                "id": event.id,
                "date": event.portal_date.isoformat(),
                "document": commitment_client or commitment_bank,
                "value": float(allocated),
                "allocated": float(allocated),
                "source_value": float(source_total),
                "receipt_total": float(source_total),
                "payer_name": metadata.get("payer_name") or "Raízen S.A.",
                "payer_cnpj": metadata.get("payer_cnpj"),
                "beneficiary_name": metadata.get("beneficiary_name"),
                "beneficiary_cnpj": metadata.get("beneficiary_cnpj"),
                "bank": metadata.get("bank"),
                "agency": metadata.get("agency"),
                "account": metadata.get("account"),
                "import_id": metadata.get("import_id"),
                "after_due_days": days_after_due,
                "is_consolidated": source_total != allocated,
                "match_basis": (
                    "Comprovante Santander da Raízen: CNPJ do pagador e CNPJ da unidade conferidos; "
                    f"valor {'consolidado' if source_total != allocated else 'individual'} alocado exatamente à competência."
                ),
                "history": receipt_label,
            }
        ]
    return ZERO, "none", []


def _upsert(
    db: Session,
    rule: BonusRule,
    reference_month: date,
    due: date,
    expected: Decimal,
    observed: Decimal,
    confidence: str,
    evidence: list[dict],
    today: date,
) -> Reconciliation:
    row = db.scalar(
        select(Reconciliation).where(
            Reconciliation.rule_id == rule.id,
            Reconciliation.reference_month == reference_month,
        )
    )
    if not row:
        row = Reconciliation(
            unit_code=rule.unit_code,
            rule_id=rule.id,
            reference_month=reference_month,
            due_date=due,
            status="pending",
        )
        db.add(row)
    serialized_evidence = json.dumps(evidence, ensure_ascii=False, default=str)
    was_reviewed = row.confirmed_at is not None
    review_became_stale = was_reviewed and (
        money(Decimal(row.expected_value or 0)) != money(expected)
        or money(Decimal(row.observed_value or 0)) != money(observed)
        or row.confidence != confidence
        or (row.evidence_json or "[]") != serialized_evidence
    )
    row.unit_code = rule.unit_code
    row.due_date = due
    row.expected_value = money(expected)
    row.observed_value = money(observed)
    row.difference_value = money(expected - observed - Decimal(row.manual_adjustment or 0))
    row.confidence = confidence
    row.evidence_json = serialized_evidence
    if review_became_stale:
        row.confirmed_at = None
        row.confirmed_by = None
        row.notes = "Reaberta automaticamente: os valores ou vínculos do ERP mudaram após a revisão anterior."
    if row.confirmed_at:
        row.status = "confirmed"
    else:
        row.status = reconciliation_status(
            money(expected), money(observed + Decimal(row.manual_adjustment or 0)), due, today
        )
        if confidence == "probable" and row.status == "confirmed":
            row.status = "partial"
    return row


def _rebuild_monthly_rule(db: Session, rule: BonusRule, today: date) -> int:
    count = 0
    months = list(_months(rule.effective_from, min(today, rule.effective_to or today)))
    next_month_portal_rule = uses_next_month_ipiranga_portal_credit(rule)
    if rule.kind == "distributor_credit":
        portal_pool = (
            _portal_credit_pool(db, rule, today)
            if rule.company_code == "IPIRANGA" and rule.unit_code != "001"
            else []
        )
        # Unidades 003/004: a prova aprovada é exclusivamente a emissão do
        # crédito no extrato Ipiranga. Descontos MDCMP continuam no ERP para
        # auditoria, mas não podem confirmar nem complementar a bonificação.
        portal_primary = next_month_portal_rule
        pool = portal_pool if portal_primary else portal_pool or _contract_credit_pool(db, rule, today)
    elif rule.kind == "s10_excess_credit":
        pool = _s10_residual_credit_pool(db, rule, today)
    else:
        pool = []
    month_specs = []
    for reference_month in months:
        total_liters, s10_liters, entry_ids = _purchase_totals(db, rule, reference_month)
        if rule.kind == "s10_excess_credit":
            expected = max(ZERO, s10_liters - Decimal(rule.threshold_liters or 0)) * Decimal(rule.rate_per_liter)
        else:
            expected = total_liters * Decimal(rule.rate_per_liter)
        month_specs.append(
            {
                "reference_month": reference_month,
                "total_liters": total_liters,
                "s10_liters": s10_liters,
                "entry_ids": entry_ids,
                "expected": money(expected),
                "due": due_date_for_month(reference_month, rule.due_month_offset, rule.due_day),
            }
        )
    exact_portal_reservations = (
        {} if next_month_portal_rule else _reserve_exact_postpaid_portal_credits(pool, month_specs, rule)
    )
    used_evidence: set[str] = set()
    for spec in month_specs:
        reference_month = spec["reference_month"]
        total_liters = spec["total_liters"]
        s10_liters = spec["s10_liters"]
        entry_ids = spec["entry_ids"]
        expected = spec["expected"]
        due = spec["due"]
        if rule.kind == "invoice_discount":
            observed, evidence = _document_discounts_capped(db, rule, entry_ids)
            confidence = "direct" if observed > ZERO and any(item.get("source") == "MDCMP" for item in evidence) else "none"
        elif rule.kind == "distributor_credit":
            if next_month_portal_rule:
                observed, evidence = _next_month_ipiranga_portal_credit_evidence(
                    pool, reference_month, due
                )
            else:
                observed = ZERO
                evidence = []
            available_from = month_start(reference_month)
            if not next_month_portal_rule and reference_month in exact_portal_reservations:
                observed, evidence = exact_portal_reservations[reference_month]
            elif not next_month_portal_rule:
                observed, evidence = _consume_fifo_windowed(pool, expected, due, available_from)
            # O crédito permanece auditável. Quando a origem inteira tem o
            # mesmo valor da competência, a política do workspace o confirma.
            confidence = "direct" if observed > ZERO and evidence else "none"
        elif rule.kind == "s10_excess_credit":
            available_from = add_months(month_start(reference_month), 1)
            observed, evidence = _consume_exact_credit_windowed(pool, expected, due, available_from)
            if not evidence:
                candidates = [item for item in pool if item["date"] >= available_from]
                evidence = [
                    {
                        "source": "unclassified_credit",
                        "id": item["id"],
                        "date": item["date"].isoformat(),
                        "document": item["document"],
                        "value": float(money(item["remaining"])),
                        "reason": item["source_reason"],
                        "counted": False,
                    }
                    for item in candidates[:20]
                ]
            confidence = "direct" if observed > ZERO else "none"
        else:  # bank_deposit
            observed, confidence, evidence = _raizen_receipt_match(
                db, rule, reference_month, due, expected
            )
            if not evidence:
                observed, confidence, evidence = _bank_or_accounting_match(
                    db,
                    rule.company_code,
                    rule.unit_code,
                    add_months(reference_month, 1),
                    max(due, month_end(add_months(reference_month, 1))),
                    expected,
                    used_evidence,
                )
            if rule.company_code == "SHELL" and not any(
                item.get("source") == "RAIZEN_PAYMENT_RECEIPT" for item in evidence
            ):
                evidence.extend(_raizen_accrual_evidence(db, reference_month, expected))
        _upsert(db, rule, reference_month, due, expected, observed, confidence, evidence, today)
        count += 1
    # A regra pode ganhar uma vigÃªncia mais precisa depois do backfill. NÃ£o
    # apagamos as linhas antigas (a auditoria e ajustes permanecem), mas elas
    # deixam de alimentar a fila e os totais operacionais.
    for stale in db.scalars(
        select(Reconciliation).where(
            Reconciliation.rule_id == rule.id,
            Reconciliation.reference_month < month_start(rule.effective_from),
        )
    ).all():
        stale.expected_value = ZERO
        stale.observed_value = ZERO
        stale.manual_adjustment = ZERO
        stale.difference_value = ZERO
        stale.status = "superseded"
        stale.confidence = "none"
        stale.confirmation_mode = "none"
        stale.evidence_json = "[]"
        stale.notes = "Substituida: competencia anterior a vigencia comprovada da regra."
        for exception in db.scalars(
            select(ReconciliationException).where(
                ReconciliationException.reconciliation_id == stale.id,
                ReconciliationException.status.in_(("open", "in_review")),
            )
        ).all():
            exception.status = "resolved"
            exception.resolution_code = "superseded_rule_effective_from"
            exception.resolution_notes = stale.notes
            exception.resolved_at = datetime.now(timezone.utc)
    return count


def _rebuild_ipiranga_portal_rule(db: Session, rule: BonusRule, today: date) -> int:
    """Materialize 26-25 cycles using NF issue date and portal-origin evidence."""
    from app.services.ipiranga_portal import build_ipiranga_cycle_ledger, match_ipiranga_events

    match_ipiranga_events(db, rule.unit_code)
    ledger = build_ipiranga_cycle_ledger(db, rule, today)
    active_months = set()
    for cycle in ledger["cycles"]:
        active_months.add(cycle["reference_month"])
        existing = db.scalar(
            select(Reconciliation).where(
                Reconciliation.rule_id == rule.id,
                Reconciliation.reference_month == cycle["reference_month"],
            )
        )
        adjustment = full_management_adjustment(db, existing) if existing else None
        row = _upsert(
            db,
            rule,
            cycle["reference_month"],
            cycle["due_date"],
            cycle["expected"],
            ZERO if adjustment else cycle["observed"],
            "none" if adjustment else cycle["confidence"],
            [full_management_adjustment_evidence(existing, adjustment)] if adjustment else cycle["evidence"],
            today,
        )
        if adjustment:
            row.status = "confirmed"
            row.confirmation_mode = "manual"
            row.confirmed_at = row.confirmed_at or adjustment.created_at
            row.confirmed_by = row.confirmed_by or adjustment.created_by
            row.notes = (
                "Competência encerrada por ajuste gerencial histórico integral; "
                "não representa crédito emitido pela Ipiranga ou identificado no ERP."
            )
    stale = db.scalars(
        select(Reconciliation).where(
            Reconciliation.rule_id == rule.id,
            ~Reconciliation.reference_month.in_(active_months) if active_months else True,
        )
    ).all()
    for row in stale:
        # Preserve reviews and manual adjustments as an immutable audit trail.
        # Calendar-month rows superseded by the contractual 26-25 ledger are
        # hidden from the active workspace instead of being physically deleted.
        row.expected_value = Decimal("0")
        row.observed_value = Decimal("0")
        row.manual_adjustment = Decimal("0")
        row.difference_value = Decimal("0")
        row.status = "superseded"
        row.confidence = "none"
        row.confirmation_mode = "none"
        row.evidence_json = "[]"
        row.notes = "Substituída pelo livro contratual Ipiranga com ciclos de 26 a 25."
        for exception in db.scalars(
            select(ReconciliationException).where(
                ReconciliationException.reconciliation_id == row.id,
                ReconciliationException.status.in_(("open", "in_review")),
            )
        ).all():
            exception.status = "resolved"
            exception.resolution_code = "superseded_cycle"
            exception.resolution_notes = row.notes
            exception.resolved_at = datetime.now(timezone.utc)
    db.flush()
    return len(ledger["cycles"])


def _umbrella_totals_at(db: Session, cutoff: date) -> dict[str, Decimal]:
    purchases = db.scalars(
        select(Purchase).where(
            Purchase.unit_code.in_(("002", "006")),
            Purchase.mapped_company_code == "BR",
            Purchase.purchase_date >= date(2025, 2, 1),
            Purchase.purchase_date <= cutoff,
        )
    ).all()
    _, totals, _ = allocate_umbrella(
        [UmbrellaPurchase(row.erp_entry_id, row.unit_code, row.purchase_date, Decimal(row.total_liters)) for row in purchases]
    )
    return totals


def _br_receipt_pool(db: Session, today: date) -> list[dict]:
    """Build one non-reusable receipt ledger for the 002/006 umbrella."""
    rows = db.scalars(
        select(AccountingEntry).where(
            AccountingEntry.unit_code.in_(("002", "006")),
            AccountingEntry.entry_date >= date(2025, 2, 1),
            AccountingEntry.entry_date <= today,
        )
    ).all()
    pool: list[dict] = []
    accounting_signatures: set[tuple[date, Decimal]] = set()
    for journal_rows in _accounting_groups(rows).values():
        bank_debits = [
            row for row in journal_rows
            if (row.account_code or "").startswith("111") and row.operation == "D"
        ]
        revenue_credits = [
            row for row in journal_rows
            if row.account_code == "3111001000037" and row.operation == "C"
        ]
        for bank in bank_debits:
            counterpart = next(
                (
                    row for row in revenue_credits
                    if abs(abs(Decimal(row.value)) - abs(Decimal(bank.value))) <= Decimal("0.01")
                ),
                None,
            )
            if not counterpart:
                continue
            value = money(abs(Decimal(bank.value)))
            if value <= ZERO or value % Decimal("35000.00") != ZERO:
                continue
            text = " ".join((item.history_text or "") for item in journal_rows).upper()
            named = any(term in text for term in ("VIBRA", "PETROBRAS", "BR DISTRIBUIDORA"))
            # O par banco (D) / receita (C), com a palavra bonificação, prova a
            # entrada mesmo quando o histórico não traz o nome da Vibra.
            # "Reforma posto" isoladamente não é suficiente para esta regra.
            generic_bonus = "BONIF" in text
            if not named and not generic_bonus:
                continue
            evidence_id = f"accounting-journal:{bank.journal_lot}:{bank.journal_entry}:{bank.unit_code}"
            pool.append(
                {
                    "id": evidence_id,
                    "date": bank.entry_date,
                    "remaining": value,
                    "source_value": value,
                    "confidence": "direct",
                    "source": "accounting_receipt",
                    "history": bank.history_text,
                    "center": bank.unit_code,
                    "bank_account": bank.account_code,
                    "revenue_account": counterpart.account_code,
                    "match_basis": (
                        "Par contábil direto banco/receita com contraparte Vibra identificada."
                        if named
                        else "Par contábil direto banco/receita e histórico explícito de bonificação."
                    ),
                }
            )
            accounting_signatures.add((bank.entry_date, value))

    bank_rows = db.scalars(
        select(BankEntry).where(
            BankEntry.entry_date >= date(2025, 2, 1),
            BankEntry.entry_date <= today,
        )
    ).all()
    for row in bank_rows:
        text = (row.history_text or "").upper()
        value = money(abs(Decimal(row.value)))
        if "VIBRA" not in text or value <= ZERO or value % Decimal("35000.00") != ZERO:
            continue
        if (row.entry_date, value) in accounting_signatures:
            continue
        pool.append(
            {
                "id": f"bank:{row.id}",
                "date": row.entry_date,
                "remaining": value,
                "source_value": value,
                "confidence": "direct",
                "source": "MExtratoBancoLanc",
                "history": row.history_text,
                "center": None,
                "bank_id": row.id,
                "bank_document": row.document,
                "bank_account": row.account_number_masked,
                "counterparty": "Vibra Energia",
                "match_basis": (
                    "Extrato bancário com contraparte Vibra Energia e valor exato em múltiplo de "
                    "R$ 35.000,00; regra operacional BR confirmada pela empresa."
                ),
            }
        )
    return sorted(pool, key=lambda item: (item["date"], item["id"]))


def _consume_br_receipts(pool: list[dict], expected: Decimal, period_close: date, due: date):
    observed = ZERO
    evidence: list[dict] = []
    qualities: list[str] = []
    for item in pool:
        if item["remaining"] <= ZERO:
            continue
        allocated = min(item["remaining"], expected - observed)
        if allocated <= ZERO:
            break
        item["remaining"] -= allocated
        observed += allocated
        before_close = item["date"] < period_close
        # O marco é atribuído cronologicamente dentro do guarda-chuva. A data
        # do recebimento não altera a prova de que a bonificação foi recebida:
        # ela apenas permanece visível para auditoria no painel.
        quality = "probable" if item["confidence"] == "probable" else "direct"
        qualities.append(quality)
        evidence.append(
            {
                "source": item["source"],
                "id": item["id"],
                "date": item["date"].isoformat(),
                "value": float(money(allocated)),
                "source_value": float(money(item.get("source_value", allocated))),
                "history": item.get("history"),
                "center": item.get("center"),
                "bank_account": item.get("bank_account"),
                "bank_document": item.get("bank_document"),
                "counterparty": item.get("counterparty"),
                "revenue_account": item.get("revenue_account"),
                "before_period_close": before_close,
                "after_due_date": item["date"] > due,
                "match_basis": item.get("match_basis") or (
                    "Recebimento único do guarda-chuva 002/006, alocado ao marco mais antigo em aberto"
                ),
                "allocation_reason": "Recebimento único do guarda-chuva 002/006, alocado ao marco mais antigo em aberto",
                "quality": quality,
            }
        )
    confidence = "none" if not qualities else "probable" if "probable" in qualities else "direct"
    return money(observed), confidence, evidence


def _rebuild_milestone_group(db: Session, rules: list[BonusRule], today: date) -> int:
    events = []
    for rule in rules:
        period_months = rule.period_months or 4
        period_start = rule.effective_from
        previous_milestones = 0
        while True:
            period_close = add_months(period_start, period_months) - timedelta(days=1)
            if period_close > today or (rule.effective_to and period_close > rule.effective_to):
                break
            totals = _umbrella_totals_at(db, period_close)
            current_milestones = br_milestones(
                totals.get(rule.unit_code, ZERO), Decimal(rule.milestone_liters or 440000)
            )
            new_milestones = max(0, current_milestones - previous_milestones)
            reference_month = month_start(period_close)
            due = due_date_for_month(reference_month, rule.due_month_offset, rule.due_day)
            events.append(
                {
                    "rule": rule,
                    "period_start": period_start,
                    "period_close": period_close,
                    "reference_month": reference_month,
                    "due": due,
                    "allocated_liters": totals.get(rule.unit_code, ZERO),
                    "new_milestones": new_milestones,
                    "expected": money(Decimal(new_milestones) * Decimal(rule.milestone_amount or 0)),
                }
            )
            previous_milestones = current_milestones
            period_start = add_months(period_start, period_months)

    # A ordem é do guarda-chuva, não uma busca independente por unidade.
    events.sort(key=lambda item: (item["period_close"], item["rule"].unit_code))
    pool = _br_receipt_pool(db, today)
    for event in events:
        observed, confidence, evidence = _consume_br_receipts(
            pool, event["expected"], event["period_close"], event["due"]
        )
        evidence.insert(
            0,
            {
                "source": "calculation",
                "period_start": event["period_start"].isoformat(),
                "period_end": event["period_close"].isoformat(),
                "allocated_liters": float(event["allocated_liters"]),
                "new_milestones": event["new_milestones"],
                "umbrella_group": "BR_002_006",
            },
        )
        _upsert(
            db,
            event["rule"],
            event["reference_month"],
            event["due"],
            event["expected"],
            observed,
            confidence,
            evidence,
            today,
        )
    return len(events)


def _rebuild_milestone_rule(db: Session, rule: BonusRule, today: date) -> int:
    count = 0
    period_months = rule.period_months or 4
    period_start = rule.effective_from
    previous_milestones = 0
    used_evidence: set[str] = set()
    while True:
        period_close = add_months(period_start, period_months) - timedelta(days=1)
        if period_close > today or (rule.effective_to and period_close > rule.effective_to):
            break
        totals = _umbrella_totals_at(db, period_close)
        current_milestones = br_milestones(
            totals.get(rule.unit_code, ZERO),
            Decimal(rule.milestone_liters or 440000),
        )
        new_milestones = max(0, current_milestones - previous_milestones)
        expected = money(Decimal(new_milestones) * Decimal(rule.milestone_amount or 0))
        reference_month = month_start(period_close)
        due = due_date_for_month(reference_month, rule.due_month_offset, rule.due_day)
        observed, confidence, evidence = _bank_or_accounting_match(
            db,
            rule.company_code,
            rule.unit_code,
            add_months(reference_month, 1),
            month_end(add_months(reference_month, 1)),
            expected,
            used_evidence,
        )
        evidence.insert(
            0,
            {
                "source": "calculation",
                "period_start": period_start.isoformat(),
                "period_end": period_close.isoformat(),
                "allocated_liters": float(totals.get(rule.unit_code, ZERO)),
                "new_milestones": new_milestones,
            },
        )
        _upsert(db, rule, reference_month, due, expected, observed, confidence, evidence, today)
        previous_milestones = current_milestones
        period_start = add_months(period_start, period_months)
        count += 1
    return count


def rebuild_reconciliations(
    db: Session,
    today: date | None = None,
    *,
    commit: bool = True,
) -> int:
    today = today or date.today()
    inactive_rows = db.scalars(
        select(Reconciliation)
        .join(BonusRule, Reconciliation.rule_id == BonusRule.id)
        .where(BonusRule.active.is_(False), Reconciliation.status != "superseded")
    ).all()
    for row in inactive_rows:
        row.status = "superseded"
        row.confirmation_mode = "none"
        row.notes = "Substituida: a regra contratual foi desativada."
        for exception in db.scalars(
            select(ReconciliationException).where(
                ReconciliationException.reconciliation_id == row.id,
                ReconciliationException.status.in_(("open", "in_review")),
            )
        ).all():
            exception.status = "resolved"
            exception.resolution_code = "superseded_inactive_rule"
            exception.resolution_notes = row.notes
            exception.resolved_at = datetime.now(timezone.utc)
    rules = db.scalars(select(BonusRule).where(BonusRule.active.is_(True)).order_by(BonusRule.id)).all()
    count = 0
    umbrella_rules = [
        rule for rule in rules
        if rule.kind == "milestone_bonus" and rule.unit_code in {"002", "006"}
    ]
    if umbrella_rules:
        count += _rebuild_milestone_group(db, umbrella_rules, today)
    for rule in rules:
        if rule.kind == "milestone_bonus":
            if rule in umbrella_rules:
                continue
            count += _rebuild_milestone_rule(db, rule, today)
        elif (
            rule.kind == "distributor_credit"
            and rule.company_code == "IPIRANGA"
            and rule.unit_code in {"001", "008"}
        ):
            count += _rebuild_ipiranga_portal_rule(db, rule, today)
        else:
            count += _rebuild_monthly_rule(db, rule, today)
    # Materializa a camada de auditoria somente depois de todas as regras terem
    # consumido seus livros globais. Ela é a autoridade para o status automático.
    from app.services.reconciliation_workspace import rebuild_reconciliation_workspace

    rebuild_reconciliation_workspace(db, today)
    if commit:
        db.commit()
    else:
        db.flush()
    return count
