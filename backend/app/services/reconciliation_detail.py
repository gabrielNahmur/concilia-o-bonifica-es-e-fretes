from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AccountingEntry,
    BankEntry,
    BonusRule,
    FinancialEntry,
    InvoiceBoletoEvidence,
    ManualAdjustment,
    PayableDocument,
    PayableMovement,
    Purchase,
    Reconciliation,
    ReconciliationAllocation,
    ReconciliationEvidence,
    ReconciliationException,
    ReconciliationInformationRequest,
    ReconciliationItem,
    ReconciliationReview,
    User,
)
from app.services.management_adjustments import full_management_adjustment
from app.services.reconciliation import invoice_discount_forfeited_by_late_payment
from app.services.rules import UmbrellaPurchase, allocate_umbrella, money, month_end, month_start


ZERO = Decimal("0")
RULE_LABELS = {
    "distributor_credit": "Crédito na distribuidora",
    "milestone_bonus": "Marcos quadrimestrais BR",
    "invoice_discount": "Desconto em boleto",
    "s10_excess_credit": "Crédito adicional S10",
    "bank_deposit": "Depósito em conta",
}
ACCOUNT_LABELS = {
    "1170810": "Bonificação Raízen a receber",
    "3111001000037": "(-) Bonificações de distribuidoras",
    "1110202000001": "Banco Bradesco C/C",
}


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _number(value, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _brl(value) -> str:
    formatted = f"{_decimal(value):,.2f}"
    return "R$ " + formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _account_label(code: str | None, name: str | None = None) -> str:
    label = name or ACCOUNT_LABELS.get(str(code or ""))
    if label and code:
        return f"{label} ({code})"
    return label or str(code or "conta contábil")


def _eligible_liters(purchase: Purchase, rule: BonusRule) -> Decimal:
    applies_to = (rule.applies_to or "all_fuel").strip().lower()
    if not applies_to.startswith("fuel_codes:"):
        return _decimal(purchase.total_liters)
    codes = {code.strip() for code in applies_to.split(":", 1)[1].split(",") if code.strip()}
    return sum(
        (_decimal(item.quantity) for item in purchase.items if str(item.item_code).strip() in codes),
        ZERO,
    )


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _legacy_request_notes(value: str | None) -> tuple[str, str]:
    raw = (value or "").strip()
    if raw.startswith("[") and "]" in raw:
        reason, notes = raw[1:].split("]", 1)
        return reason.strip() or "solicitacao_interna", notes.strip() or "Sem justificativa registrada."
    return "solicitacao_interna", raw or "Sem justificativa registrada."


def _formula(rule: BonusRule) -> str:
    if rule.kind == "milestone_bonus":
        return f"Cada {float(rule.milestone_liters or 440000):,.0f} L cumulativos libera R$ {float(rule.milestone_amount or 35000):,.2f}."
    if rule.kind == "s10_excess_credit":
        return (
            f"Máximo de zero ou litros S10 acima de {float(rule.threshold_liters or 0):,.0f} L, "
            f"multiplicado por R$ {float(rule.rate_per_liter):.2f}/L."
        )
    if (rule.applies_to or "").startswith("fuel_codes:"):
        codes = (rule.applies_to or "").split(":", 1)[1]
        return f"Litros dos produtos ERP {codes} multiplicados por R$ {float(rule.rate_per_liter):.6f}/L."
    return f"Litros elegíveis multiplicados por R$ {float(rule.rate_per_liter):.6f}/L."


def _bonus_match_criterion(db: Session, rule: BonusRule, row: Reconciliation) -> dict:
    if full_management_adjustment(db, row):
        return {
            "step": "Ajuste histórico -> Competência",
            "basis": (
                "Competência encerrada por decisão gerencial auditada. O valor não é apresentado "
                "como crédito emitido pela distribuidora nem como lançamento identificado no ERP."
            ),
            "quality": "manual",
        }
    quality = "exact" if row.confidence == "direct" else "probable" if row.confidence == "probable" else "missing"
    if rule.kind == "invoice_discount":
        basis = (
            "Movimento MDCMP tipo D ligado pela chave completa do título e da baixa; o 6204 é apenas corroboração. "
            "Somente o total centavo a centavo da regra é apropriado; diferenças ficam não classificadas. "
            "Vlr_OutAbt é somente informativo."
        )
    elif rule.kind == "distributor_credit":
        basis = (
            "Extrato postecipado do portal ligado por valor exato e janela de um dia ao desconto MDCMP, "
            "título, baixa, nota do fornecedor contratual e MLANF 6204. Ajustes excedentes são alocados "
            "aos ciclos vencidos mais antigos sem reutilizar evidência."
        )
        portal_evidence = [
            item for item in json.loads(row.evidence_json or "[]")
            if item.get("source") == "IPIRANGA_PORTAL"
        ]
        cycle_evidence = [
            item for item in json.loads(row.evidence_json or "[]")
            if item.get("source") == "IPIRANGA_PORTAL_CYCLE"
        ]
        unassigned_portal_evidence = [
            item for item in json.loads(row.evidence_json or "[]")
            if item.get("source") == "unassigned_portal_credit"
        ]
        if cycle_evidence:
            basis = (
                "Crédito postecipado explicitamente listado no extrato Ipiranga e atribuído somente "
                "quando uma sequência cronológica única de NFs fecha o valor a R$ 0,07/L."
            )
        elif portal_evidence:
            basis = (
                "Credito postecipado explicitamente listado no extrato do portal Ipiranga. Quando existir, "
                "a cadeia de mesmo valor/data para MDCMP, titulo, baixa, nota e 6204 e exibida como prova "
                "adicional de utilizacao; sua ausencia nao descaracteriza o credito concedido no portal."
            )
        elif unassigned_portal_evidence:
            basis = (
                "O extrato Ipiranga comprova a emissao de credito, mas nao informa a competencia liquidada. "
                "O credito permanece visivel como saldo nao atribuido e nao fecha automaticamente este mes."
            )
    elif rule.kind == "s10_excess_credit":
        basis = (
            "O ERP calcula o esperado, mas resíduos de desconto não são apropriados automaticamente ao S10, "
            "pois podem ser saldo inicial ou antecipação."
        )
    elif rule.kind == "milestone_bonus":
        basis = (
            "Volume guarda-chuva 002/006 em ordem cronológica, marcos únicos de 440.000 L e evidência bancária/contábil "
            "não reutilizada, pesquisada até o fim do mês seguinte ao quadrimestre."
        )
    else:
        basis = (
            "Depósito pesquisado na janela contratual por Raízen/Shell e valor com tolerância de 2%; "
            "texto e valor coincidentes são diretos, aproximação apenas por valor é provável e exige revisão."
        )
    return {"step": "Bonificação -> Competência", "basis": basis, "quality": quality}


def _is_br_umbrella_rule(rule: BonusRule) -> bool:
    return rule.kind == "milestone_bonus" and rule.unit_code in {"002", "006"}


def _evidence_value(raw: dict) -> Decimal:
    return abs(_decimal(raw.get("allocated") or raw.get("value")))


def _primary_evidence(rule: BonusRule, row: Reconciliation, raw_evidence: list[dict]) -> list[dict]:
    """Return manager-facing proof without leaking unrelated technical links.

    BR allocation calculations belong only to the 002/006 umbrella.  When a
    RaÃ­zen cash-deposit competence has an exact settlement, that settlement is
    the one primary proof; a non-counted accrual remains in the administrator
    technical audit data and cannot be mistaken for a second payment.
    """
    evidence = list(raw_evidence)
    if not _is_br_umbrella_rule(rule):
        evidence = [item for item in evidence if item.get("source") != "calculation"]
    if rule.kind != "bank_deposit":
        return evidence

    expected = money(row.expected_value)
    exact_sources = [
        item
        for item in evidence
        if item.get("source") in {"MExtratoBancoLanc", "accounting_settlement", "RAIZEN_PAYMENT_RECEIPT"}
        and abs(_evidence_value(item) - expected) <= Decimal("0.01")
    ]
    if not exact_sources:
        return evidence
    exact_sources.sort(
        key=lambda item: (
            0 if item.get("source") == "RAIZEN_PAYMENT_RECEIPT" else 1 if item.get("source") == "MExtratoBancoLanc" else 2,
            str(item.get("date") or ""),
            str(item.get("id") or ""),
        )
    )
    return [exact_sources[0]]


def _period(row: Reconciliation, rule: BonusRule, evidence: list[dict]) -> tuple[date, date]:
    calculation = (
        next((item for item in evidence if item.get("source") == "calculation"), None)
        if _is_br_umbrella_rule(rule)
        else None
    )
    if calculation and calculation.get("period_start") and calculation.get("period_end"):
        return date.fromisoformat(calculation["period_start"]), date.fromisoformat(calculation["period_end"])
    start = max(month_start(row.reference_month), rule.effective_from)
    end = min(month_end(row.reference_month), rule.effective_to or month_end(row.reference_month))
    return start, end


def _purchases_for_period(
    db: Session, row: Reconciliation, rule: BonusRule, period_start: date, period_end: date
) -> tuple[list[Purchase], dict[int, Decimal]]:
    if rule.kind == "milestone_bonus" and row.unit_code in {"002", "006"}:
        all_rows = db.scalars(
            select(Purchase)
            .options(selectinload(Purchase.items))
            .where(
                Purchase.unit_code.in_(("002", "006")),
                Purchase.mapped_company_code == "BR",
                Purchase.purchase_date >= rule.effective_from,
                Purchase.purchase_date <= period_end,
            )
            .order_by(Purchase.purchase_date, Purchase.erp_entry_id)
        ).all()
        allocations, _, _ = allocate_umbrella(
            [UmbrellaPurchase(item.erp_entry_id, item.unit_code, item.purchase_date, _decimal(item.total_liters)) for item in all_rows]
        )
        allocated: dict[int, Decimal] = defaultdict(Decimal)
        for allocation in allocations:
            if (
                allocation.contract_unit == row.unit_code
                and period_start <= allocation.purchase_date <= period_end
            ):
                allocated[allocation.purchase_key] += allocation.liters
        selected = [item for item in all_rows if item.erp_entry_id in allocated]
        return selected, allocated

    contractual_date = (
        func.coalesce(Purchase.invoice_issue_date, Purchase.purchase_date)
        if rule.kind == "distributor_credit" and rule.company_code == "IPIRANGA"
        else Purchase.purchase_date
    )
    purchases = db.scalars(
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(
            Purchase.unit_code == row.unit_code,
            Purchase.mapped_company_code == rule.company_code,
            contractual_date >= period_start,
            contractual_date <= period_end,
        )
        .order_by(contractual_date, Purchase.erp_entry_id)
    ).all()
    return purchases, {item.erp_entry_id: _decimal(item.total_liters) for item in purchases}


def _enrich_evidence(
    db: Session,
    evidence: list[dict],
    rule: BonusRule | None = None,
) -> list[dict]:
    result = []
    for raw in evidence:
        source = raw.get("source")
        item = {"source": source, "technical": raw}
        if source in {"IPIRANGA_PORTAL", "IPIRANGA_PORTAL_CYCLE", "IPIRANGA_PORTAL_USAGE"}:
            portal_company = str(raw.get("company_code") or "IPIRANGA").upper()
            portal_name = {"TEXACO": "Texaco", "IPIRANGA": "Ipiranga"}.get(
                portal_company, "da distribuidora"
            )
            item.update(
                {
                    "kind": "portal",
                    "label": (
                        f"Crédito postecipado usado no portal {portal_name}"
                        if source == "IPIRANGA_PORTAL_USAGE"
                        else f"Ciclo único de NFs no portal {portal_name}"
                        if source == "IPIRANGA_PORTAL_CYCLE"
                        else f"Bonificação postecipada no portal {portal_name}"
                    ),
                    "record_id": str(raw.get("id") or ""),
                    "date": raw.get("date"),
                    "document": raw.get("document"),
                    "value": _number(raw.get("allocated") or raw.get("value")),
                    "history": (
                        (
                            f"NF {raw.get('document') or raw.get('invoice_number') or '—'} declarada pelo portal como uso do crédito; "
                            "este vínculo comprova a utilização e não identifica a compra que gerou a bonificação."
                        )
                        if source == "IPIRANGA_PORTAL_USAGE"
                        else (
                            f"Crédito do ciclo em {raw.get('cycle_start')} a {raw.get('cycle_end')}; "
                            f"{raw.get('cycle_purchase_count') or 0} NFs e "
                            f"{raw.get('cycle_liters') or 0:,.3f} L."
                        )
                        if source == "IPIRANGA_PORTAL_CYCLE"
                        else (
                            f"Evento do portal; NF {raw.get('invoice_number') or '—'}; "
                            f"entrada ERP {raw.get('purchase_entry_id') or '—'}"
                        )
                    ),
                    "match_basis": raw.get("match_basis"),
                }
            )
        elif source == "MDCMP":
            movement = db.get(PayableMovement, str(raw.get("id"))) if raw.get("id") else None
            is_distributor_credit = bool(rule and rule.kind == "distributor_credit")
            item.update(
                {
                    "kind": "payable",
                    "label": (
                        "Crédito utilizado na distribuidora"
                        if is_distributor_credit else "Desconto nativo do título"
                    ),
                    "record_id": str(raw.get("id") or ""),
                    "date": _iso(movement.movement_date) if movement else raw.get("date"),
                    "document": movement.document_id if movement else raw.get("document"),
                    "value": _number(raw.get("allocated") or (abs(_decimal(movement.amount)) if movement else raw.get("value"))),
                    "history": (
                        (
                            "Crédito identificado no pagamento do título; "
                            f"MDCMP tipo D; lote {movement.batch_id or '—'}; baixa {movement.payment_sequence}"
                        )
                        if movement and is_distributor_credit
                        else f"MDCMP tipo D; lote {movement.batch_id or '—'}; baixa {movement.payment_sequence}"
                        if movement else raw.get("allocation_reason")
                    ),
                    "match_basis": (
                        "Desconto técnico do título que comprova a utilização do crédito contratual na distribuidora."
                        if is_distributor_credit else
                        "Chave completa do título + mesma baixa; 6204 corroborou o valor."
                        if raw.get("corroborated_by_6204") else
                        "Chave completa do título e da baixa no MDCMP."
                    ),
                }
            )
        elif source == "MLANF":
            entry = None
            if raw.get("id") is not None:
                entry = db.get(FinancialEntry, int(raw["id"]))
            elif raw.get("launch") is not None:
                entry = db.scalar(
                    select(FinancialEntry)
                    .where(FinancialEntry.erp_launch_id == int(raw["launch"]))
                    .order_by(FinancialEntry.erp_sequence)
                )
            item.update(
                {
                    "kind": "financial",
                    "label": "Crédito/desconto financeiro",
                    "record_id": str(entry.id) if entry else str(raw.get("id") or raw.get("launch") or ""),
                    "date": _iso(entry.entry_date) if entry else raw.get("date"),
                    "document": entry.document_id if entry else raw.get("document"),
                    "value": _number(raw.get("allocated") or (abs(_decimal(entry.value)) if entry else raw.get("value"))),
                    "history": entry.history_text if entry else None,
                    "match_basis": "Histórico 6204 e alocação cronológica" if raw.get("allocated") else "Unidade + documento + histórico 6204",
                }
            )
        elif source == "MExtratoBancoLanc":
            entry = db.get(BankEntry, str(raw.get("id"))) if raw.get("id") else None
            item.update(
                {
                    "kind": "bank",
                    "label": "Crédito no extrato bancário",
                    "record_id": str(raw.get("id") or ""),
                    "date": _iso(entry.entry_date) if entry else raw.get("date"),
                    "document": entry.document if entry else None,
                    "value": _number(abs(_decimal(entry.value)) if entry else raw.get("value")),
                    "history": entry.history_text if entry else raw.get("history"),
                    "match_basis": "Data + valor + identificação da companhia no histórico",
                }
            )
        elif source == "RAIZEN_PAYMENT_RECEIPT":
            receipt_total = _decimal(raw.get("receipt_total") or raw.get("source_value") or raw.get("value"))
            allocated = _decimal(raw.get("allocated") or raw.get("value"))
            late = raw.get("after_due_days")
            history = (
                f"{raw.get('payer_name') or 'Raízen S.A.'} (CNPJ {raw.get('payer_cnpj') or 'não informado'}) "
                f"para {raw.get('beneficiary_name') or 'unidade 054'} (CNPJ {raw.get('beneficiary_cnpj') or 'não informado'})."
            )
            if receipt_total != allocated:
                history += f" TED consolidado de {_brl(receipt_total)}; parcela desta competência: {_brl(allocated)}."
            if late is not None and int(late) > 0:
                history += f" Crédito efetuado {late} dia(s) após o vencimento contratual."
            item.update(
                {
                    "kind": "bank",
                    "label": "Comprovante bancário da Raízen",
                    "record_id": str(raw.get("id") or ""),
                    "date": raw.get("date"),
                    "document": raw.get("document"),
                    "value": _number(raw.get("allocated") or raw.get("value")),
                    "history": history,
                    "match_basis": raw.get("match_basis") or "Comprovante bancário externo vinculado por CNPJ, valor e competência.",
                }
            )
        elif source in {"mlanc", "accounting_settlement", "accounting_receipt", "accounting_accrual"}:
            entry = db.get(AccountingEntry, str(raw.get("id"))) if raw.get("id") else None
            labels = {
                "accounting_settlement": "Liquidação contábil identificada",
                "accounting_receipt": "Recebimento contábil da bonificação",
                "accounting_accrual": "Crédito Raízen contabilizado (a receber)",
            }
            if source == "accounting_settlement":
                history = (
                    f"Baixa de {_account_label(raw.get('receivable_account'), raw.get('receivable_account_name'))} "
                    f"contra {_account_label(raw.get('bank_account'), raw.get('bank_account_name'))}"
                )
            elif source == "accounting_accrual":
                history = (
                    f"{_account_label(raw.get('receivable_account'), raw.get('receivable_account_name'))} "
                    f"contra {_account_label(raw.get('counterpart_account'), raw.get('counterpart_account_name'))}"
                )
            else:
                history = entry.history_text if entry else raw.get("history")
            item.update(
                {
                    "kind": "accounting",
                    "label": labels.get(source, "Lançamento contábil complementar"),
                    "record_id": str(raw.get("id") or ""),
                    "date": _iso(entry.entry_date) if entry else raw.get("date"),
                    "document": entry.document_id if entry else None,
                    "value": _number(abs(_decimal(entry.value)) if entry else raw.get("value")),
                    "history": history,
                    "match_basis": raw.get("match_basis") or raw.get("reason") or "Par contábil do mesmo lote e lançamento",
                }
            )
        elif source == "unclassified_credit":
            item.update(
                {
                    "kind": "unclassified",
                    "label": "Crédito de origem não classificada",
                    "record_id": str(raw.get("id") or raw.get("mdcmp_id") or ""),
                    "date": raw.get("date"),
                    "document": raw.get("document"),
                    "value": _number(raw.get("value")),
                    "history": raw.get("reason"),
                    "match_basis": "Exibido para revisão, sem compor o valor confirmado.",
                }
            )
        elif source == "unassigned_portal_credit":
            item.update(
                {
                    "kind": "portal",
                    "label": "Crédito do portal sem competência informada",
                    "record_id": str(raw.get("id") or ""),
                    "date": raw.get("date"),
                    "document": raw.get("document"),
                    "value": _number(raw.get("value")),
                    "history": raw.get("reason"),
                    "match_basis": (
                        "Extrato da Ipiranga preservado para auditoria; não compõe o valor "
                        "identificado sem a competência explícita da distribuidora."
                    ),
                }
            )
        elif source == "BOLETO_PDF":
            boleto = db.get(InvoiceBoletoEvidence, str(raw.get("id"))) if raw.get("id") else None
            item.update(
                {
                    "kind": "boleto",
                    "label": "Comprovante externo auxiliar (não contabilizado)",
                    "record_id": str(raw.get("id") or ""),
                    "date": _iso(boleto.issue_date) if boleto else raw.get("date"),
                    "document": boleto.title_document_id if boleto else raw.get("document"),
                    "value": _number(boleto.discount_value if boleto else raw.get("discount_value")),
                    "history": (
                        f"NF {boleto.invoice_number or '—'}; bruto {_number(boleto.gross_value)}; "
                        f"líquido {_number(boleto.net_value)}"
                        if boleto else None
                    ),
                    "match_basis": boleto.match_basis if boleto else raw.get("match_basis"),
                }
            )
        elif source == "calculation":
            item.update(
                {
                    "kind": "calculation",
                    "label": "Memória de cálculo do marco",
                    "record_id": "calculation",
                    "date": raw.get("period_end"),
                    "value": None,
                    "history": f"{raw.get('new_milestones', 0)} novo(s) marco(s); {raw.get('allocated_liters', 0):,.3f} L acumulados.",
                    "match_basis": "Alocação cronológica do guarda-chuva 002/006",
                }
            )
        elif source == "MDCDP.Vlr_OutAbt":
            item.update(
                {
                    "kind": "payable",
                    "label": "Abatimento do título (informativo)",
                    "record_id": str(raw.get("document") or ""),
                    "date": raw.get("payment_date"),
                    "document": raw.get("document"),
                    "value": _number(raw.get("informative_value")),
                    "history": "Campo Vlr_OutAbt; não usado como confirmação da bonificação.",
                    "match_basis": "Mesmo documento financeiro",
                }
            )
        elif source == "MANAGEMENT_ADJUSTMENT":
            item.update(
                {
                    "kind": "adjustment",
                    "label": "Ajuste gerencial histórico",
                    "record_id": str(raw.get("id") or ""),
                    "date": raw.get("date"),
                    "document": None,
                    "value": _number(raw.get("value")),
                    "history": raw.get("reason"),
                    "match_basis": raw.get("match_basis") or (
                        "Decisão gerencial auditada; não representa crédito da distribuidora ou lançamento do ERP."
                    ),
                }
            )
        else:
            item.update(
                {
                    "kind": "technical",
                    "label": source or "Evidência técnica",
                    "record_id": str(raw.get("id") or ""),
                    "date": raw.get("date"),
                    "document": raw.get("document"),
                    "value": _number(raw.get("value")) if raw.get("value") is not None else None,
                    "history": raw.get("history"),
                    "match_basis": "Registro técnico sincronizado",
                }
            )
        result.append(item)
    return result


def _allocation_evidence_payload(
    evidence: ReconciliationEvidence,
    allocation: ReconciliationAllocation,
    item: ReconciliationItem | None = None,
) -> dict:
    """Return the original proof enriched with its item-specific allocation.

    A monthly reconciliation can aggregate several invoices.  The manager
    drawer, however, opens one invoice at a time, so its displayed proof must
    come from that invoice's allocation rather than from the month's complete
    evidence list.
    """
    raw = json.loads(evidence.identity_json or "{}")
    raw.update(
        {
            "source": evidence.source_type,
            "id": raw.get("id") or evidence.source_key,
            "date": raw.get("date") or _iso(evidence.evidence_date),
            "document": raw.get("document") or evidence.document_id,
            "value": raw.get("value") if raw.get("value") is not None else _number(evidence.value),
            "allocated": _number(allocation.allocated_value),
        }
    )
    if allocation.match_basis and not raw.get("match_basis"):
        raw["match_basis"] = allocation.match_basis
    if evidence.source_type in {"IPIRANGA_PORTAL", "IPIRANGA_PORTAL_CYCLE"} and item:
        # The portal can issue a single consolidated credit for several NFs.
        # The operations drawer is scoped to one NF, therefore it must show
        # only that NF's exact share, never a sibling title or the event total.
        details = json.loads(item.details_json or "{}")
        allocated = _number(allocation.allocated_value) or _number(raw.get("allocated"))
        raw.update(
            {
                "document": None,
                "invoice_number": item.source_document,
                "purchase_entry_id": json.loads(item.details_json or "{}").get("erp_entry_id"),
                "value": allocated,
                "allocated": allocated,
            }
        )
        if evidence.source_type == "IPIRANGA_PORTAL_CYCLE":
            raw["match_basis"] = (
                "Ciclo Ipiranga único: a sequência de NFs em ordem cronológica fecha "
                "o crédito postecipado a R$ 0,07/L, sem reutilização do evento."
            )
        elif details.get("portal_credit_issued"):
            # ``ReconciliationEvidence`` is intentionally shared by all
            # allocations of one consolidated portal event.  Its raw snapshot
            # can therefore describe a paid sibling NF.  The open/settled
            # status displayed here must always come from the current item.
            raw["credit_issued"] = True
            raw["portal_settlement_confirmed"] = False
            raw["match_basis"] = (
                "Extrato do portal Texaco: crédito emitido e vinculado de forma única "
                "a esta NF pelo valor exato, sem reutilização."
            )
    return raw


def build_reconciliation_detail(db: Session, row: Reconciliation, rule: BonusRule) -> dict:
    raw_evidence = json.loads(row.evidence_json or "[]")
    primary_evidence = _primary_evidence(rule, row, raw_evidence)
    period_start, period_end = _period(row, rule, raw_evidence)
    purchases, allocations = _purchases_for_period(db, row, rule, period_start, period_end)
    entry_ids = [item.erp_entry_id for item in purchases]
    boleto_rows = db.scalars(
        select(InvoiceBoletoEvidence)
        .where(InvoiceBoletoEvidence.reconciliation_id == row.id)
        .order_by(InvoiceBoletoEvidence.created_at, InvoiceBoletoEvidence.id)
    ).all()
    boletos_by_entry: dict[int, list[InvoiceBoletoEvidence]] = defaultdict(list)
    for boleto in boleto_rows:
        if boleto.purchase_entry_id is not None:
            boletos_by_entry[boleto.purchase_entry_id].append(boleto)
    documents = db.scalars(
        select(PayableDocument)
        .where(PayableDocument.erp_entry_id.in_(entry_ids) if entry_ids else False)
        .order_by(PayableDocument.erp_entry_id, PayableDocument.due_date, PayableDocument.id)
    ).all()
    docs_by_entry: dict[int, list[PayableDocument]] = defaultdict(list)
    for document in documents:
        docs_by_entry[document.erp_entry_id].append(document)

    document_ids = sorted({item.document_id for item in documents if item.document_id})
    financial_rows = db.scalars(
        select(FinancialEntry)
        .where(
            FinancialEntry.unit_code.in_(sorted({item.unit_code for item in purchases}) or [row.unit_code]),
            FinancialEntry.document_id.in_(document_ids) if document_ids else False,
        )
        .order_by(FinancialEntry.entry_date, FinancialEntry.erp_launch_id, FinancialEntry.erp_sequence)
    ).all()
    finances_by_document: dict[tuple[str, str], list[FinancialEntry]] = defaultdict(list)
    for entry in financial_rows:
        finances_by_document[(entry.unit_code, entry.document_id)].append(entry)

    movement_rows = db.scalars(
        select(PayableMovement)
        .where(
            PayableMovement.unit_code.in_(sorted({item.unit_code for item in purchases}) or [row.unit_code]),
            PayableMovement.document_id.in_(document_ids) if document_ids else False,
        )
        .order_by(
            PayableMovement.movement_date,
            PayableMovement.payment_sequence,
            PayableMovement.movement_type,
        )
    ).all()
    movements_by_title: dict[tuple, list[PayableMovement]] = defaultdict(list)
    for movement in movement_rows:
        movements_by_title[
            (
                movement.unit_code,
                movement.person_id,
                movement.title_type,
                movement.document_id,
                movement.document_sequence,
            )
        ].append(movement)

    chains = []
    linked_purchase_count = 0
    paid_documents = 0
    discount_documents = 0
    launch_usage = Counter()
    for purchase in purchases:
        purchase_boletos = boletos_by_entry.get(purchase.erp_entry_id, [])
        purchase_documents = docs_by_entry.get(purchase.erp_entry_id, [])
        if purchase_documents:
            linked_purchase_count += 1
        document_payload = []
        for document in purchase_documents:
            launches = finances_by_document.get((document.unit_code, document.document_id), [])
            movements = movements_by_title.get(
                (
                    document.unit_code,
                    document.person_id,
                    document.title_type,
                    document.document_id,
                    document.sequence,
                ),
                [],
            )
            for launch in launches:
                launch_usage[launch.id] += 1
            payment_entries = [item for item in launches if item.history_code == 51]
            discount_entries = [item for item in launches if item.history_code == 6204]
            payment_movements = [
                item for item in movements
                if item.movement_type == "B" and item.reversal_sequence == 0
            ]
            discount_movements = [
                item for item in movements
                if item.movement_type == "D" and item.reversal_sequence == 0
            ]
            interest_movements = [
                item for item in movements
                if item.movement_type == "J" and item.reversal_sequence == 0
            ]
            gross_settlement = sum((abs(_decimal(item.amount)) for item in payment_movements), ZERO)
            native_discount = sum((abs(_decimal(item.amount)) for item in discount_movements), ZERO)
            native_interest = sum((abs(_decimal(item.amount)) for item in interest_movements), ZERO)
            is_paid = bool(document.payment_date or payment_movements or payment_entries)
            payment_chain_exact = bool(payment_movements) and all(
                len([
                    launch for launch in launches
                    if launch.erp_launch_id == payment.financial_launch_id
                    and launch.history_code == 51
                    and launch.document_id == payment.document_id
                    and money(abs(_decimal(launch.value))) == money(abs(_decimal(payment.amount)))
                ]) == 1
                for payment in payment_movements
            )
            exact_native_chain = bool(discount_movements) and all(
                sum(
                    payment.payment_sequence == discount.payment_sequence
                    for payment in payment_movements
                ) == 1
                and any(
                    payment.payment_sequence == discount.payment_sequence
                    and len([
                        launch for launch in launches
                        if launch.erp_launch_id == payment.financial_launch_id
                        and launch.history_code == 51
                        and launch.document_id == payment.document_id
                        and money(abs(_decimal(launch.value))) == money(abs(_decimal(payment.amount)))
                    ]) == 1
                    for payment in payment_movements
                )
                and len([
                    launch for launch in launches
                    if launch.erp_launch_id == discount.financial_launch_id
                    and launch.history_code == 6204
                    and launch.document_id == discount.document_id
                    and money(abs(_decimal(launch.value))) == money(abs(_decimal(discount.amount)))
                ]) == 1
                for discount in discount_movements
            )
            if is_paid:
                paid_documents += 1
            if discount_movements:
                discount_documents += 1
            document_payload.append(
                {
                    "id": document.id,
                    "document_id": document.document_id,
                    "sequence": document.sequence,
                    "title_type": document.title_type,
                    "invoice_number": document.invoice_number,
                    "issue_date": _iso(document.issue_date),
                    "due_date": _iso(document.due_date),
                    "payment_date": _iso(document.payment_date),
                    "document_value": _number(document.document_value),
                    "balance": _number(document.balance),
                    "vlr_outabt": _number(document.other_discount),
                    "is_paid": is_paid,
                    "payment_chain_exact": payment_chain_exact,
                    "exact_native_chain": exact_native_chain,
                    "match_basis": "Vínculo exato por MDCDP.Cd_Entrada = MDCHP.Cd_Entrada",
                    "movement_summary": {
                        "gross_settlement": _number(gross_settlement),
                        "discount": _number(native_discount),
                        "interest": _number(native_interest),
                        "net_settlement": _number(gross_settlement - native_discount + native_interest),
                    },
                    "payable_movements": [
                        {
                            "id": item.erp_key,
                            "type": item.movement_type,
                            "label": {
                                "E": "Emissão",
                                "B": "Baixa",
                                "D": "Desconto",
                                "J": "Juros",
                            }.get(item.movement_type, item.movement_type),
                            "date": _iso(item.movement_date),
                            "value": _number(abs(_decimal(item.amount))),
                            "payment_sequence": item.payment_sequence,
                            "financial_launch": item.financial_launch_id,
                            "financial_sequence": item.financial_sequence,
                            "batch": item.batch_id,
                            "payment_method": item.payment_method,
                            "payment_method_label": {
                                "803": "Banrisul GBI",
                                "804": "Banrisul Stilo",
                                "806": "Itaú",
                            }.get(item.payment_method, item.payment_method),
                            "corroborated_by_6204": len([
                                launch for launch in launches
                                if launch.erp_launch_id == item.financial_launch_id
                                and launch.history_code == 6204
                                and launch.document_id == item.document_id
                                and money(abs(_decimal(launch.value))) == money(abs(_decimal(item.amount)))
                            ]) == 1
                            if item.movement_type == "D" else None,
                            "corroborated_by_51": len([
                                launch for launch in launches
                                if launch.erp_launch_id == item.financial_launch_id
                                and launch.history_code == 51
                                and launch.document_id == item.document_id
                                and money(abs(_decimal(launch.value))) == money(abs(_decimal(item.amount)))
                            ]) == 1
                            if item.movement_type == "B" else None,
                            "match_basis": "Mesma chave completa do título e mesma sequência de baixa",
                        }
                        for item in movements
                    ],
                    "financial_entries": [
                        {
                            "id": item.id,
                            "launch_id": item.erp_launch_id,
                            "sequence": item.erp_sequence,
                            "date": _iso(item.entry_date),
                            "history_code": item.history_code,
                            "kind": "discount" if item.history_code == 6204 else "payment" if item.history_code == 51 else "other",
                            "direction": item.direction,
                            "value": _number(abs(_decimal(item.value))),
                            "document_id": item.document_id,
                            "history": item.history_text,
                            "origin": item.origin,
                            "native_movement_matched": (
                                any(
                                    movement.movement_type == ("D" if item.history_code == 6204 else "B")
                                    and movement.reversal_sequence == 0
                                    and movement.financial_launch_id == item.erp_launch_id
                                    and movement.document_id == item.document_id
                                    and money(abs(_decimal(movement.amount))) == money(abs(_decimal(item.value)))
                                    for movement in movements
                                )
                                if item.history_code in {51, 6204} else None
                            ),
                            "match_basis": "Unidade + número do documento financeiro",
                        }
                        for item in launches
                    ],
                }
            )

        expected_bonus = None
        eligible_liters = _eligible_liters(purchase, rule)
        if rule.kind in {"invoice_discount", "distributor_credit", "bank_deposit"}:
            expected_bonus = money(eligible_liters * _decimal(rule.rate_per_liter))
        actual_discount = money(sum(
            (_decimal(item["movement_summary"]["discount"]) for item in document_payload),
            ZERO,
        ))
        if rule.kind == "invoice_discount":
            if not purchase_documents:
                chain_status = "data_gap"
                decision_reason = "Entrada ERP sem título MDCDP vinculado."
            elif len(purchase_documents) != 1:
                chain_status = "data_gap"
                decision_reason = "Mais de um título ligado à mesma entrada; vínculo não único."
            elif invoice_discount_forfeited_by_late_payment(rule, purchase_documents):
                title = purchase_documents[0]
                late_days = (title.payment_date - title.due_date).days
                chain_status = "late_payment"
                decision_reason = (
                    f"Título liquidado {late_days} dia(s) após o vencimento; "
                    "o desconto contratual não é aplicável."
                )
            elif not document_payload[0]["is_paid"]:
                chain_status = "pending_payment"
                decision_reason = "Título ainda sem baixa no ERP."
            elif not document_payload[0]["payment_chain_exact"]:
                chain_status = "data_gap"
                decision_reason = "A baixa existe, mas não possui um lançamento financeiro 51 único do mesmo valor."
            elif actual_discount != expected_bonus:
                chain_status = "discount_divergent"
                decision_reason = (
                    f"Baixa concluída com desconto de R$ {actual_discount:.2f}; "
                    f"a regra exige R$ {expected_bonus:.2f}."
                )
            elif document_payload[0]["exact_native_chain"]:
                chain_status = "discount_exact"
                decision_reason = "Nota, título, baixa/51 e desconto D/6204 fecham exatamente."
            else:
                chain_status = "data_gap"
                decision_reason = "Valor fecha, mas falta um elo técnico único na cadeia interna."
        elif not purchase_documents:
            chain_status = "missing_title"
            decision_reason = "Entrada ERP sem título financeiro vinculado."
        elif any(item["payable_movements"] or item["financial_entries"] for item in document_payload):
            chain_status = "financial_linked"
            decision_reason = "Movimentos financeiros ligados à entrada ERP."
        elif any(item["is_paid"] for item in document_payload):
            chain_status = "paid_without_financial_link"
            decision_reason = "Título pago sem lançamento financeiro ligado."
        else:
            chain_status = "title_linked"
            decision_reason = "Título localizado e ainda sem baixa."
        chains.append(
            {
                "purchase": {
                    "erp_entry_id": purchase.erp_entry_id,
                    "erp_reference_label": "Entrada ERP",
                    "unit_code": purchase.unit_code,
                    "purchase_date": _iso(purchase.purchase_date),
                    "invoice_issue_date": _iso(purchase.invoice_issue_date),
                    "contractual_date": _iso(purchase.invoice_issue_date or purchase.purchase_date),
                    "invoice_number": purchase.invoice_number,
                    "invoice_series": purchase.invoice_series,
                    "access_key": purchase.access_key,
                    "supplier_name": purchase.supplier_name,
                    "supplier_cnpj": purchase.supplier_cnpj,
                    "total_liters": _number(purchase.total_liters, 3),
                    "eligible_bonus_liters": _number(eligible_liters, 3),
                    "eligibility_rule": rule.applies_to,
                    "contract_allocated_liters": _number(allocations.get(purchase.erp_entry_id), 3),
                    "s10_liters": _number(purchase.s10_liters, 3),
                    "gross_value": _number(purchase.gross_value),
                    "net_value": _number(purchase.net_value),
                    "expected_bonus": _number(expected_bonus) if expected_bonus is not None else None,
                    "items": [
                        {
                            "code": item.item_code,
                            "description": item.description,
                            "quantity": _number(item.quantity, 3),
                            "unit_value": _number(item.unit_value, 4),
                            "total_value": _number(item.total_value),
                        }
                        for item in purchase.items
                    ],
                },
                "boletos": [
                    {
                        "id": boleto.id,
                        "filename": boleto.original_filename,
                        "file_url": f"/api/reconciliations/boleto-evidence/{boleto.id}/file",
                        "cedent_name": boleto.cedent_name,
                        "payer_name": boleto.payer_name,
                        "payer_cnpj": boleto.payer_cnpj,
                        "boleto_document_number": boleto.boleto_document_number,
                        "invoice_number": boleto.invoice_number,
                        "title_document_id": boleto.title_document_id,
                        "nosso_numero": boleto.nosso_numero,
                        "issue_date": _iso(boleto.issue_date),
                        "due_date": _iso(boleto.due_date),
                        "gross_value": _number(boleto.gross_value),
                        "discount_value": _number(boleto.discount_value),
                        "net_value": _number(boleto.net_value),
                        "expected_discount_value": (
                            _number(boleto.expected_discount_value)
                            if boleto.expected_discount_value is not None else None
                        ),
                        "match_status": boleto.match_status,
                        "match_basis": boleto.match_basis,
                        "created_at": _iso(boleto.created_at),
                    }
                    for boleto in purchase_boletos
                ],
                "documents": document_payload,
                "status": chain_status,
                "actual_discount": _number(actual_discount),
                "decision_reason": decision_reason,
            }
        )

    ambiguous_launches = [entry_id for entry_id, count in launch_usage.items() if count > 1]
    orphan_6204 = [
        item for item in financial_rows
        if item.history_code == 6204
        and not any(
            movement.movement_type == "D"
            and movement.financial_launch_id == item.erp_launch_id
            and movement.document_id == item.document_id
            and money(abs(_decimal(movement.amount))) == money(abs(_decimal(item.value)))
            for movement in movement_rows
        )
    ]
    from app.services.boleto_evidence import boleto_payload

    evidence = _enrich_evidence(
        db, primary_evidence + [boleto_payload(item) for item in boleto_rows], rule
    )
    workspace_items = db.scalars(
        select(ReconciliationItem)
        .where(ReconciliationItem.reconciliation_id == row.id)
        .order_by(ReconciliationItem.source_date, ReconciliationItem.source_document, ReconciliationItem.id)
    ).all()
    workspace_item_ids = [item.id for item in workspace_items]
    information_request_rows = db.scalars(
        select(ReconciliationInformationRequest)
        .where(ReconciliationInformationRequest.reconciliation_id == row.id)
        .order_by(ReconciliationInformationRequest.requested_at.desc(), ReconciliationInformationRequest.id.desc())
    ).all()
    workspace_allocations = db.scalars(
        select(ReconciliationAllocation).where(
            ReconciliationAllocation.item_id.in_(workspace_item_ids) if workspace_item_ids else False
        )
    ).all()
    workspace_evidence = {
        item.id: item
        for item in db.scalars(
            select(ReconciliationEvidence).where(
                ReconciliationEvidence.id.in_({item.evidence_id for item in workspace_allocations})
                if workspace_allocations else False
            )
        ).all()
    }
    allocations_by_item: dict[str, list[ReconciliationAllocation]] = defaultdict(list)
    for allocation in workspace_allocations:
        allocations_by_item[allocation.item_id].append(allocation)
    workspace_exceptions = db.scalars(
        select(ReconciliationException)
        .where(
            ReconciliationException.reconciliation_id == row.id,
            ~ReconciliationException.exception_type.in_(("missing_boleto_evidence", "boleto_value_mismatch")),
        )
        .order_by(ReconciliationException.status, ReconciliationException.severity.desc(), ReconciliationException.created_at)
    ).all()
    active_workspace_exceptions = [
        item for item in workspace_exceptions if item.status in {"open", "in_review"}
    ]
    adjustments = db.scalars(
        select(ManualAdjustment)
        .where(ManualAdjustment.reconciliation_id == row.id)
        .order_by(ManualAdjustment.created_at)
    ).all()
    reviews = db.scalars(
        select(ReconciliationReview)
        .where(ReconciliationReview.reconciliation_id == row.id)
        .order_by(ReconciliationReview.created_at.desc())
    ).all()
    user_ids = {item.created_by for item in adjustments}
    user_ids.update(item.created_by for item in reviews)
    if row.confirmed_by:
        user_ids.add(row.confirmed_by)
    user_ids.update(item.reviewed_by for item in workspace_items if item.reviewed_by)
    user_ids.update(item.requested_by for item in information_request_rows if item.requested_by)
    user_ids.update(item.responded_by for item in information_request_rows if item.responded_by)
    user_ids.update(item.closed_by for item in information_request_rows if item.closed_by)
    user_ids.update(item.assigned_to for item in workspace_exceptions if item.assigned_to)
    user_ids.update(item.resolved_by for item in workspace_exceptions if item.resolved_by)
    users = {item.id: item for item in db.scalars(select(User).where(User.id.in_(user_ids) if user_ids else False)).all()}
    workspace_items_by_id = {item.id: item for item in workspace_items}
    information_requests = [
        {
            "id": request.id,
            "item_id": request.item_id,
            "document": (workspace_items_by_id.get(request.item_id).source_document if request.item_id in workspace_items_by_id else None) or "Competência",
            "status": request.status,
            "reason_code": request.reason_code,
            "request_notes": request.request_notes,
            "requested_by": users.get(request.requested_by).full_name if request.requested_by in users else request.requested_by or "Administrador",
            "requested_at": _iso(request.requested_at),
            "response": request.response_notes,
            "responded_by": users.get(request.responded_by).full_name if request.responded_by in users else request.responded_by,
            "responded_at": _iso(request.responded_at),
            "closed_by": users.get(request.closed_by).full_name if request.closed_by in users else request.closed_by,
            "closed_at": _iso(request.closed_at),
            "legacy": False,
        }
        for request in information_request_rows
    ]
    active_request_item_ids = {request.item_id for request in information_request_rows if request.status == "open"}
    for item in workspace_items:
        if item.review_status != "needs_information" or item.id in active_request_item_ids:
            continue
        reason_code, request_notes = _legacy_request_notes(item.review_notes)
        information_requests.append(
            {
                "id": f"legacy:{item.id}",
                "item_id": item.id,
                "document": item.source_document or "Competência",
                "status": "open",
                "reason_code": reason_code,
                "request_notes": request_notes,
                "requested_by": users.get(item.reviewed_by).full_name if item.reviewed_by in users else item.reviewed_by or "Administrador",
                "requested_at": _iso(item.reviewed_at),
                "response": None,
                "responded_by": None,
                "responded_at": None,
                "closed_by": None,
                "closed_at": None,
                "legacy": True,
            }
        )

    purchase_count = len(purchases)
    document_count = len(documents)
    purchase_coverage = linked_purchase_count / purchase_count if purchase_count else 0
    payment_coverage = paid_documents / document_count if document_count else 0
    evidence_score = 35 if row.confidence == "direct" else 20 if row.confidence == "probable" else 0
    score = min(100, round((25 if purchase_count else 0) + 25 * purchase_coverage + 15 * payment_coverage + evidence_score))
    alerts = []
    missing = purchase_count - linked_purchase_count
    if missing:
        alerts.append({"severity": "warning", "message": f"{missing} nota(s) sem título financeiro ligado por Cd_Entrada."})
    if ambiguous_launches:
        alerts.append({"severity": "danger", "message": f"{len(ambiguous_launches)} lançamento(s) financeiro(s) aparecem em mais de uma cadeia."})
    if orphan_6204:
        alerts.append({"severity": "warning", "message": f"{len(orphan_6204)} histórico(s) 6204 não possuem desconto MDCMP correspondente e não foram contabilizados."})
    unclassified_count = sum(item.get("source") == "unclassified_credit" for item in raw_evidence)
    if unclassified_count:
        alerts.append({"severity": "warning", "message": f"{unclassified_count} crédito(s) excedente(s) foram exibidos, mas não apropriados por falta de origem comprovada."})
    accrual_count = sum(item.get("source") == "accounting_accrual" for item in raw_evidence)
    if accrual_count and not any(item.get("source") in {"accounting_settlement", "MExtratoBancoLanc", "RAIZEN_PAYMENT_RECEIPT"} for item in raw_evidence):
        alerts.append({"severity": "warning", "message": "Há crédito Raízen contabilizado a receber, mas nenhuma liquidação identificada nesta competência."})
    if row.confidence == "probable":
        alerts.append({"severity": "warning", "message": "Correspondência aproximada por data/valor; exige confirmação administrativa."})
    if abs(_decimal(row.difference_value)) > Decimal("1.00"):
        alerts.append({"severity": "danger", "message": f"Diferença em aberto de R$ {abs(float(row.difference_value)):,.2f}."})
    if not alerts:
        alerts.append({"severity": "success", "message": "Nenhuma inconsistência estrutural encontrada nesta conciliação."})

    return {
        "id": row.id,
        "unit_code": row.unit_code,
        "rule": {
            "id": rule.id,
            "kind": rule.kind,
            "label": RULE_LABELS.get(rule.kind, rule.kind),
            "company_code": rule.company_code,
            "rate_per_liter": _number(rule.rate_per_liter, 6),
            "formula": _formula(rule),
        },
        "reference_month": _iso(row.reference_month),
        "period_start": _iso(period_start),
        "period_end": _iso(period_end),
        "due_date": _iso(row.due_date),
        "expected_value": _number(row.expected_value),
        "observed_value": _number(row.observed_value),
        "manual_adjustment": _number(row.manual_adjustment),
        "difference_value": _number(row.difference_value),
        "status": row.status,
        "confidence": row.confidence,
        "confirmation_mode": row.confirmation_mode,
        "auto_confirmed_at": _iso(row.auto_confirmed_at),
        "algorithm_version": row.algorithm_version,
        "notes": row.notes,
        "score": score,
        "alerts": alerts,
        "match_summary": {
            "purchase_count": purchase_count,
            "purchase_with_document_count": linked_purchase_count,
            "document_count": document_count,
            "paid_document_count": paid_documents,
            "discount_document_count": discount_documents,
            "financial_entry_count": len(financial_rows),
            "payable_movement_count": len(movement_rows),
            "orphan_6204_count": len(orphan_6204),
            "evidence_count": len(evidence),
            "boleto_count": len(boleto_rows),
            "exact_boleto_count": sum(item.match_status == "exact" for item in boleto_rows),
            "purchase_document_coverage": round(purchase_coverage * 100, 1),
            "payment_coverage": round(payment_coverage * 100, 1),
        },
        "criteria": [
            {"step": "Nota -> Entrada ERP", "basis": "A nota é identificada em MDCHP e seus itens em MDCIP pela chave Cd_Entrada.", "quality": "exact"},
            {"step": "Entrada ERP -> Título", "basis": "Vínculo exato MDCDP.Cd_Entrada = MDCHP.Cd_Entrada.", "quality": "exact"},
            {"step": "Título -> Baixa/desconto", "basis": "Chave completa MDCDP/MDCMP: unidade, fornecedor, tipo, documento e parcela; baixa identificada por Sq_Baixa.", "quality": "exact"},
            {"step": "MDCMP -> Financeiro", "basis": "Cd_LancFin, unidade, documento e valor ligam a baixa ao histórico 51 e o desconto ao 6204; divergência ou vínculo não único nunca confirma.", "quality": "exact"},
            _bonus_match_criterion(db, rule, row),
        ],
        "erp_order_note": "O módulo de entrada de combustível não expõe um número de pedido de compra próprio. A referência operacional comprovável é MDCHP.Cd_Entrada, exibida como Entrada ERP.",
        "chains": chains,
        "evidence": evidence,
        "information_requests": information_requests,
        "workspace": {
            "items": [
                {
                    "id": item.id,
                    "item_type": item.item_type,
                    "source_key": item.source_key,
                    "source_date": _iso(item.source_date),
                    "source_document": item.source_document,
                    "description": item.description,
                    "expected_value": _number(item.expected_value),
                    "observed_value": _number(item.observed_value),
                    "difference_value": _number(item.difference_value),
                    "identified_discount_value": (
                        _number(_decimal(json.loads(item.details_json or "{}").get("raw_discount_value")))
                        if item.status == "late_payment"
                        else None
                    ),
                    "status": item.status,
                    "confidence": item.confidence,
                    "automatic_eligible": item.automatic_eligible,
                    "automatic_confirmed_at": _iso(item.automatic_confirmed_at),
                    "review_status": item.review_status,
                    "reviewed_by": users.get(item.reviewed_by).full_name if item.reviewed_by in users else item.reviewed_by,
                    "reviewed_at": _iso(item.reviewed_at),
                    "review_notes": item.review_notes,
                    "policy_reason": item.policy_reason,
                    "details": json.loads(item.details_json or "{}"),
                    "display_evidence": _enrich_evidence(
                        db,
                        [
                            _allocation_evidence_payload(workspace_evidence[allocation.evidence_id], allocation, item)
                            for allocation in allocations_by_item.get(item.id, [])
                            if (
                                allocation.evidence_id in workspace_evidence
                                and abs(_decimal(allocation.allocated_value)) > ZERO
                            )
                        ],
                        rule,
                    ),
                    "allocations": [
                        {
                            "id": allocation.id,
                            "allocated_value": _number(allocation.allocated_value),
                            "match_status": allocation.match_status,
                            "confidence": allocation.confidence,
                            "match_basis": allocation.match_basis,
                            "algorithm_version": allocation.algorithm_version,
                            "evidence": {
                                "id": workspace_evidence[allocation.evidence_id].id,
                                "source_type": workspace_evidence[allocation.evidence_id].source_type,
                                "source_key": workspace_evidence[allocation.evidence_id].source_key,
                                "date": _iso(workspace_evidence[allocation.evidence_id].evidence_date),
                                "document": workspace_evidence[allocation.evidence_id].document_id,
                                "value": _number(workspace_evidence[allocation.evidence_id].value),
                                "counted": workspace_evidence[allocation.evidence_id].counted,
                                "identity": json.loads(workspace_evidence[allocation.evidence_id].identity_json or "{}"),
                            },
                        }
                        for allocation in allocations_by_item.get(item.id, [])
                        if allocation.evidence_id in workspace_evidence
                    ],
                }
                for item in workspace_items
            ],
            "exceptions": [
                {
                    "id": item.id,
                    "item_id": item.item_id,
                    "exception_type": item.exception_type,
                    "severity": item.severity,
                    "status": item.status,
                    "title": item.title,
                    "description": item.description,
                    "expected_value": _number(item.expected_value),
                    "observed_value": _number(item.observed_value),
                    "difference_value": _number(item.difference_value),
                    "assigned_to": users.get(item.assigned_to).full_name if item.assigned_to in users else item.assigned_to,
                    "resolution_code": item.resolution_code,
                    "resolution_notes": item.resolution_notes,
                    "resolved_by": users.get(item.resolved_by).full_name if item.resolved_by in users else item.resolved_by,
                    "resolved_at": _iso(item.resolved_at),
                }
                for item in active_workspace_exceptions
            ],
            "summary": {
                "item_count": len(workspace_items),
                "automatic_confirmed": sum(item.status == "auto_confirmed" for item in workspace_items),
                "manual_confirmed": sum(item.status == "manual_confirmed" for item in workspace_items),
                "review_required": sum(item.status in {"review_required", "partial", "overdue", "divergent", "data_gap"} for item in workspace_items),
                "open_exceptions": len(active_workspace_exceptions),
                "resolved_exceptions": len(workspace_exceptions) - len(active_workspace_exceptions),
            },
        },
        "review": {
            "confirmed_at": _iso(row.confirmed_at),
            "confirmed_by": users.get(row.confirmed_by).full_name if row.confirmed_by in users else None,
            "adjustments": [
                {
                    "id": item.id,
                    "amount": _number(item.amount),
                    "reason": item.reason,
                    "created_at": _iso(item.created_at),
                    "created_by": users.get(item.created_by).full_name if item.created_by in users else item.created_by,
                }
                for item in adjustments
            ],
            "history": [
                {
                    "id": item.id,
                    "action": item.action,
                    "expected_value": _number(item.expected_value),
                    "observed_value": _number(item.observed_value),
                    "difference_value": _number(item.difference_value),
                    "confidence": item.confidence,
                    "notes": item.notes,
                    "created_at": _iso(item.created_at),
                    "created_by": users.get(item.created_by).full_name if item.created_by in users else item.created_by,
                }
                for item in reviews
            ],
        },
        "technical_evidence": raw_evidence,
    }
