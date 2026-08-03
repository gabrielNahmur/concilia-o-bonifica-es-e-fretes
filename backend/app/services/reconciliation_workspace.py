from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    BonusRule,
    FinancialEntry,
    InvoiceBoletoEvidence,
    PayableDocument,
    PayableMovement,
    Purchase,
    Reconciliation,
    ReconciliationAllocation,
    ReconciliationEvidence,
    ReconciliationException,
    ReconciliationItem,
)
from app.services.reconciliation import invoice_discount_forfeited_by_late_payment
from app.services.rules import money, month_end, month_start, reconciliation_status


ALGORITHM_VERSION = "value-evidence-v3.7-raizen"
# Valores já chegam quantizados em centavos. A política aprovada considera a
# conciliação concluída quando as evidências vinculadas fecham exatamente o
# valor esperado; os elos técnicos disponíveis seguem exibidos para auditoria.
CENT_TOLERANCE = Decimal("0.00")
# A management decision can remove a specific document from the contractual
# scope without erasing the ERP purchase/payment trail.  This is deliberately
# distinct from a confirmation: it never says that a bonus was received.
CONTRACTUAL_EXCLUSION_REVIEW_STATUS = "excluded_contractually"
NON_COUNTED_SOURCES = {
    "calculation",
    "accounting_accrual",
    "unclassified_credit",
    "MDCDP.Vlr_OutAbt",
    "BOLETO_PDF",
}
ITEM_LABELS = {
    "distributor_credit": "Crédito na distribuidora",
    "milestone_bonus": "Marco quadrimestral BR",
    "invoice_discount": "Desconto em boleto",
    "s10_excess_credit": "Crédito adicional S10",
    "bank_deposit": "Depósito em conta",
}


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _is_br_umbrella_rule(rule: BonusRule) -> bool:
    """Only the two BR contracts may surface umbrella calculation evidence."""
    return rule.kind == "milestone_bonus" and rule.unit_code in {"002", "006"}


def _evidence_for_rule(rule: BonusRule, evidence: list[dict]) -> list[dict]:
    """Drop legacy BR-calculation artifacts from unrelated contractual rules.

    ``calculation`` is an informative allocation record, not a financial proof.
    It has meaning exclusively for the chronological 002/006 BR umbrella.  Old
    materialized rows can still contain it after a rule was changed, so the
    workspace must not expose or count it for Ipiranga, Texaco, or Raizen.
    The unmodified source remains in ``Reconciliation.evidence_json`` for the
    administrator's technical audit trail.
    """
    if _is_br_umbrella_rule(rule):
        return evidence
    return [item for item in evidence if item.get("source") != "calculation"]


def _fingerprint(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _eligible_liters(purchase: Purchase, rule: BonusRule) -> Decimal:
    if not (rule.applies_to or "").startswith("fuel_codes:"):
        return _decimal(purchase.total_liters)
    codes = {value.strip() for value in rule.applies_to.split(":", 1)[1].split(",") if value.strip()}
    return sum((_decimal(item.quantity) for item in purchase.items if str(item.item_code) in codes), Decimal("0"))


def _source_identity(payload: dict) -> tuple[str, str]:
    source = str(payload.get("source") or "unknown")
    key = payload.get("id") or payload.get("mdcmp_id") or payload.get("bank_id")
    if not key and payload.get("debit_entry") and payload.get("credit_entry"):
        key = f"{payload['debit_entry']}:{payload['credit_entry']}"
    if not key:
        stable = {
            name: payload.get(name)
            for name in ("date", "document", "financial_launch", "financial_sequence", "batch", "value", "history")
            if payload.get(name) is not None
        }
        key = _fingerprint(stable)[:40]
    if source == "unclassified_credit":
        key = f"unclassified:{key}"
    return source, str(key)


def _evidence_amount(payload: dict) -> tuple[Decimal, Decimal, bool]:
    source = str(payload.get("source") or "unknown")
    counted = payload.get("counted") is not False and source not in NON_COUNTED_SOURCES
    source_value = money(_decimal(payload.get("source_value", payload.get("value", payload.get("allocated", 0)))))
    allocated = money(_decimal(payload.get("allocated", payload.get("value", 0)))) if counted else Decimal("0")
    return source_value, allocated, counted


def _upsert_evidence(db: Session, row: Reconciliation, payload: dict) -> ReconciliationEvidence:
    source, source_key = _source_identity(payload)
    source_value, _, counted = _evidence_amount(payload)
    identity = dict(payload)
    fingerprint = _fingerprint(identity)
    evidence = db.scalar(
        select(ReconciliationEvidence).where(
            ReconciliationEvidence.source_type == source,
            ReconciliationEvidence.source_key == source_key,
        )
    )
    if not evidence:
        evidence = ReconciliationEvidence(source_type=source, source_key=source_key)
        db.add(evidence)
    evidence.evidence_date = _date(payload.get("date") or payload.get("payment_date"))
    evidence.unit_code = row.unit_code
    evidence.document_id = str(payload.get("document"))[:80] if payload.get("document") else None
    evidence.value = source_value
    evidence.counted = counted
    evidence.identity_json = _json(identity)
    evidence.fingerprint = fingerprint
    db.flush()
    return evidence


def _invoice_context(db: Session, row: Reconciliation, rule: BonusRule):
    purchases = db.scalars(
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(
            Purchase.unit_code == row.unit_code,
            Purchase.mapped_company_code == rule.company_code,
            Purchase.purchase_date >= month_start(row.reference_month),
            Purchase.purchase_date <= month_end(row.reference_month),
            Purchase.purchase_date >= rule.effective_from,
        )
        .order_by(Purchase.purchase_date, Purchase.erp_entry_id)
    ).all()
    if rule.effective_to:
        purchases = [item for item in purchases if item.purchase_date <= rule.effective_to]
    ids = [item.erp_entry_id for item in purchases]
    documents = db.scalars(
        select(PayableDocument).where(PayableDocument.erp_entry_id.in_(ids) if ids else False)
    ).all()
    docs_by_entry: dict[int, list[PayableDocument]] = defaultdict(list)
    title_to_entries: dict[tuple, set[int]] = defaultdict(set)
    for document in documents:
        docs_by_entry[document.erp_entry_id].append(document)
        title_to_entries[(
            document.unit_code,
            document.person_id,
            document.title_type,
            document.document_id,
            document.sequence,
        )].add(document.erp_entry_id)
    document_ids = sorted({item.document_id for item in documents if item.document_id})
    movements = db.scalars(
        select(PayableMovement).where(
            PayableMovement.unit_code == row.unit_code,
            PayableMovement.document_id.in_(document_ids) if document_ids else False,
        )
    ).all()
    movements_by_entry: dict[int, list[PayableMovement]] = defaultdict(list)
    movement_to_entry: dict[str, int] = {}
    for movement in movements:
        targets = title_to_entries.get((
            movement.unit_code,
            movement.person_id,
            movement.title_type,
            movement.document_id,
            movement.document_sequence,
        ), set())
        if len(targets) == 1:
            entry_id = next(iter(targets))
            movements_by_entry[entry_id].append(movement)
            movement_to_entry[movement.erp_key] = entry_id
    return purchases, docs_by_entry, movements_by_entry, movement_to_entry


def _financial_corroboration_is_exact(
    db: Session, movement: PayableMovement, history_code: int
) -> bool:
    if not movement.financial_launch_id:
        return False
    statement = select(FinancialEntry).where(
        FinancialEntry.erp_launch_id == movement.financial_launch_id,
        FinancialEntry.history_code == history_code,
        FinancialEntry.unit_code == movement.unit_code,
        FinancialEntry.document_id == movement.document_id,
    )
    matches = db.scalars(statement).all()
    exact_value = [
        item for item in matches
        if money(abs(_decimal(item.value))) == money(abs(_decimal(movement.amount)))
    ]
    # Sq_LanFin em MDCMP é uma sequência operacional da baixa e não o campo
    # MLANF.Seq. A identidade determinística é lançamento + unidade + documento
    # + histórico 6204 + valor, exigindo candidato único.
    return len(exact_value) == 1


def _corroboration_is_exact(db: Session, movement: PayableMovement) -> bool:
    return _financial_corroboration_is_exact(db, movement, 6204)


def _payment_corroboration_is_exact(db: Session, movement: PayableMovement) -> bool:
    return _financial_corroboration_is_exact(db, movement, 51)


def _has_exact_payment(
    db: Session, movements: list[PayableMovement], discount: PayableMovement
) -> bool:
    matches = [
        item
        for item in movements
        if (
            item.movement_type == "B"
            and item.reversal_sequence == 0
            and item.payment_sequence == discount.payment_sequence
        )
    ]
    return len(matches) == 1 and _payment_corroboration_is_exact(db, matches[0])


def _base_item_payloads(db: Session, row: Reconciliation, rule: BonusRule):
    raw_evidence = _evidence_for_rule(rule, json.loads(row.evidence_json or "[]"))
    if rule.kind != "invoice_discount":
        # A zero/zero month carries no financial action.  It is deliberately
        # absent from the operational queue rather than labelled as pending.
        if (
            money(row.expected_value) <= 0
            and money(_decimal(row.observed_value) + _decimal(row.manual_adjustment)) <= 0
        ):
            return []
        return [
            {
                "source_key": f"{rule.kind}:{row.reference_month.isoformat()}",
                "item_type": rule.kind,
                "source_date": row.reference_month,
                "source_document": None,
                "description": f"{ITEM_LABELS.get(rule.kind, rule.kind)} - competência {row.reference_month.strftime('%m/%Y')}",
                "expected": money(row.expected_value),
                "evidence": raw_evidence,
                "details": {"aggregate": True, "rule_kind": rule.kind},
                "automatic_kind": rule.kind == "bank_deposit",
            }
        ]

    purchases, docs_by_entry, movements_by_entry, movement_to_entry = _invoice_context(db, row, rule)
    from app.services.boleto_evidence import boleto_payload

    boleto_rows = db.scalars(
        select(InvoiceBoletoEvidence).where(
            InvoiceBoletoEvidence.reconciliation_id == row.id,
        )
    ).all()
    boletos_by_entry: dict[int, list[dict]] = defaultdict(list)
    for boleto in boleto_rows:
        if boleto.purchase_entry_id is not None:
            boletos_by_entry[boleto.purchase_entry_id].append(boleto_payload(boleto))
    payloads = []
    evidence_by_entry: dict[int, list[dict]] = defaultdict(list)
    # PDFs históricos são apenas referência visual. Nunca criam item pendente,
    # exceção ou confirmação e não participam da decisão automática.
    unlinked_evidence = []
    for payload in raw_evidence:
        entry_id = payload.get("purchase_entry_id")
        try:
            entry_id = int(entry_id) if entry_id is not None else None
        except (TypeError, ValueError):
            entry_id = None
        movement_id = str(payload.get("id") or payload.get("mdcmp_id") or "")
        entry_id = entry_id or movement_to_entry.get(movement_id)
        if entry_id is None and payload.get("document"):
            candidates = {
                document.erp_entry_id
                for documents in docs_by_entry.values()
                for document in documents
                if document.document_id == str(payload.get("document"))
            }
            if len(candidates) == 1:
                entry_id = next(iter(candidates))
        if entry_id is None:
            unlinked_evidence.append(payload)
        else:
            evidence_by_entry[entry_id].append(payload)

    for purchase in purchases:
        movements = movements_by_entry.get(purchase.erp_entry_id, [])
        payments = [
            item for item in movements
            if item.movement_type == "B" and item.reversal_sequence == 0
        ]
        discounts = [
            item for item in movements
            if item.movement_type == "D" and item.reversal_sequence == 0
        ]
        raw_discount = money(sum((abs(_decimal(item.amount)) for item in discounts), Decimal("0")))
        documents = docs_by_entry.get(purchase.erp_entry_id, [])
        lost_due_to_late_payment = invoice_discount_forfeited_by_late_payment(rule, documents)
        expected = (
            Decimal("0")
            if lost_due_to_late_payment
            else money(_eligible_liters(purchase, rule) * _decimal(rule.rate_per_liter))
        )
        # A zero expected value (including a discount forfeited by late
        # payment) is retained in the ERP/audit data but is not a conciliable
        # financial item. Showing it as "a tratar" creates a false alert.
        if expected <= 0:
            continue
        paid_document_count = sum(
            bool(item.payment_date) or _decimal(item.balance) == 0
            for item in documents
        )
        payment_51_exact_count = sum(
            _payment_corroboration_is_exact(db, item) for item in payments
        )
        payment_chain_exact = bool(payments) and payment_51_exact_count == len(payments)
        exact_chain = len(documents) == 1 and raw_discount == expected and bool(discounts) and all(
            _has_exact_payment(db, movements, item) and _corroboration_is_exact(db, item)
            for item in discounts
        )
        if lost_due_to_late_payment:
            decision_code = "late_payment_forfeits_discount"
        elif exact_chain:
            decision_code = "exact_contract_discount"
        elif len(documents) == 0:
            decision_code = "missing_title"
        elif len(documents) != 1:
            decision_code = "ambiguous_title"
        elif not payments and paid_document_count == 0:
            decision_code = "awaiting_payment"
        elif not payment_chain_exact:
            decision_code = "incomplete_payment_chain"
        elif not discounts:
            decision_code = "paid_without_discount"
        elif raw_discount != expected:
            decision_code = (
                "unclassified_excess_discount"
                if raw_discount > expected
                else "contract_discount_shortfall"
            )
        else:
            decision_code = "incomplete_discount_chain"
        item_evidence = evidence_by_entry.get(purchase.erp_entry_id, []) + boletos_by_entry.get(
            purchase.erp_entry_id, []
        )
        boleto_evidence = [item for item in item_evidence if item.get("source") == "BOLETO_PDF"]
        unclassified = [item for item in item_evidence if item.get("source") == "unclassified_credit"]
        portal_postpaid_exact = any(
            item.get("source") == "IPIRANGA_PORTAL"
            and item.get("portal_match_status") in {
                "portal_exact", "portal_group_exact", "portal_issued_awaiting_payment"
            }
            and _decimal(item.get("allocated")) == expected
            for item in item_evidence
        )
        portal_credit_issued = any(
            item.get("source") == "IPIRANGA_PORTAL"
            and item.get("credit_issued") is True
            and item.get("portal_settlement_confirmed") is False
            and _decimal(item.get("allocated")) == expected
            for item in item_evidence
        )
        if portal_postpaid_exact:
            decision_code = "portal_contract_credit"
        elif portal_credit_issued and decision_code == "awaiting_payment":
            decision_code = "portal_credit_issued_awaiting_payment"
        payloads.append(
            {
                "source_key": f"purchase:{purchase.erp_entry_id}",
                "item_type": "invoice",
                "source_date": purchase.purchase_date,
                "source_document": purchase.invoice_number or str(purchase.erp_entry_id),
                "description": (
                    f"NF {purchase.invoice_number or purchase.erp_entry_id} - beneficio perdido por pagamento apos o vencimento"
                    if lost_due_to_late_payment
                    else f"NF {purchase.invoice_number or purchase.erp_entry_id} - {purchase.supplier_name or rule.company_code}"
                ),
                "expected": expected,
                "evidence": item_evidence,
                "details": {
                    "erp_entry_id": purchase.erp_entry_id,
                    "invoice_number": purchase.invoice_number,
                    "access_key": purchase.access_key,
                    "eligible_liters": float(_eligible_liters(purchase, rule)),
                    "document_count": len(documents),
                    "paid_document_count": paid_document_count,
                    "payment_count": len(payments),
                    "payment_51_exact_count": payment_51_exact_count,
                    "payment_chain_exact": payment_chain_exact,
                    "discount_count": len(discounts),
                    "raw_discount_value": float(raw_discount),
                    "discount_matches_expected": raw_discount == expected,
                    "exact_native_chain": exact_chain,
                    "lost_due_to_late_payment": lost_due_to_late_payment,
                    "late_payment_days": (
                        (documents[0].payment_date - documents[0].due_date).days
                        if lost_due_to_late_payment
                        else 0
                    ),
                    "portal_postpaid_exact": portal_postpaid_exact,
                    "portal_credit_issued": portal_credit_issued,
                    "decision_code": decision_code,
                    "title_due_date": min(
                        (item.due_date for item in documents if item.due_date),
                        default=None,
                    ),
                    "boleto_count": len(boleto_evidence),
                    "exact_boleto_count": sum(item.get("match_status") == "exact" for item in boleto_evidence),
                    "boleto_match_statuses": [item.get("match_status") for item in boleto_evidence],
                    "unclassified_discount_value": float(
                        money(sum((_decimal(item.get("value")) for item in unclassified), Decimal("0")))
                    ),
                },
                "automatic_kind": exact_chain,
            }
        )
    if unlinked_evidence and any(
        _evidence_amount(item)[2] and _evidence_amount(item)[1] > 0
        for item in unlinked_evidence
    ):
        payloads.append(
            {
                "source_key": "unlinked:evidence",
                "item_type": "unlinked_evidence",
                "source_date": row.reference_month,
                "source_document": None,
                "description": "Evidências sem vínculo único com uma nota",
                "expected": Decimal("0"),
                "evidence": unlinked_evidence,
                "details": {"unlinked": True},
                "automatic_kind": False,
            }
        )
    return payloads


def _automatic_policy(rule: BonusRule, item_payload: dict, observed: Decimal) -> tuple[bool, str]:
    expected = money(item_payload["expected"])
    exact = abs(expected - observed) <= CENT_TOLERANCE
    counted = [payload for payload in item_payload["evidence"] if _evidence_amount(payload)[2]]
    source_values_are_exact = bool(counted) and all(
        _evidence_amount(payload)[0] == _evidence_amount(payload)[1]
        and _evidence_amount(payload)[1] > 0
        for payload in counted
    )
    if expected <= 0:
        return False, "O valor identificado não fecha exatamente o valor esperado deste item."
    if not exact:
        return False, "O valor identificado não fecha exatamente o valor esperado deste item."
    if not counted:
        if (
            rule.kind == "invoice_discount"
            and item_payload.get("details", {}).get("portal_credit_issued")
        ):
            return (
                False,
                "Crédito contratual já emitido no portal e ligado à NF por valor exato; "
                "aguarda somente a baixa financeira do título.",
            )
        return False, "Não há valor financeiro vinculado para sustentar a conciliação."
    if rule.kind == "invoice_discount":
        portal_exact = bool(item_payload.get("details", {}).get("portal_postpaid_exact"))
        portal_statuses = {"portal_exact", "portal_group_exact", "portal_issued_awaiting_payment"}
        grouped_portal_exact = bool(counted) and all(
            payload.get("source") == "IPIRANGA_PORTAL"
            and (
                payload.get("portal_match_status") == "portal_group_exact"
                or payload.get("portal_product_anchor_exact") is True
            )
            and payload.get("portal_group_allocation_exact") is True
            for payload in counted
        )
        if (
            portal_exact
            and all(payload.get("source") == "IPIRANGA_PORTAL" for payload in counted)
            and all(payload.get("portal_match_status") in portal_statuses for payload in counted)
            and (source_values_are_exact or grouped_portal_exact)
        ):
            return (
                True,
                (
                    "Valor exato confirmado: bloco de Notas Proprias do portal Texaco fecha a formula contratual "
                    "da NF, com alocacao exata e sem reutilizacao de credito."
                    if grouped_portal_exact
                    else "Valor exato confirmado: Nota Propria do portal Texaco foi vinculada unicamente a NF e fecha a formula contratual."
                ),
            )
        if any(payload.get("source") == "unclassified_credit" for payload in item_payload["evidence"]):
            return False, "Existe desconto de origem não classificada no título; o valor não fecha a regra contratual desta nota e não foi apropriado."
        if not counted or any(payload.get("source") != "MDCMP" for payload in counted):
            return True, "Conciliação concluída: as evidências vinculadas fecham integralmente o desconto esperado da nota."
        if not source_values_are_exact:
            return True, "Conciliação concluída: as alocações vinculadas fecham integralmente o desconto esperado da nota."
        return True, "Valor exato confirmado: desconto MDCMP vinculado à nota fecha a bonificação esperada."
    if rule.kind == "bank_deposit":
        allowed = {"MExtratoBancoLanc", "accounting_settlement", "RAIZEN_PAYMENT_RECEIPT"}
        if source_values_are_exact and all(payload.get("source") in allowed for payload in counted):
            return True, "Valor exato confirmado: comprovante de depósito vinculado fecha a bonificação esperada."
        return True, "Conciliação concluída: os valores identificados e as evidências vinculadas fecham o depósito esperado."
    if rule.kind == "milestone_bonus":
        allowed = {"MExtratoBancoLanc", "accounting_receipt"}
        if source_values_are_exact and all(payload.get("source") in allowed for payload in counted):
            return (
                True,
                "Valor exato confirmado: recebimento BR foi alocado cronologicamente ao marco do guarda-chuva, "
                "com documento, data e histórico preservados.",
            )
        return True, "Conciliação concluída: os valores identificados e as evidências vinculadas fecham o marco esperado."
    if rule.kind == "distributor_credit":
        # Na 003 o extrato Ipiranga é a única fonte que comprova a concessão.
        # Um abatimento MDCMP registra a utilização do saldo, não a emissão da
        # bonificação, e por isso não fecha a competência dessa unidade.
        allowed = (
            {"IPIRANGA_PORTAL"}
            if rule.unit_code == "003" and rule.company_code == "IPIRANGA"
            else {"MDCMP", "IPIRANGA_PORTAL"}
        )
        # A bonificação em crédito pode ser usada de forma fracionada em mais
        # de uma nota. Quando somente fontes contratuais vinculadas somam a
        # competência, a utilização é a prova operacional aprovada. A
        # capacidade de cada fonte continua protegida por
        # _enforce_evidence_capacity.
        if all(payload.get("source") in allowed for payload in counted):
            if all(payload.get("source") == "IPIRANGA_PORTAL" for payload in counted):
                return (
                    True,
                    "Crédito postecipado no portal Ipiranga: os valores do extrato somam exatamente a bonificação esperada da competência.",
                )
            return (
                True,
                "Crédito utilizado por valor exato: os valores vinculados a títulos/notas somam a bonificação esperada da competência.",
            )
        if rule.unit_code == "003" and rule.company_code == "IPIRANGA":
            return False, "A unidade 003 exige extrato Ipiranga como prova do crédito postecipado."
        return True, "Conciliação concluída: os créditos utilizados vinculados fecham integralmente o valor esperado."
    if rule.kind == "s10_excess_credit":
        if source_values_are_exact and all(payload.get("source") == "MDCMP" for payload in counted):
            return True, "Valor exato confirmado: crédito residual após o desconto-base fecha o adicional S10 esperado."
        return False, "O crédito residual não possui valor exato e exclusivo para o adicional S10."
    return True, "Conciliação concluída: os valores identificados e as evidências vinculadas fecham integralmente o valor esperado."


def _item_status(
    rule: BonusRule,
    payload: dict,
    expected: Decimal,
    observed: Decimal,
    due: date,
    today: date,
    automatic: bool,
) -> str:
    if expected <= 0 and observed <= 0:
        return "not_applicable"
    if automatic:
        return "auto_confirmed"
    if rule.kind == "invoice_discount" and payload.get("item_type") == "invoice":
        details = payload.get("details") or {}
        document_count = int(details.get("document_count") or 0)
        paid = bool(details.get("paid_document_count") or details.get("payment_count"))
        raw_discount = money(_decimal(details.get("raw_discount_value")))
        if document_count != 1:
            return "data_gap"
        if not paid:
            if details.get("portal_credit_issued"):
                return "pending"
            title_due = _date(details.get("title_due_date")) or due
            return "overdue" if title_due and title_due < today else "pending"
        if int(details.get("discount_count") or 0) == 0 or raw_discount != expected:
            return "divergent"
        if not details.get("exact_native_chain"):
            return "data_gap"
        return "data_gap"
    if abs(expected - observed) <= CENT_TOLERANCE and expected > 0:
        return "review_required"
    status = reconciliation_status(expected, observed, due, today, tolerance=CENT_TOLERANCE)
    return "review_required" if status == "confirmed" else status


def _upsert_item(db: Session, row: Reconciliation, rule: BonusRule, payload: dict, today: date) -> ReconciliationItem:
    evidence_rows = []
    observed = Decimal("0")
    for evidence_payload in payload["evidence"]:
        evidence = _upsert_evidence(db, row, evidence_payload)
        _, allocated, counted = _evidence_amount(evidence_payload)
        evidence_rows.append((evidence, evidence_payload, allocated, counted))
        if counted:
            observed += allocated
    expected = money(payload["expected"])
    observed = money(observed)
    automatic, policy_reason = _automatic_policy(rule, payload, observed)
    automatic_status = _item_status(
        rule,
        payload,
        expected,
        observed,
        row.due_date,
        today,
        automatic,
    )
    fingerprint_payload = {
        "expected": str(expected),
        "observed": str(observed),
        "evidence": sorted((item[0].source_type, item[0].source_key, str(item[2])) for item in evidence_rows),
        "details": payload["details"],
        "algorithm": ALGORITHM_VERSION,
    }
    fingerprint = _fingerprint(fingerprint_payload)
    item = db.scalar(
        select(ReconciliationItem).where(
            ReconciliationItem.reconciliation_id == row.id,
            ReconciliationItem.source_key == payload["source_key"],
        )
    )
    legacy_manual = row.confirmed_at is not None and item is None
    if not item:
        item = ReconciliationItem(
            reconciliation_id=row.id,
            source_key=payload["source_key"],
            item_type=payload["item_type"],
            description=payload["description"],
            status="pending",
            fingerprint=fingerprint,
        )
        db.add(item)
    changed = bool(item.fingerprint and item.fingerprint != fingerprint)
    # This status records an audited scope decision, not a conclusion inferred
    # from the current payment chain. A subsequent ERP refresh may enrich that
    # chain and change its fingerprint, but it must not silently reactivate a
    # document that management removed from the contractual scope.
    contractual_exclusion = item.review_status == CONTRACTUAL_EXCLUSION_REVIEW_STATUS
    if changed and not contractual_exclusion:
        item.review_status = "stale"
        item.reviewed_by = None
        item.reviewed_at = None
        item.review_notes = "Revisão invalidada: valores ou evidências de origem foram alterados."
    item.item_type = payload["item_type"]
    item.source_date = payload["source_date"]
    item.source_document = payload["source_document"]
    item.description = payload["description"]
    item.expected_value = expected
    item.observed_value = observed
    item.difference_value = money(expected - observed)
    item.automatic_eligible = automatic
    item.automatic_confirmed_at = datetime.now(timezone.utc) if automatic else None
    item.policy_reason = policy_reason
    item.details_json = _json(payload["details"])
    item.fingerprint = fingerprint
    if legacy_manual:
        item.review_status = "accepted"
        item.reviewed_by = row.confirmed_by
        item.reviewed_at = row.confirmed_at
        item.review_notes = "Revisão mensal anterior preservada durante a migração para itens granulares."
    if item.review_status == CONTRACTUAL_EXCLUSION_REVIEW_STATUS:
        item.status = "not_applicable"
        item.automatic_eligible = False
        item.automatic_confirmed_at = None
        item.policy_reason = (
            "Documento excluido da conciliacao contratual por decisao auditada; "
            "a compra e a cadeia financeira permanecem preservadas."
        )
    elif item.review_status == "accepted" and not changed:
        item.status = "manual_confirmed"
    elif item.review_status == "rejected" and not changed:
        item.status = "divergent"
    else:
        item.status = automatic_status
        if item.review_status not in {"accepted", "rejected", "needs_information"}:
            item.review_status = "pending"
    item.confidence = (
        "direct"
        if automatic or item.status == "divergent"
        else "probable"
        if observed > 0
        else "none"
    )
    db.flush()

    seen_evidence = set()
    for evidence, evidence_payload, allocated, counted in evidence_rows:
        allocation = db.scalar(
            select(ReconciliationAllocation).where(
                ReconciliationAllocation.item_id == item.id,
                ReconciliationAllocation.evidence_id == evidence.id,
            )
        )
        if not allocation:
            allocation = ReconciliationAllocation(item_id=item.id, evidence_id=evidence.id)
            db.add(allocation)
        old_value = _decimal(allocation.allocated_value)
        if old_value != allocated and allocation.match_status in {"accepted", "rejected"}:
            allocation.match_status = "proposed"
            allocation.reviewed_by = None
            allocation.reviewed_at = None
        allocation.allocated_value = allocated
        allocation.confidence = "direct" if automatic and counted else "probable" if counted else "informative"
        allocation.match_status = "automatic" if automatic and counted else allocation.match_status or "proposed"
        if not automatic and allocation.match_status == "automatic":
            allocation.match_status = "proposed"
        allocation.match_basis = (
            evidence_payload.get("match_basis")
            or evidence_payload.get("allocation_reason")
            or evidence_payload.get("reason")
            or policy_reason
        )
        allocation.algorithm_version = ALGORITHM_VERSION
        seen_evidence.add(evidence.id)
    stale_allocations = db.scalars(
        select(ReconciliationAllocation).where(
            ReconciliationAllocation.item_id == item.id,
            ~ReconciliationAllocation.evidence_id.in_(seen_evidence) if seen_evidence else True,
        )
    ).all()
    for allocation in stale_allocations:
        db.delete(allocation)
    db.flush()
    return item


def _exception_spec(item: ReconciliationItem, rule: BonusRule, today: date) -> list[dict]:
    if item.review_status == CONTRACTUAL_EXCLUSION_REVIEW_STATUS:
        return []
    details = json.loads(item.details_json or "{}")
    if details.get("lost_due_to_late_payment"):
        return []
    expected = money(item.expected_value)
    observed = money(item.observed_value)
    difference = money(item.difference_value)
    specs = []
    if item.item_type == "invoice" and not details.get("portal_postpaid_exact"):
        if _decimal(details.get("unclassified_discount_value")) > 0:
            specs.append((
                "unclassified_invoice_discount",
                "critical" if details.get("paid_document_count", 0) else "high",
                "Desconto do título não fecha a regra",
                "O MDCMP possui desconto, mas seu total por nota difere dos litros elegíveis multiplicados por R$ 0,04.",
            ))
    if item.item_type == "invoice" and details.get("document_count", 0) == 0:
        specs.append(("missing_title", "high", "Nota sem título/boleto", "Não existe MDCDP ligado à entrada ERP por Cd_Entrada."))
    elif item.item_type == "invoice" and details.get("document_count", 0) != 1:
        specs.append((
            "ambiguous_title",
            "high",
            "Nota com vínculo de título não único",
            "A entrada ERP aponta para mais de um título; o sistema não assume qual baixa comprova o desconto.",
        ))
    elif (
        item.item_type == "invoice"
        and details.get("paid_document_count", 0) == 0
        and not item.automatic_eligible
        and not details.get("portal_credit_issued")
    ):
        specs.append(("unpaid_title", "medium", "Título ainda sem baixa", "A nota possui título, mas não há baixa comprovada no ERP."))
    if (
        item.item_type == "invoice"
        and details.get("paid_document_count", 0) > 0
        and details.get("discount_count", 0) == 0
        and not details.get("portal_postpaid_exact")
    ):
        specs.append(("paid_without_discount", "critical", "Título pago sem desconto esperado", "A nota foi paga, mas nenhum desconto postecipado elegível foi identificado."))
    raw_discount = money(_decimal(details.get("raw_discount_value")))
    if (
        item.item_type == "invoice"
        and raw_discount == expected
        and raw_discount > 0
        and not details.get("exact_native_chain")
        and not item.automatic_eligible
    ):
        specs.append(("incomplete_document_chain", "high", "Cadeia documental incompleta", "O valor fecha, mas faltam elos técnicos complementares para auditoria."))
    if item.item_type == "unlinked_evidence":
        specs.append(("unlinked_evidence", "high", "Evidência sem nota única", "O documento da evidência aponta para nenhuma ou mais de uma entrada ERP."))
    if rule.kind == "milestone_bonus" and expected > 0 and not item.automatic_eligible:
        specs.append(("umbrella_allocation_review", "medium", "Alocação do guarda-chuva exige revisão", "O recebimento é comprovável no grupo 002/006, mas a unidade individual é uma alocação interna."))
    if (
        rule.kind in {"distributor_credit", "s10_excess_credit"}
        and observed >= expected
        and expected > 0
        and not item.automatic_eligible
    ):
        specs.append(("origin_not_proven", "medium", "Origem do crédito não comprovada", item.policy_reason or "A utilização não comprova a geração do crédito."))
    specific_financial_exception = any(
        kind in {"missing_title", "ambiguous_title", "unpaid_title", "paid_without_discount", "unclassified_invoice_discount", "unlinked_evidence"}
        for kind, _, _, _ in specs
    )
    if expected > observed + CENT_TOLERANCE and item.status == "overdue" and not specific_financial_exception:
        specs.append(("overdue_difference", "critical", "Bonificação vencida sem comprovação integral", "O prazo terminou e permanece valor em aberto."))
    if item.status == "review_required" and not specs:
        specs.append(("manual_review_required", "medium", "Correspondência exige revisão", item.policy_reason or "A soma fecha, mas a origem não atende à política automática."))
    return [
        {
            "type": kind,
            "severity": severity,
            "title": title,
            "description": description,
            "expected": expected,
            "observed": observed,
            "difference": difference,
        }
        for kind, severity, title, description in specs
    ]


def _sync_exceptions(db: Session, row: Reconciliation, rule: BonusRule, items: list[ReconciliationItem], today: date) -> None:
    active_keys = set()
    for item in items:
        for spec in _exception_spec(item, rule, today):
            source_key = f"{item.source_key}:{spec['type']}"[:220]
            active_keys.add(source_key)
            exception = db.scalar(
                select(ReconciliationException).where(
                    ReconciliationException.reconciliation_id == row.id,
                    ReconciliationException.source_key == source_key,
                )
            )
            if not exception:
                exception = ReconciliationException(
                    reconciliation_id=row.id,
                    item_id=item.id,
                    source_key=source_key,
                    exception_type=spec["type"],
                    severity=spec["severity"],
                    status="open",
                    title=spec["title"],
                    description=spec["description"],
                )
                db.add(exception)
            elif exception.status == "auto_resolved":
                exception.status = "open"
                exception.resolution_code = None
                exception.resolution_notes = None
                exception.resolved_at = None
                exception.resolved_by = None
            exception.item_id = item.id
            exception.severity = spec["severity"]
            exception.title = spec["title"]
            exception.description = spec["description"]
            exception.expected_value = spec["expected"]
            exception.observed_value = spec["observed"]
            exception.difference_value = spec["difference"]
    existing = db.scalars(
        select(ReconciliationException).where(ReconciliationException.reconciliation_id == row.id)
    ).all()
    for exception in existing:
        if exception.source_key not in active_keys and exception.status in {"open", "in_review"}:
            exception.status = "auto_resolved"
            exception.resolution_code = "source_changed"
            exception.resolution_notes = "Resolvida automaticamente porque a condição deixou de existir após nova sincronização."
            exception.resolved_at = datetime.now(timezone.utc)


def recompute_reconciliation_confirmation(db: Session, row: Reconciliation, today: date | None = None) -> None:
    today = today or date.today()
    items = db.scalars(
        select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id)
    ).all()
    if not items:
        # The workspace intentionally emits no item for a zero expected / zero
        # identified month.  Persist the same semantic status on its aggregate
        # row so an old pending state cannot leak back into a list endpoint.
        if (
            _decimal(row.expected_value) <= 0
            and money(_decimal(row.observed_value) + _decimal(row.manual_adjustment)) <= 0
        ):
            row.status = "not_applicable"
            row.confirmation_mode = "none"
            row.auto_confirmed_at = None
            row.confirmed_at = None
            row.confirmed_by = None
            row.algorithm_version = ALGORITHM_VERSION
        return
    excluded = [
        item for item in items
        if item.review_status == CONTRACTUAL_EXCLUSION_REVIEW_STATUS
    ]
    meaningful = [
        item for item in items
        if _decimal(item.expected_value) > 0
        and item.review_status != CONTRACTUAL_EXCLUSION_REVIEW_STATUS
    ]
    all_auto = bool(meaningful) and all(item.status == "auto_confirmed" for item in meaningful)
    all_reviewed = bool(meaningful) and all(item.status in {"auto_confirmed", "manual_confirmed"} for item in meaningful)
    has_divergence = any(item.status == "divergent" for item in meaningful)
    has_data_gap = any(item.status == "data_gap" for item in meaningful)
    has_overdue = any(item.status == "overdue" for item in meaningful)
    has_pending = any(item.status == "pending" for item in meaningful)
    exact_total = abs(_decimal(row.difference_value)) <= CENT_TOLERANCE
    if not meaningful and (excluded or _decimal(row.expected_value) <= 0):
        row.status = "not_applicable"
        row.confirmation_mode = "none"
        row.auto_confirmed_at = None
        row.confirmed_at = None
        row.confirmed_by = None
    elif all_auto and exact_total:
        row.status = "confirmed"
        row.confirmation_mode = "automatic"
        row.auto_confirmed_at = row.auto_confirmed_at or datetime.now(timezone.utc)
        row.confirmed_at = None
        row.confirmed_by = None
    elif all_reviewed and exact_total:
        row.status = "confirmed"
        row.confirmation_mode = "manual"
        row.auto_confirmed_at = None
        row.confirmed_at = row.confirmed_at or datetime.now(timezone.utc)
    elif exact_total and _decimal(row.manual_adjustment) != 0:
        # A manual adjustment is an explicit, audited managerial decision.
        # When it closes the aggregate balance, leaving its row as overdue
        # contradicts both the displayed values and the decision log.
        row.status = "confirmed"
        row.confirmation_mode = "manual"
        row.auto_confirmed_at = None
        row.confirmed_at = row.confirmed_at or datetime.now(timezone.utc)
    elif has_divergence:
        row.status = "divergent"
        row.confirmation_mode = "none"
        row.auto_confirmed_at = None
        row.confirmed_at = None
        row.confirmed_by = None
    elif has_overdue:
        row.status = "overdue"
        row.confirmation_mode = "none"
        row.auto_confirmed_at = None
        row.confirmed_at = None
        row.confirmed_by = None
    elif has_data_gap:
        row.status = "partial"
        row.confirmation_mode = "none"
        row.auto_confirmed_at = None
        row.confirmed_at = None
        row.confirmed_by = None
    elif has_pending:
        row.status = "pending"
        row.confirmation_mode = "none"
        row.auto_confirmed_at = None
        row.confirmed_at = None
        row.confirmed_by = None
    else:
        row.confirmation_mode = "none"
        row.auto_confirmed_at = None
        if row.confirmed_at and not all_reviewed:
            row.confirmed_at = None
            row.confirmed_by = None
        status = reconciliation_status(
            money(row.expected_value),
            money(_decimal(row.observed_value) + _decimal(row.manual_adjustment)),
            row.due_date,
            today,
            tolerance=CENT_TOLERANCE,
        )
        row.status = "partial" if status == "confirmed" else status
    row.algorithm_version = ALGORITHM_VERSION


def _enforce_evidence_capacity(db: Session, today: date) -> None:
    evidence_rows = db.scalars(select(ReconciliationEvidence).where(ReconciliationEvidence.counted.is_(True))).all()
    for evidence in evidence_rows:
        allocations = db.scalars(
            select(ReconciliationAllocation).where(
                ReconciliationAllocation.evidence_id == evidence.id,
                ReconciliationAllocation.allocated_value > 0,
            )
        ).all()
        total = sum((_decimal(item.allocated_value) for item in allocations), Decimal("0"))
        if total <= _decimal(evidence.value) + CENT_TOLERANCE:
            continue
        for allocation in allocations:
            item = db.get(ReconciliationItem, allocation.item_id)
            if not item:
                continue
            item.automatic_eligible = False
            item.automatic_confirmed_at = None
            if item.status == "auto_confirmed":
                item.status = "review_required"
            item.policy_reason = "A mesma evidência foi apropriada acima do seu valor de origem."
            row = db.get(Reconciliation, item.reconciliation_id)
            source_key = f"{item.source_key}:evidence_overallocated"[:220]
            exception = db.scalar(
                select(ReconciliationException).where(
                    ReconciliationException.reconciliation_id == row.id,
                    ReconciliationException.source_key == source_key,
                )
            )
            if not exception:
                db.add(ReconciliationException(
                    reconciliation_id=row.id,
                    item_id=item.id,
                    source_key=source_key,
                    exception_type="evidence_overallocated",
                    severity="critical",
                    status="open",
                    title="Evidência reutilizada acima do valor disponível",
                    description=f"Alocações somam {money(total)} para uma evidência de {money(evidence.value)}.",
                    expected_value=item.expected_value,
                    observed_value=item.observed_value,
                    difference_value=item.difference_value,
                ))


def rebuild_reconciliation_workspace(db: Session, today: date | None = None) -> int:
    today = today or date.today()
    rows = db.scalars(select(Reconciliation).order_by(Reconciliation.reference_month, Reconciliation.unit_code)).all()
    active_item_ids = set()
    for row in rows:
        if row.status == "superseded":
            continue
        rule = db.get(BonusRule, row.rule_id)
        if not rule:
            continue
        payloads = _base_item_payloads(db, row, rule)
        items = [_upsert_item(db, row, rule, payload, today) for payload in payloads]
        active_item_ids.update(item.id for item in items)
        stale_items = db.scalars(
            select(ReconciliationItem).where(
                ReconciliationItem.reconciliation_id == row.id,
                ~ReconciliationItem.id.in_([item.id for item in items]) if items else True,
            )
        ).all()
        for item in stale_items:
            db.delete(item)
        _sync_exceptions(db, row, rule, items, today)
        recompute_reconciliation_confirmation(db, row, today)
    db.flush()
    _enforce_evidence_capacity(db, today)
    for row in rows:
        if row.status == "superseded":
            continue
        recompute_reconciliation_confirmation(db, row, today)
    return len(active_item_ids)
