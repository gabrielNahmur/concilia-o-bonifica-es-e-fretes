"""Operational monthly routine for contractual bonus evidence.

The ERP remains read-only.  This module only groups the reconciliations that
already exist, receives distributor evidence in the application database and
keeps an auditable allocation for consolidated Raízen receipts.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import select

from app.dependencies import AdminUser, CurrentUser, DbSession
from app.models import BonusRule, ManualAdjustment, PortalBonusEvent, PortalBonusEventSource, PortalStatementImport, Purchase, Reconciliation, ReconciliationAllocation, ReconciliationEvidence, ReconciliationItem, Unit, User
from app.services.audit import audit
from app.services.ipiranga_portal import MAX_PORTAL_BYTES, PortalStatementError, import_ipiranga_statement
from app.services.reconciliation import RAIZEN_RECEIPT_CATEGORY, rebuild_reconciliations
from app.services.rules import money, month_end
from app.services.unit_004_portal_usage import is_unit_004_portal_credit_rule, unit_004_portal_usage_events
from app.services.uploads import UploadValidationError, read_validated_upload


router = APIRouter(prefix="/monthly-routine", tags=["rotina mensal"])

ZERO = Decimal("0")
RAIZEN_UNIT = "054"
RAIZEN_COMPANY = "SHELL"
RAIZEN_PAYER_CNPJ = "033453598000123"
RAIZEN_BENEFICIARY_CNPJ = "12564276000262"
UNIT_004_HISTORICAL_PURCHASE_CUTOFF = date(2026, 5, 26)
UNIT_004_HISTORICAL_CREDIT_CUTOFF = date(2026, 7, 16)
UNIT_004_APPROVED_HISTORICAL_RESIDUAL = Decimal("69.99")

RULE_LABELS = {
    "distributor_credit": "Crédito na distribuidora",
    "milestone_bonus": "Marco BR guarda-chuva",
    "invoice_discount": "Desconto em boleto",
    "s10_excess_credit": "Crédito adicional S10",
    "bank_deposit": "Depósito em conta",
}

SOURCE_META = {
    "erp": {
        "label": "ERP sincronizado",
        "description": "A conciliação é feita pela cadeia interna de compra, título, baixa e desconto/depósito exato.",
        "formats": [],
    },
    "ipiranga_portal": {
        "label": "Extrato ou relatório Ipiranga",
        "description": "Importe o extrato postecipado ou o relatório de créditos da unidade no portal Ipiranga.",
        "formats": [".xls", ".xlsx", ".pdf"],
    },
    "texaco_portal": {
        "label": "Extrato Ipiranga/Texaco",
        "description": "Importe o extrato consolidado do portal para comprovar o crédito adicional S10.",
        "formats": [".xls", ".xlsx", ".pdf"],
    },
    "raizen_receipt": {
        "label": "Comprovante ou relatório Raízen",
        "description": "Importe o comprovante de crédito em conta emitido pela Raízen.",
        "formats": [".pdf"],
    },
}


class RaizenAllocation(BaseModel):
    reference_month: date
    amount: Decimal = Field(gt=0)


class RaizenAllocationIn(BaseModel):
    allocations: list[RaizenAllocation] = Field(min_length=1, max_length=48)


class RaizenReceiptError(ValueError):
    pass


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _filter_values(value) -> list[str]:
    if value is None:
        raw = []
    elif isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        default = getattr(value, "default", None)
        raw = [] if default is None or default is value else _filter_values(default)
    return [part.strip() for item in raw for part in item.split(",") if part.strip()]


def _filter_months(value) -> list[date]:
    months: set[date] = set()
    for raw in _filter_values(value):
        try:
            parsed = date.fromisoformat(f"{raw}-01" if len(raw) == 7 else raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Competência deve usar AAAA-MM ou AAAA-MM-DD.") from exc
        months.add(_month_start(parsed))
    return sorted(months, reverse=True)


def _is_historical_exclusion_adjustment(adjustment: ManualAdjustment) -> bool:
    """Keep an approved exclusion from being presented as received bonus."""
    reason = unicodedata.normalize("NFKD", adjustment.reason or "")
    reason = "".join(char for char in reason if not unicodedata.combining(char)).lower()
    return "exclusao historica" in reason and "fora da conciliacao contratual" in reason


def _unit_004_cumulative_snapshot(db: DbSession, rule: BonusRule, as_of: date, today: date) -> dict:
    """Return the approved accumulated portal-statement view for unit 004."""
    purchases = db.scalars(
        select(Purchase).where(
            Purchase.unit_code == rule.unit_code,
            Purchase.mapped_company_code == rule.company_code,
            Purchase.purchase_date >= rule.effective_from,
            Purchase.purchase_date <= as_of,
        )
    ).all()
    rate = Decimal(rule.rate_per_liter)
    total_expected = money(sum((Decimal(row.total_liters or 0) * rate for row in purchases), ZERO))
    historical_expected = money(
        sum(
            (
                Decimal(row.total_liters or 0) * rate
                for row in purchases
                if row.purchase_date <= UNIT_004_HISTORICAL_PURCHASE_CUTOFF
            ),
            ZERO,
        )
    )
    usage = [entry for entry in unit_004_portal_usage_events(db, rule) if entry.event.portal_date <= today]
    confirmed_credits = money(sum((Decimal(entry.event.value or 0) for entry in usage), ZERO))
    historical_credits = money(
        sum(
            (
                Decimal(entry.event.value or 0)
                for entry in usage
                if entry.event.portal_date <= UNIT_004_HISTORICAL_CREDIT_CUTOFF
            ),
            ZERO,
        )
    )
    historical_residual = money(historical_expected - historical_credits)
    later_expected = money(max(ZERO, total_expected - historical_expected))
    later_credits = money(max(ZERO, confirmed_credits - historical_credits))
    awaiting_statement = money(max(ZERO, later_expected - later_credits))
    residual_is_approved = abs(historical_residual - UNIT_004_APPROVED_HISTORICAL_RESIDUAL) <= Decimal("0.01")
    return {
        "expected": total_expected,
        "identified": confirmed_credits,
        "difference": money(total_expected - confirmed_credits),
        "historical_expected": historical_expected,
        "historical_identified": historical_credits,
        "historical_adjustment": historical_residual,
        "historical_adjustment_approved": residual_is_approved,
        "next_statement_expected": awaiting_statement,
        "credit_count": len(usage),
    }


def _normalized(value: str | None) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value or "").upper() if not unicodedata.combining(char)
    )


def _digits(value: str | None) -> str:
    return "".join(char for char in value or "" if char.isdigit())


def _same_cnpj(value: str | None, expected: str) -> bool:
    candidates = re.findall(r"(?:\d{2,3}\.\d{3}\.\d{3}/\d{4}-\d{2})|(?:\d{14,15})", value or "")
    return any(_digits(candidate)[-14:] == expected[-14:] for candidate in candidates)


def _decimal_br(value: str) -> Decimal:
    raw = value.strip().replace(" ", "")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return money(Decimal(raw))
    except Exception as exc:  # pragma: no cover - defensive parser wrapper
        raise RaizenReceiptError("Valor inválido no comprovante Raízen") from exc


def _source_type(rule: BonusRule) -> str:
    if rule.kind == "distributor_credit" and rule.unit_code in {"001", "003", "004", "008"}:
        return "ipiranga_portal"
    if rule.kind == "s10_excess_credit" and rule.unit_code == "050":
        return "texaco_portal"
    if rule.kind == "bank_deposit" and rule.unit_code == RAIZEN_UNIT:
        return "raizen_receipt"
    return "erp"


def _ipiranga_cumulative_card(
    db: DbSession,
    *,
    rule: BonusRule,
    reference_months: list[date],
    today: date,
    unit: Unit | None,
    imports: list[PortalStatementImport],
    users: dict[str, User],
) -> dict:
    """Build one operational card for an Ipiranga credit wallet.

    The statement is a cumulative source: it does not prove a separate
    contractual event for every calendar month.  Showing one card per
    competence made the same imported credit appear several times, which was
    both noisy and misleading.  The detailed monthly records remain available
    below the card and in the reconciliation history.
    """
    as_of_month = max(reference_months)
    as_of = min(month_end(as_of_month), today)
    rows = db.scalars(
        select(Reconciliation)
        .where(
            Reconciliation.rule_id == rule.id,
            Reconciliation.status != "superseded",
            Reconciliation.reference_month <= as_of_month,
        )
        .order_by(Reconciliation.reference_month)
    ).all()
    rows = [row for row in rows if row.status != "not_applicable"]
    historical_adjustment = ZERO
    next_statement_expected = ZERO

    if is_unit_004_portal_credit_rule(rule):
        snapshot = _unit_004_cumulative_snapshot(db, rule, as_of, today)
        expected = snapshot["expected"]
        identified = snapshot["identified"]
        historical_adjustment = (
            snapshot["historical_adjustment"]
            if snapshot["historical_adjustment_approved"]
            else ZERO
        )
        next_statement_expected = snapshot["next_statement_expected"]
        credit_event_count = snapshot["credit_count"]
        credit_history = [
            {
                "date": entry.event.portal_date,
                "value": float(money(Decimal(entry.event.value or 0))),
                "document": entry.purchase.invoice_number,
            }
            for entry in unit_004_portal_usage_events(db, rule)
            if entry.event.portal_date <= today
        ]
    else:
        expected = money(sum((Decimal(row.expected_value or 0) for row in rows), ZERO))
        events = db.scalars(
            select(PortalBonusEvent)
            .where(
                PortalBonusEvent.unit_code == rule.unit_code,
                PortalBonusEvent.company_code == rule.company_code,
                PortalBonusEvent.category == "postpaid",
                PortalBonusEvent.portal_date <= today,
            )
            .order_by(PortalBonusEvent.portal_date, PortalBonusEvent.id)
        ).all()
        portal_credit_total = money(sum((Decimal(event.value or 0) for event in events), ZERO))
        reconciliation_ids = [row.id for row in rows]
        allocated_credit_values = (
            db.scalars(
                select(ReconciliationAllocation.allocated_value)
                .join(ReconciliationEvidence, ReconciliationEvidence.id == ReconciliationAllocation.evidence_id)
                .join(ReconciliationItem, ReconciliationItem.id == ReconciliationAllocation.item_id)
                .where(
                    ReconciliationItem.reconciliation_id.in_(reconciliation_ids),
                    ReconciliationEvidence.source_type == "IPIRANGA_PORTAL",
                    ReconciliationEvidence.unit_code == rule.unit_code,
                    ReconciliationEvidence.counted.is_(True),
                    ReconciliationEvidence.evidence_date <= today,
                    ReconciliationAllocation.match_status.in_(("automatic", "accepted")),
                )
            ).all()
            if reconciliation_ids
            else []
        )
        # The portal wallet can retain a credit balance after a credit is split
        # across contractual competences.  Only the allocated amount proves a
        # competence; the remaining wallet balance is informational.
        identified = money(sum((Decimal(value or 0) for value in allocated_credit_values), ZERO))
        portal_unallocated = money(portal_credit_total - identified)
        historical_adjustment = money(sum((Decimal(row.manual_adjustment or 0) for row in rows), ZERO))
        credit_event_count = len(events)
        credit_history = [
            {"date": event.portal_date, "value": float(money(Decimal(event.value or 0))), "document": event.reference}
            for event in events
        ]

    # The operational card represents what has been reconciled, not only what
    # was emitted by the distributor. A historical adjustment is an audited
    # component of that total, while the portal subtotal remains separate.
    portal_appropriated = identified
    identified = money(portal_appropriated + historical_adjustment)

    not_due_expected = money(
        sum((Decimal(row.expected_value or 0) for row in rows if row.due_date and row.due_date >= today), ZERO)
    )
    mature_expected = money(expected - not_due_expected)
    effective_difference = money(expected - identified)
    mature_difference = money(mature_expected - identified)
    latest_import = max(
        imports,
        key=lambda item: (str(item.created_at or ""), item.period_end or date.min),
        default=None,
    )
    latest_import_payload = _import_payload(latest_import, users) if latest_import else None
    if latest_import_payload:
        latest_import_payload["latest_credit_date"] = db.scalar(
            select(PortalBonusEvent.portal_date)
            .join(PortalBonusEventSource, PortalBonusEventSource.event_id == PortalBonusEvent.id)
            .where(PortalBonusEventSource.import_id == latest_import.id)
            .order_by(PortalBonusEvent.portal_date.desc())
            .limit(1)
        )

    if expected <= ZERO:
        situation = "automatic"
        action = "Nenhuma bonificação liberada no período"
        description = "A regra permanece acompanhada, mas ainda não há valor contratual acumulado."
    elif abs(effective_difference) <= Decimal("0.01"):
        situation = "automatic"
        action = "Conciliação acumulada concluída"
        description = (
            "Os créditos postecipados do portal e os ajustes históricos auditados fecham o total calculado pela regra contratual."
            if historical_adjustment > ZERO
            else "Os créditos postecipados do portal fecham o total calculado pela regra contratual."
        )
    elif not imports:
        situation = "awaiting_statement" if portal_appropriated > ZERO else "awaiting_source"
        action = "Aguardar próximo extrato" if portal_appropriated > ZERO else "Importar extrato ou relatório Ipiranga"
        description = (
            "Já existem créditos do portal preservados, mas falta o extrato mais recente para atualizar o saldo acumulado."
            if portal_appropriated > ZERO
            else "Ainda não há extrato externo para comprovar os créditos postecipados acumulados."
        )
    elif abs(mature_difference) <= Decimal("0.01") and not_due_expected > ZERO:
        situation = "awaiting_due"
        action = "Aguardar competência ainda no prazo"
        description = (
            f"R$ {not_due_expected:,.2f} permanecem dentro do prazo contratual. "
            "O total será atualizado pelo próximo extrato, sem gerar cobrança agora."
        )
    elif latest_import and latest_import.period_end and latest_import.period_end < as_of:
        situation = "awaiting_statement"
        action = "Aguardar próximo extrato"
        description = (
            "O último extrato importado não alcança a data atual do acompanhamento. "
            "Importe a próxima atualização antes de tratar o saldo como divergência."
        )
    else:
        situation = "analysis"
        action = "Conferir saldo acumulado"
        description = (
            "Os créditos do portal e a bonificação calculada ainda não fecham após os ajustes auditados. "
            "Acompanhe o próximo extrato ou revise o saldo acumulado."
        )

    adjustment_note = (
        "Ajuste histórico aprovado incluído em Créditos apropriados"
        if historical_adjustment > ZERO
        else None
    )
    return {
        "id": f"{rule.id}:cumulative:{as_of_month.isoformat()}",
        "rule_id": rule.id,
        "unit_code": rule.unit_code,
        "unit_name": unit.display_name if unit else f"Unidade {rule.unit_code}",
        "brand": unit.brand if unit else rule.company_code,
        "company_code": rule.company_code,
        "rule_kind": rule.kind,
        "rule_label": RULE_LABELS.get(rule.kind, rule.kind),
        "reference_month": as_of_month,
        "as_of_date": as_of,
        "due_date": None,
        "expected_value": float(expected),
        "observed_value": float(identified),
        "difference_value": float(effective_difference),
        "portal_difference_value": float(money(expected - portal_appropriated)),
        "portal_appropriated_value": float(portal_appropriated),
        "portal_credit_total_value": float(portal_credit_total) if not is_unit_004_portal_credit_rule(rule) else float(portal_appropriated),
        "portal_unallocated_value": float(portal_unallocated) if not is_unit_004_portal_credit_rule(rule) else 0.0,
        "historical_adjustment_value": float(historical_adjustment),
        "next_statement_expected_value": float(next_statement_expected),
        "credit_event_count": credit_event_count,
        "credit_history": list(reversed(credit_history[-24:])),
        "competencies": [
            {
                "reference_month": row.reference_month,
                "due_date": row.due_date,
                "expected_value": float(money(Decimal(row.expected_value or 0))),
                "adjustment_value": float(money(Decimal(row.manual_adjustment or 0))),
            }
            for row in reversed(rows[-24:])
        ],
        "latest_import": latest_import_payload,
        "reconciliation_id": None,
        "confirmation_mode": "automatic" if situation == "automatic" else "none",
        "source_type": "ipiranga_portal",
        "source": SOURCE_META["ipiranga_portal"],
        "situation": situation,
        "action": action,
        "description": description,
        "adjustment_note": adjustment_note,
        "imports": [_import_payload(row, users) for row in imports[:20]],
        "queue_url": (
            f"/conciliacoes?unit={rule.unit_code}&state="
            f"{'confirmed' if situation == 'automatic' else 'waiting' if situation.startswith('awaiting') else 'actionable'}"
        ),
        "cumulative": True,
    }


def _import_payload(row: PortalStatementImport, users: dict[str, User]) -> dict:
    return {
        "id": row.id,
        "unit_code": row.unit_code,
        "company_code": row.company_code,
        "category": row.category,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "original_filename": row.original_filename,
        "content_sha256": row.content_sha256,
        "row_count": row.row_count,
        "imported_count": row.imported_count,
        "duplicate_count": row.duplicate_count,
        "uploaded_by": users.get(row.uploaded_by).full_name if row.uploaded_by in users else row.uploaded_by,
        "created_at": row.created_at,
        "file_url": f"/api/portal-statements/imports/{row.id}/file",
    }


def _covers_month(row: PortalStatementImport, reference_month: date) -> bool:
    start = _month_start(reference_month)
    end = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    if row.period_start and row.period_end:
        return row.period_start < end and row.period_end >= start
    created = row.created_at.date() if row.created_at else None
    return bool(created and _month_start(created) == start)


def _receipt_metadata(event: PortalBonusEvent) -> dict:
    try:
        payload = json.loads(event.review_notes or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _raizen_allocated_months(db: DbSession, *, ignore_event_id: str | None = None) -> set[str]:
    events = db.scalars(
        select(PortalBonusEvent).where(
            PortalBonusEvent.unit_code == RAIZEN_UNIT,
            PortalBonusEvent.company_code == RAIZEN_COMPANY,
            PortalBonusEvent.category == RAIZEN_RECEIPT_CATEGORY,
        )
    ).all()
    allocated: set[str] = set()
    for event in events:
        if event.id == ignore_event_id:
            continue
        allocated.update((_receipt_metadata(event).get("allocations") or {}).keys())
    return allocated


def _raizen_candidates(db: DbSession, *, ignore_event_id: str | None = None) -> list[Reconciliation]:
    allocated = _raizen_allocated_months(db, ignore_event_id=ignore_event_id)
    rows = db.execute(
        select(Reconciliation, BonusRule)
        .join(BonusRule, BonusRule.id == Reconciliation.rule_id)
        .where(
            Reconciliation.unit_code == RAIZEN_UNIT,
            Reconciliation.status != "superseded",
            Reconciliation.expected_value > 0,
            BonusRule.kind == "bank_deposit",
            BonusRule.company_code == RAIZEN_COMPANY,
        )
        .order_by(Reconciliation.reference_month)
    ).all()
    return [
        row
        for row, _ in rows
        if row.reference_month.isoformat() not in allocated and Decimal(row.observed_value or 0) <= ZERO
    ]


def _receipt_payload(event: PortalBonusEvent, db: DbSession) -> dict:
    metadata = _receipt_metadata(event)
    allocations = metadata.get("allocations") or {}
    candidates = _raizen_candidates(db, ignore_event_id=event.id)
    return {
        "id": event.id,
        "credit_date": event.portal_date,
        "value": float(money(Decimal(event.value or 0))),
        "bank_commitment": metadata.get("bank_commitment"),
        "client_commitment": metadata.get("client_commitment"),
        "payer_name": metadata.get("payer_name"),
        "beneficiary_name": metadata.get("beneficiary_name"),
        "allocations": [
            {"reference_month": month, "amount": float(money(Decimal(str(value))))}
            for month, value in sorted(allocations.items())
        ],
        "needs_allocation": not allocations,
        "candidate_competencies": [
            {
                "reference_month": row.reference_month,
                "expected_value": float(money(Decimal(row.expected_value))),
                "due_date": row.due_date,
            }
            for row in candidates
        ],
    }


def build_monthly_routine(
    db: DbSession,
    *,
    reference_months: list[date] | None = None,
    units: set[str] | None = None,
    companies: set[str] | None = None,
    source_types: set[str] | None = None,
    situations: set[str] | None = None,
    today: date | None = None,
) -> dict:
    """Build the read-only operational view from the current reconciliation snapshot."""
    today = today or date.today()
    reference_months = reference_months or [_month_start(date.today())]
    units = units or set()
    companies = companies or set()
    source_types = source_types or set()
    situations = situations or set()

    rules = db.scalars(select(BonusRule).where(BonusRule.active.is_(True))).all()
    unit_rows = {row.code: row for row in db.scalars(select(Unit)).all()}
    users = {row.id: row for row in db.scalars(select(User)).all()}
    reconciliations = db.scalars(
        select(Reconciliation).where(
            Reconciliation.status != "superseded",
            Reconciliation.reference_month.in_(reference_months),
        )
    ).all()
    reconciliation_by_key = {(row.rule_id, row.reference_month): row for row in reconciliations}
    reconciliation_items_by_id: dict[str, list[ReconciliationItem]] = defaultdict(list)
    reconciliation_ids = [row.id for row in reconciliations]
    if reconciliation_ids:
        for item in db.scalars(
            select(ReconciliationItem).where(ReconciliationItem.reconciliation_id.in_(reconciliation_ids))
        ).all():
            reconciliation_items_by_id[item.reconciliation_id].append(item)
    manual_adjustments_by_reconciliation: dict[str, list[ManualAdjustment]] = defaultdict(list)
    if reconciliation_ids:
        for adjustment in db.scalars(
            select(ManualAdjustment).where(ManualAdjustment.reconciliation_id.in_(reconciliation_ids))
        ).all():
            manual_adjustments_by_reconciliation[adjustment.reconciliation_id].append(adjustment)
    imports = db.scalars(select(PortalStatementImport).order_by(PortalStatementImport.created_at.desc())).all()
    imports_by_unit: dict[str, list[PortalStatementImport]] = defaultdict(list)
    for row in imports:
        imports_by_unit[row.unit_code].append(row)

    cards: list[dict] = []
    # Ipiranga credits are a statement wallet, so the operational routine has
    # exactly one card per unit/rule.  Calendar months stay inside the card's
    # audit history rather than duplicating its total across the screen.
    cumulative_rule_ids: set[str] = set()
    for rule in rules:
        source_type = _source_type(rule)
        if source_type != "ipiranga_portal":
            continue
        if units and rule.unit_code not in units:
            continue
        if companies and rule.company_code not in companies:
            continue
        if source_types and source_type not in source_types:
            continue
        card = _ipiranga_cumulative_card(
            db,
            rule=rule,
            reference_months=reference_months,
            today=today,
            unit=unit_rows.get(rule.unit_code),
            imports=imports_by_unit[rule.unit_code],
            users=users,
        )
        cumulative_rule_ids.add(rule.id)
        if situations and card["situation"] not in situations:
            continue
        cards.append(card)

    for reference_month in reference_months:
        for rule in rules:
            if rule.id in cumulative_rule_ids:
                continue
            if units and rule.unit_code not in units:
                continue
            if companies and rule.company_code not in companies:
                continue
            source_type = _source_type(rule)
            if source_types and source_type not in source_types:
                continue
            if is_unit_004_portal_credit_rule(rule) and unit_004_portal_usage_events(db, rule):
                as_of = min(month_end(reference_month), today)
                snapshot = _unit_004_cumulative_snapshot(db, rule, as_of, today)
                if snapshot["next_statement_expected"] > ZERO:
                    situation = "awaiting_statement"
                    action = "Aguardar próximo extrato"
                    description = (
                        f"Créditos confirmados no extrato Ipiranga: R$ {snapshot['identified']:,.2f}. "
                        f"R$ {snapshot['next_statement_expected']:,.2f} das compras posteriores ao corte histórico "
                        "aguardam o próximo extrato; isso não é cobrança por NF."
                    )
                elif snapshot["historical_adjustment_approved"]:
                    situation = "automatic"
                    action = "Conciliação acumulada concluída"
                    description = (
                        "O total de créditos Ipiranga foi conferido contra a bonificação calculada por volume. "
                        "O ajuste histórico de R$ 69,99 permanece apenas como acompanhamento auditável."
                    )
                else:
                    situation = "analysis"
                    action = "Conferir saldo acumulado"
                    description = (
                        "Os créditos do portal foram somados, mas o saldo histórico ainda não corresponde ao ajuste "
                        "aprovado de R$ 69,99. Importe o extrato que faltar antes de tratar como diferença."
                    )
                if situations and situation not in situations:
                    continue
                unit = unit_rows.get(rule.unit_code)
                cards.append(
                    {
                        "id": f"{rule.id}:accumulated:{reference_month.isoformat()}",
                        "rule_id": rule.id,
                        "unit_code": rule.unit_code,
                        "unit_name": unit.display_name if unit else f"Unidade {rule.unit_code}",
                        "brand": unit.brand if unit else rule.company_code,
                        "company_code": rule.company_code,
                        "rule_kind": rule.kind,
                        "rule_label": RULE_LABELS.get(rule.kind, rule.kind),
                        "reference_month": reference_month,
                        "due_date": None,
                        "expected_value": float(snapshot["expected"]),
                        "observed_value": float(snapshot["identified"]),
                        "difference_value": float(snapshot["difference"]),
                        "historical_expected_value": float(snapshot["historical_expected"]),
                        "historical_identified_value": float(snapshot["historical_identified"]),
                        "historical_adjustment_value": float(snapshot["historical_adjustment"]),
                        "next_statement_expected_value": float(snapshot["next_statement_expected"]),
                        "credit_count": snapshot["credit_count"],
                        "reconciliation_id": None,
                        "confirmation_mode": "automatic" if situation == "automatic" else "none",
                        "source_type": source_type,
                        "source": SOURCE_META[source_type],
                        "situation": situation,
                        "action": action,
                        "description": description,
                        "imports": [_import_payload(row, users) for row in imports_by_unit[rule.unit_code][:20]],
                        "queue_url": "/conciliacoes?unit=004&state=confirmed",
                    }
                )
                continue
            reconciliation = reconciliation_by_key.get((rule.id, reference_month))
            if reconciliation and reconciliation.status == "not_applicable":
                # Contractual exclusions retain their audit trail, but they
                # must not resurface as a monthly operational card.
                continue
            expected = money(Decimal(reconciliation.expected_value)) if reconciliation else ZERO
            historical_exclusion_value = money(
                sum(
                    (
                        Decimal(adjustment.amount or 0)
                        for adjustment in manual_adjustments_by_reconciliation.get(reconciliation.id, [])
                        if _is_historical_exclusion_adjustment(adjustment)
                    ),
                    ZERO,
                )
            ) if reconciliation else ZERO
            effective_adjustment = (
                money(Decimal(reconciliation.manual_adjustment or 0) - historical_exclusion_value)
                if reconciliation
                else ZERO
            )
            observed = (
                money(Decimal(reconciliation.observed_value) + effective_adjustment)
                if reconciliation
                else ZERO
            )
            difference = money(expected - observed) if reconciliation else ZERO
            confirmed = bool(
                reconciliation
                and (
                    reconciliation.status in {"confirmed", "auto_confirmed", "manual_confirmed"}
                    or (historical_exclusion_value > ZERO and abs(difference) <= Decimal("0.01"))
                )
            )
            covered_imports = [row for row in imports_by_unit[rule.unit_code] if _covers_month(row, reference_month)]
            invoice_items = reconciliation_items_by_id.get(reconciliation.id, []) if reconciliation else []
            # A remaining value with only unpaid titles cannot be charged or
            # treated as a missing benefit until the ERP settles the title.
            only_waiting_invoice_settlements = (
                rule.kind == "invoice_discount"
                and reconciliation is not None
                and reconciliation.status == "pending"
                and bool(invoice_items)
                and any(item.status == "pending" for item in invoice_items)
                and all(item.status in {"pending", "confirmed", "auto_confirmed", "manual_confirmed"} for item in invoice_items)
            )
            if expected <= ZERO:
                situation = "automatic"
                action = "Nenhuma bonificação liberada nesta competência"
                description = "A regra permanece monitorada, mas não há valor a conferir no período selecionado."
            elif confirmed:
                situation = "automatic"
                action = "Conferência concluída"
                description = "O valor esperado foi fechado por evidência exata e a trilha foi preservada."
            elif only_waiting_invoice_settlements:
                situation = "awaiting_settlement"
                action = "Aguardar baixa"
                description = (
                    "As demais notas da competência já foram conciliadas. O valor restante pertence a "
                    "título(s) ainda sem baixa no ERP e não representa cobrança até a liquidação."
                )
            elif (
                source_type == "ipiranga_portal"
                and reconciliation
                and reconciliation.status == "pending"
                and "unassigned_portal_credit" in (reconciliation.evidence_json or "")
            ):
                situation = "awaiting_competence"
                action = "Aguardar identificação da competência"
                description = (
                    "O extrato lista um crédito, mas não informa a competência. "
                    "A prova foi preservada sem apropriação automática; acompanhe a identificação da Ipiranga."
                )
            elif (
                source_type == "ipiranga_portal"
                and reconciliation
                and reconciliation.status == "pending"
                and reconciliation.due_date < today
                and covered_imports
            ):
                situation = "awaiting_portal_credit"
                action = "Aguardar crédito no portal"
                description = (
                    "O extrato já foi importado, mas ainda não traz um crédito que possa ser "
                    "vinculado com segurança a esta competência. Acompanhe a próxima atualização do portal Ipiranga."
                )
            elif source_type != "erp" and not covered_imports:
                situation = "awaiting_source"
                action = f"Importar {SOURCE_META[source_type]['label']}"
                description = SOURCE_META[source_type]["description"]
            elif reconciliation and reconciliation.due_date >= today and observed <= ZERO:
                # A prior file can cover the competence without containing the
                # credit itself yet.  Before the contractual deadline that is
                # expected timing, not an item that needs investigation.
                situation = "awaiting_due"
                action = "Aguardar vencimento e próxima atualização"
                description = (
                    "A competência ainda não venceu. O arquivo já importado não traz um crédito "
                    "para este mês; acompanhe a próxima atualização antes de tratar como diferença."
                    if source_type != "erp" and covered_imports
                    else "A competência ainda não venceu e não há valor identificado. Aguarde o vencimento ou a próxima baixa do ERP."
                )
            else:
                situation = "analysis"
                action = "Conferir vínculo e acompanhar"
                description = (
                    "Há arquivo importado, mas o valor ainda não fechou com uma prova exata."
                    if source_type != "erp"
                    else "O ERP ainda não apresentou uma cadeia financeira exata para concluir a competência."
                )
            if situations and situation not in situations:
                continue
            unit = unit_rows.get(rule.unit_code)
            cards.append(
                {
                    "id": f"{rule.id}:{reference_month.isoformat()}",
                    "rule_id": rule.id,
                    "unit_code": rule.unit_code,
                    "unit_name": unit.display_name if unit else f"Unidade {rule.unit_code}",
                    "brand": unit.brand if unit else rule.company_code,
                    "company_code": rule.company_code,
                    "rule_kind": rule.kind,
                    "rule_label": RULE_LABELS.get(rule.kind, rule.kind),
                    "reference_month": reference_month,
                    "due_date": reconciliation.due_date if reconciliation else None,
                    "expected_value": float(expected),
                    "observed_value": float(observed),
                    "difference_value": float(difference),
                    "reconciliation_id": reconciliation.id if reconciliation else None,
                    "confirmation_mode": reconciliation.confirmation_mode if reconciliation else "none",
                    "source_type": source_type,
                    "source": SOURCE_META[source_type],
                    "situation": situation,
                    "action": action,
                    "description": description,
                    "imports": [_import_payload(row, users) for row in covered_imports],
                    "queue_url": (
                        f"/conciliacoes?unit={rule.unit_code}&reference_month={reference_month.isoformat()}"
                        f"&state={'confirmed' if situation == 'automatic' else 'waiting' if situation in {'awaiting_source', 'awaiting_due', 'awaiting_settlement', 'awaiting_competence', 'awaiting_portal_credit', 'awaiting_statement'} else 'actionable'}"
                    ),
                }
            )

    order = {"awaiting_source": 0, "awaiting_due": 1, "awaiting_settlement": 2, "awaiting_competence": 3, "awaiting_portal_credit": 4, "awaiting_statement": 5, "analysis": 6, "automatic": 7}
    cards.sort(key=lambda row: (-date.fromisoformat(str(row["reference_month"])).toordinal(), order[row["situation"]], row["unit_code"], row["rule_kind"]))
    raizen_events = db.scalars(
        select(PortalBonusEvent)
        .where(
            PortalBonusEvent.unit_code == RAIZEN_UNIT,
            PortalBonusEvent.company_code == RAIZEN_COMPANY,
            PortalBonusEvent.category == RAIZEN_RECEIPT_CATEGORY,
        )
        .order_by(PortalBonusEvent.portal_date.desc())
    ).all()
    pending_receipts = [_receipt_payload(event, db) for event in raizen_events if not _receipt_metadata(event).get("allocations")]
    filtered_imports = [
        _import_payload(row, users)
        for row in imports
        if (not units or row.unit_code in units)
        and (not companies or row.company_code in companies)
    ]
    return {
        "reference_months": reference_months,
        "cards": cards,
        "summary": {
            "automatic": sum(row["situation"] == "automatic" for row in cards),
            "awaiting_source": sum(row["situation"] == "awaiting_source" for row in cards),
            "awaiting_due": sum(row["situation"] == "awaiting_due" for row in cards),
            "awaiting_settlement": sum(row["situation"] == "awaiting_settlement" for row in cards),
            "awaiting_competence": sum(row["situation"] == "awaiting_competence" for row in cards),
            "awaiting_portal_credit": sum(row["situation"] == "awaiting_portal_credit" for row in cards),
            "awaiting_statement": sum(row["situation"] == "awaiting_statement" for row in cards),
            "analysis": sum(row["situation"] == "analysis" for row in cards),
            "expected_value": float(sum((Decimal(str(row["expected_value"])) for row in cards), ZERO)),
            "open_value": float(sum((abs(Decimal(str(row["difference_value"]))) for row in cards if row["situation"] != "automatic"), ZERO)),
        },
        "imports": filtered_imports[:80],
        "raizen_receipts": pending_receipts,
        "source_options": [{"value": key, "label": value["label"]} for key, value in SOURCE_META.items()],
    }


@router.get("")
def monthly_routine(
    db: DbSession,
    _: CurrentUser,
    reference_month: list[str] | None = Query(None),
    unit: list[str] | None = Query(None),
    company: list[str] | None = Query(None),
    source_type: list[str] | None = Query(None),
    situation: list[str] | None = Query(None),
):
    return build_monthly_routine(
        db,
        reference_months=_filter_months(reference_month),
        units={value.zfill(3) for value in _filter_values(unit)},
        companies={value.upper() for value in _filter_values(company)},
        source_types=set(_filter_values(source_type)),
        situations=set(_filter_values(situation)),
    )


def _validated_portal_upload(file: UploadFile):
    return read_validated_upload(
        file,
        max_bytes=MAX_PORTAL_BYTES,
        signatures={
            ".pdf": (b"%PDF-",),
            ".xlsx": (b"PK\x03\x04",),
            ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
        },
    )


@router.post("/imports/ipiranga")
async def import_portal_statement(
    db: DbSession,
    actor: AdminUser,
    unit_code: str = Form(...),
    file: UploadFile = File(...),
):
    unit_code = unit_code.zfill(3)
    try:
        uploaded = await _validated_portal_upload(file)
        row, already_imported = import_ipiranga_statement(
            db,
            unit_code=unit_code,
            filename=uploaded.filename,
            content_type=file.content_type,
            content=uploaded.content,
            uploaded_by=actor.id,
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except PortalStatementError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(
        db,
        actor,
        "monthly_routine_portal_import",
        "portal_statement_import",
        row.id,
        {"unit_code": unit_code, "filename": row.original_filename, "sha256": row.content_sha256, "already_imported": already_imported},
    )
    rebuild_reconciliations(db)
    return {
        "import": _import_payload(row, {actor.id: actor}),
        "already_imported": already_imported,
        "routine": build_monthly_routine(db),
    }


def _parse_raizen_receipt_pages(content: bytes) -> list[dict]:
    try:
        reader = PdfReader(BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pragma: no cover - library-specific wrapper
        raise RaizenReceiptError("Não foi possível ler o PDF do comprovante Raízen") from exc
    if not pages:
        raise RaizenReceiptError("O comprovante Raízen não possui páginas legíveis")

    pattern = re.compile(
        r"N[ºO°]?\s*COMPROMISSO\s*BANCO\s+N[ºO°]?\s*COMPROMISSO\s*CLIENTE\s+DATA\s+DO\s*CR[ÉE]DITO\s+VALOR\s+(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{4})\s+([\d.,]+)",
        re.IGNORECASE | re.DOTALL,
    )
    receipts: list[dict] = []
    for page_number, text in enumerate(pages, start=1):
        normalized = _normalized(text)
        if "RAIZEN" not in normalized or not _same_cnpj(text, RAIZEN_PAYER_CNPJ):
            raise RaizenReceiptError(f"A página {page_number} não comprova pagamento da Raízen S.A.")
        if "BENEFICIARIO" not in normalized or not _same_cnpj(text, RAIZEN_BENEFICIARY_CNPJ):
            raise RaizenReceiptError(f"A página {page_number} não pertence à unidade 054 (beneficiário incompatível)")
        match = pattern.search(text)
        if not match:
            raise RaizenReceiptError(f"Compromisso, data de crédito ou valor não localizado na página {page_number}")
        bank_commitment, client_commitment, credit_date, value = match.groups()
        receipts.append(
            {
                "page": page_number,
                "bank_commitment": bank_commitment,
                "client_commitment": client_commitment,
                "credit_date": datetime.strptime(credit_date, "%d/%m/%Y").date(),
                "value": _decimal_br(value),
                "payer_name": "Raízen S.A.",
                "payer_cnpj": RAIZEN_PAYER_CNPJ,
                "beneficiary_cnpj": RAIZEN_BENEFICIARY_CNPJ,
            }
        )
    return receipts


def _raizen_event_key(receipt: dict) -> str:
    identity = "|".join(
        [
            RAIZEN_UNIT,
            receipt["bank_commitment"],
            receipt["client_commitment"],
            receipt["credit_date"].isoformat(),
            str(receipt["value"]),
        ]
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def _set_automatic_raizen_allocation(db: DbSession, event: PortalBonusEvent) -> bool:
    candidates = [
        row
        for row in _raizen_candidates(db, ignore_event_id=event.id)
        if row.reference_month < _month_start(event.portal_date)
        and money(Decimal(row.expected_value)) == money(Decimal(event.value))
    ]
    if len(candidates) != 1:
        return False
    metadata = _receipt_metadata(event)
    metadata["allocations"] = {candidates[0].reference_month.isoformat(): str(money(Decimal(event.value)))}
    metadata["allocation_mode"] = "automatic_unique_exact"
    event.review_notes = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return True


@router.post("/imports/raizen/054")
async def import_raizen_receipt(db: DbSession, actor: AdminUser, file: UploadFile = File(...)):
    try:
        uploaded = await read_validated_upload(file, max_bytes=MAX_PORTAL_BYTES, signatures={".pdf": (b"%PDF-",)})
        receipts = _parse_raizen_receipt_pages(uploaded.content)
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RaizenReceiptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    digest = hashlib.sha256(uploaded.content).hexdigest()
    import_row = db.scalar(select(PortalStatementImport).where(PortalStatementImport.content_sha256 == digest))
    already_imported = import_row is not None
    if import_row:
        return {
            "import": _import_payload(import_row, {actor.id: actor}),
            "already_imported": True,
            "automatically_allocated": 0,
            "routine": build_monthly_routine(db),
        }
    if not import_row:
        import_row = PortalStatementImport(
            unit_code=RAIZEN_UNIT,
            company_code=RAIZEN_COMPANY,
            category=RAIZEN_RECEIPT_CATEGORY,
            client_cnpj=RAIZEN_BENEFICIARY_CNPJ,
            period_start=min(receipt["credit_date"] for receipt in receipts),
            period_end=max(receipt["credit_date"] for receipt in receipts),
            original_filename=Path(uploaded.filename).name[:255],
            content_type=file.content_type or "application/pdf",
            content_sha256=digest,
            source_file=uploaded.content,
            row_count=len(receipts),
            imported_count=0,
            duplicate_count=0,
            uploaded_by=actor.id,
        )
        db.add(import_row)
        db.flush()

    # Build the current expected competences before trying an unambiguous exact allocation.
    rebuild_reconciliations(db, commit=False)
    created = 0
    automatically_allocated = 0
    for receipt in receipts:
        event_key = _raizen_event_key(receipt)
        event = db.scalar(select(PortalBonusEvent).where(PortalBonusEvent.event_key == event_key))
        if not event:
            metadata = {
                "import_id": import_row.id,
                "page": receipt["page"],
                "bank_commitment": receipt["bank_commitment"],
                "client_commitment": receipt["client_commitment"],
                "payer_name": receipt["payer_name"],
                "payer_cnpj": receipt["payer_cnpj"],
                "beneficiary_cnpj": receipt["beneficiary_cnpj"],
                "allocations": {},
            }
            event = PortalBonusEvent(
                unit_code=RAIZEN_UNIT,
                company_code=RAIZEN_COMPANY,
                category=RAIZEN_RECEIPT_CATEGORY,
                portal_date=receipt["credit_date"],
                value=receipt["value"],
                description="TED Raízen - bonificação contratual",
                reference=f"{receipt['bank_commitment']}/{receipt['client_commitment']}",
                client_cnpj=RAIZEN_BENEFICIARY_CNPJ,
                business_classification="contractual_bonus",
                review_notes=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                reviewed_by=actor.id,
                reviewed_at=datetime.now(timezone.utc),
                event_key=event_key,
            )
            db.add(event)
            db.flush()
            created += 1
            import_row.imported_count += 1
            if _set_automatic_raizen_allocation(db, event):
                automatically_allocated += 1
        else:
            import_row.duplicate_count += 1
        if not db.scalar(
            select(PortalBonusEventSource).where(
                PortalBonusEventSource.import_id == import_row.id,
                PortalBonusEventSource.event_id == event.id,
            )
        ):
            db.add(
                PortalBonusEventSource(
                    import_id=import_row.id,
                    event_id=event.id,
                    row_number=receipt["page"],
                    raw_json=json.dumps(receipt, ensure_ascii=False, default=str),
                )
            )

    audit(
        db,
        actor,
        "monthly_routine_raizen_import",
        "portal_statement_import",
        import_row.id,
        {
            "filename": import_row.original_filename,
            "sha256": digest,
            "receipts": len(receipts),
            "created_events": created,
            "automatically_allocated": automatically_allocated,
            "already_imported": already_imported,
        },
    )
    rebuild_reconciliations(db)
    return {
        "import": _import_payload(import_row, {actor.id: actor}),
        "already_imported": already_imported,
        "automatically_allocated": automatically_allocated,
        "routine": build_monthly_routine(db),
    }


@router.post("/raizen/054/receipts/{event_id}/allocations")
def allocate_raizen_receipt(event_id: str, payload: RaizenAllocationIn, db: DbSession, actor: AdminUser):
    event = db.get(PortalBonusEvent, event_id)
    if not event or event.unit_code != RAIZEN_UNIT or event.category != RAIZEN_RECEIPT_CATEGORY:
        raise HTTPException(status_code=404, detail="Comprovante Raízen não encontrado")

    normalized_allocations: dict[str, Decimal] = {}
    for allocation in payload.allocations:
        month = _month_start(allocation.reference_month).isoformat()
        if month in normalized_allocations:
            raise HTTPException(status_code=422, detail="Uma competência não pode ser repetida no rateio")
        normalized_allocations[month] = money(Decimal(allocation.amount))
    total = money(sum(normalized_allocations.values(), ZERO))
    if total != money(Decimal(event.value)):
        raise HTTPException(status_code=422, detail="A soma do rateio deve ser exatamente igual ao valor do comprovante")

    candidates = {row.reference_month.isoformat(): row for row in _raizen_candidates(db, ignore_event_id=event.id)}
    for month, amount in normalized_allocations.items():
        row = candidates.get(month)
        if not row:
            raise HTTPException(status_code=422, detail=f"A competência {month} não está disponível para este rateio")
        if money(Decimal(row.expected_value)) != amount:
            raise HTTPException(
                status_code=422,
                detail=f"O rateio de {month} deve fechar exatamente o esperado da competência",
            )

    previous = _receipt_metadata(event).get("allocations") or {}
    metadata = _receipt_metadata(event)
    metadata["allocations"] = {month: str(value) for month, value in sorted(normalized_allocations.items())}
    metadata["allocation_mode"] = "manual_audited"
    event.review_notes = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    event.reviewed_by = actor.id
    event.reviewed_at = datetime.now(timezone.utc)
    audit(
        db,
        actor,
        "monthly_routine_raizen_allocation",
        "portal_bonus_event",
        event.id,
        {"previous_allocations": previous, "allocations": metadata["allocations"], "receipt_value": str(event.value)},
    )
    rebuild_reconciliations(db)
    return {"routine": build_monthly_routine(db)}
