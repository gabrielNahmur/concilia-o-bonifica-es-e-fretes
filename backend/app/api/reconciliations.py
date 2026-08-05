import json
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from app.dependencies import AdminUser, CurrentUser, DbSession
from app.models import (
    BonusRule,
    InvoiceBoletoEvidence,
    ManualAdjustment,
    Reconciliation,
    ReconciliationAllocation,
    ReconciliationException,
    ReconciliationInformationRequest,
    ReconciliationItem,
    ReconciliationReview,
)
from app.services.audit import audit
from app.services.boleto_evidence import MAX_BOLETO_BYTES, BoletoEvidenceError, import_boleto_evidence
from app.services.reconciliation import rebuild_reconciliations
from app.services.reconciliation_detail import build_reconciliation_detail
from app.services.reconciliation_workspace import recompute_reconciliation_confirmation
from app.services.reconciliation_workspace import CONTRACTUAL_EXCLUSION_REVIEW_STATUS
from app.services.rules import money, reconciliation_status
from app.services.unit_004_portal_usage import is_unit_004_portal_credit_rule
from app.services.uploads import UploadValidationError, read_validated_upload


router = APIRouter(prefix="/reconciliations", tags=["conciliações"])


class ConfirmInput(BaseModel):
    notes: str | None = None


class AdjustInput(BaseModel):
    amount: Decimal
    reason: str = Field(min_length=5, max_length=1000)


class ItemReviewInput(BaseModel):
    action: str = Field(pattern="^(accept|reject|needs_information)$")
    reason_code: str = Field(min_length=3, max_length=50)
    notes: str = Field(min_length=5, max_length=1500)


class InformationRequestResponseInput(BaseModel):
    notes: str = Field(min_length=5, max_length=1500)


class ExceptionActionInput(BaseModel):
    action: str = Field(pattern="^(start_review|resolve|reopen)$")
    resolution_code: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=1500)
    assigned_to: str | None = None


def _filter_values(value: str | Iterable[str] | None) -> list[str]:
    """Accept repeated and comma-separated filters from the operational UI."""
    # Direct service/API tests call the endpoint function without FastAPI's
    # dependency resolution, therefore omitted Query parameters still hold the
    # ``Query(None)`` descriptor.  Treat that descriptor exactly as an empty
    # filter just as FastAPI does on a real HTTP request.
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


def _filter_months(value: str | Iterable[str] | None) -> set[date]:
    months: set[date] = set()
    for raw in _filter_values(value):
        try:
            parsed = date.fromisoformat(f"{raw}-01" if len(raw) == 7 else raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Competência deve usar AAAA-MM ou AAAA-MM-DD.") from exc
        months.add(parsed.replace(day=1))
    return months


def _boleto_payload(row: InvoiceBoletoEvidence, already_imported: bool = False) -> dict:
    return {
        "id": row.id,
        "reconciliation_id": row.reconciliation_id,
        "unit_code": row.unit_code,
        "company_code": row.company_code,
        "purchase_entry_id": row.purchase_entry_id,
        "payable_document_id": row.payable_document_id,
        "original_filename": row.original_filename,
        "content_sha256": row.content_sha256,
        "cedent_name": row.cedent_name,
        "payer_name": row.payer_name,
        "payer_cnpj": row.payer_cnpj,
        "boleto_document_number": row.boleto_document_number,
        "invoice_number": row.invoice_number,
        "title_document_id": row.title_document_id,
        "nosso_numero": row.nosso_numero,
        "issue_date": row.issue_date,
        "due_date": row.due_date,
        "gross_value": float(row.gross_value),
        "discount_value": float(row.discount_value),
        "net_value": float(row.net_value),
        "expected_discount_value": (
            float(row.expected_discount_value) if row.expected_discount_value is not None else None
        ),
        "match_status": row.match_status,
        "match_basis": row.match_basis,
        "uploaded_by": row.uploaded_by,
        "created_at": row.created_at,
        "already_imported": already_imported,
        "file_url": f"/api/reconciliations/boleto-evidence/{row.id}/file",
    }


def _payload(row: Reconciliation, rule: BonusRule | None, db=None) -> dict:
    evidence = json.loads(row.evidence_json or "[]")
    item_count = open_exception_count = review_required_count = 0
    if db is not None:
        item_count = db.scalar(
            select(func.count()).select_from(ReconciliationItem).where(
                ReconciliationItem.reconciliation_id == row.id
            )
        ) or 0
        open_exception_count = db.scalar(
            select(func.count()).select_from(ReconciliationException).where(
                ReconciliationException.reconciliation_id == row.id,
                ReconciliationException.status.in_(("open", "in_review")),
            )
        ) or 0
        review_required_count = db.scalar(
            select(func.count()).select_from(ReconciliationItem).where(
                ReconciliationItem.reconciliation_id == row.id,
                ReconciliationItem.status.in_(("review_required", "partial", "overdue", "divergent", "data_gap")),
            )
        ) or 0
    return {
        "id": row.id,
        "unit_code": row.unit_code,
        "rule_kind": rule.kind if rule else None,
        "reference_month": row.reference_month,
        "due_date": row.due_date,
        "expected_value": float(row.expected_value),
        "observed_value": float(row.observed_value),
        "manual_adjustment": float(row.manual_adjustment),
        "difference_value": float(row.difference_value),
        "status": row.status,
        "confidence": row.confidence,
        "confirmation_mode": row.confirmation_mode,
        "auto_confirmed_at": row.auto_confirmed_at,
        "algorithm_version": row.algorithm_version,
        "notes": row.notes,
        "evidence_count": len(evidence),
        "item_count": item_count,
        "open_exception_count": open_exception_count,
        "review_required_count": review_required_count,
        "confirmed_at": row.confirmed_at,
    }


def _record_review(db, row: Reconciliation, rule: BonusRule, user, action: str, notes: str | None):
    snapshot = build_reconciliation_detail(db, row, rule)
    review = ReconciliationReview(
        reconciliation_id=row.id,
        action=action,
        expected_value=row.expected_value,
        observed_value=Decimal(row.observed_value) + Decimal(row.manual_adjustment or 0),
        difference_value=row.difference_value,
        confidence=row.confidence,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, default=str),
        notes=notes,
        created_by=user.id,
    )
    db.add(review)
    return review


def _split_information_request_notes(value: str | None) -> tuple[str, str]:
    raw = (value or "").strip()
    if raw.startswith("[") and "]" in raw:
        reason, notes = raw[1:].split("]", 1)
        return reason.strip() or "solicitacao_interna", notes.strip() or "Sem justificativa registrada."
    return "solicitacao_interna", raw or "Sem justificativa registrada."


def _legacy_information_request(db, item: ReconciliationItem) -> ReconciliationInformationRequest:
    reason_code, request_notes = _split_information_request_notes(item.review_notes)
    request = ReconciliationInformationRequest(
        reconciliation_id=item.reconciliation_id,
        item_id=item.id,
        status="open",
        reason_code=reason_code[:50],
        request_notes=request_notes,
        requested_by=item.reviewed_by or "",
        requested_at=item.reviewed_at or datetime.now(timezone.utc),
    )
    db.add(request)
    db.flush()
    return request


def _filtered_open_exception_count(db, filters, rule_kind: str | None) -> int:
    statement = (
        select(func.count())
        .select_from(ReconciliationException)
        .join(Reconciliation, Reconciliation.id == ReconciliationException.reconciliation_id)
    )
    if rule_kind:
        statement = statement.join(BonusRule, BonusRule.id == Reconciliation.rule_id).where(
            BonusRule.kind == rule_kind
        )
    return db.scalar(
        statement.where(
            *filters,
            ReconciliationException.status.in_(("open", "in_review")),
        )
    ) or 0


@router.get("")
def reconciliations(
    db: DbSession,
    _: CurrentUser,
    unit: str | None = None,
    status: str | None = None,
    reference_month: date | None = None,
    rule_kind: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    filters = [Reconciliation.status != "superseded"]
    if unit:
        filters.append(Reconciliation.unit_code == unit.zfill(3))
    if status:
        filters.append(Reconciliation.status == status)
    if reference_month:
        filters.append(Reconciliation.reference_month == reference_month.replace(day=1))
    statement = select(Reconciliation)
    if rule_kind:
        statement = statement.join(BonusRule).where(BonusRule.kind == rule_kind)
    statement = statement.where(*filters)
    all_rows = db.scalars(statement.order_by(Reconciliation.reference_month.desc(), Reconciliation.unit_code)).all()
    total = len(all_rows)
    rows = db.scalars(
        statement
        .order_by(Reconciliation.reference_month.desc(), Reconciliation.unit_code)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    rules = {row.id: row for row in db.scalars(select(BonusRule)).all()}
    status_counts = Counter(item.status for item in all_rows)
    return {
        "items": [_payload(row, rules.get(row.rule_id), db) for row in rows],
        "total": total,
        "page": page,
        "pages": (total + page_size - 1) // page_size,
        "summary": {
            "expected_value": float(sum((Decimal(item.expected_value) for item in all_rows), Decimal("0"))),
            "observed_value": float(sum((Decimal(item.observed_value) + Decimal(item.manual_adjustment or 0) for item in all_rows), Decimal("0"))),
            "difference_value": float(sum((Decimal(item.difference_value) for item in all_rows), Decimal("0"))),
            "status_counts": dict(status_counts),
            "review_required": sum(
                status_counts.get(value, 0)
                for value in ("review_required", "partial", "divergent", "overdue", "data_gap")
            ),
            "manually_reviewed": sum(1 for item in all_rows if item.confirmed_at is not None),
            "automatically_confirmed": sum(1 for item in all_rows if item.confirmation_mode == "automatic"),
            "open_exceptions": _filtered_open_exception_count(db, filters, rule_kind),
        },
    }


INVOICE_COVERAGE_LABELS = {
    "late_payment": "Pago com atraso",
    "credit_issued_awaiting_payment": "Crédito emitido aguardando baixa",
    "exact_confirmed": "Desconto contratual confirmado",
    "paid_without_discount": "Pagamento integral sem desconto",
    "contract_discount_shortfall": "Desconto abaixo da regra",
    "unclassified_excess_discount": "Crédito superior não decomponível",
    "unpaid_overdue": "Título vencido aguardando baixa",
    "unpaid_pending": "Título ainda no prazo",
    "data_gap": "Lacuna na cadeia interna",
    "manual_review": "Decisão manual",
    "integrity_violation": "Violação da política automática",
}


def _invoice_coverage_category(item: ReconciliationItem) -> str:
    details = json.loads(item.details_json or "{}")
    decision_code = details.get("decision_code")
    if item.status == "late_payment" or decision_code == "late_payment_forfeits_discount":
        return "late_payment"
    if item.status == "auto_confirmed":
        if details.get("portal_postpaid_exact") or (
            details.get("payment_chain_exact") and details.get("exact_native_chain")
        ):
            return "exact_confirmed"
        return "integrity_violation"
    if decision_code in {
        "paid_without_discount",
        "contract_discount_shortfall",
        "unclassified_excess_discount",
    } and details.get("payment_chain_exact"):
        return decision_code
    if decision_code == "portal_credit_issued_awaiting_payment":
        return "credit_issued_awaiting_payment"
    if decision_code == "awaiting_payment":
        return "unpaid_overdue" if item.status == "overdue" else "unpaid_pending"
    if item.status in {"manual_confirmed"} or item.review_status == "accepted":
        return "manual_review"
    return "data_gap"


def _coverage_payload(rows: list[tuple[ReconciliationItem, Reconciliation, BonusRule]]) -> dict:
    counts = Counter()
    values: dict[str, Decimal] = {}
    algorithms = Counter()
    for item, reconciliation, _ in rows:
        category = _invoice_coverage_category(item)
        counts[category] += 1
        values[category] = values.get(category, Decimal("0")) + Decimal(item.expected_value or 0)
        algorithms[reconciliation.algorithm_version or "sem-versão"] += 1
    total = len(rows)
    confirmed = counts.get("exact_confirmed", 0)
    settled_exceptions = sum(
        counts.get(value, 0)
        for value in (
            "paid_without_discount",
            "contract_discount_shortfall",
            "unclassified_excess_discount",
        )
    )
    waiting = (
        counts.get("unpaid_overdue", 0)
        + counts.get("unpaid_pending", 0)
        + counts.get("credit_issued_awaiting_payment", 0)
    )
    late_payment = counts.get("late_payment", 0)
    deterministic = confirmed + settled_exceptions + late_payment
    valid_automatic = confirmed
    integrity_violations = counts.get("integrity_violation", 0)
    return {
        "total_items": total,
        "exact_confirmed": confirmed,
        "settled_exceptions": settled_exceptions,
        "late_payment": late_payment,
        "waiting_payment": waiting,
        "data_gaps": counts.get("data_gap", 0),
        "deterministic_decisions": deterministic,
        "closed_rate": round((deterministic / total * 100), 2) if total else 0,
        "automatic_integrity_rate": round(
            (valid_automatic / (valid_automatic + integrity_violations) * 100), 2
        ) if valid_automatic + integrity_violations else 0,
        "automatic_integrity_violations": integrity_violations,
        "counts": dict(counts),
        "expected_values": {key: float(money(value)) for key, value in values.items()},
        "labels": INVOICE_COVERAGE_LABELS,
        "algorithm_versions": dict(algorithms),
    }


@router.get("/coverage")
def reconciliation_coverage(
    db: DbSession,
    _: CurrentUser,
    unit: str | None = None,
):
    statement = (
        select(ReconciliationItem, Reconciliation, BonusRule)
        .join(Reconciliation, Reconciliation.id == ReconciliationItem.reconciliation_id)
        .join(BonusRule, BonusRule.id == Reconciliation.rule_id)
        .where(
            Reconciliation.status != "superseded",
            BonusRule.kind == "invoice_discount",
            ReconciliationItem.item_type == "invoice",
        )
    )
    if unit:
        statement = statement.where(Reconciliation.unit_code == unit.zfill(3))
    rows = list(db.execute(statement.order_by(Reconciliation.unit_code, ReconciliationItem.source_date)).all())
    by_unit: dict[str, list[tuple[ReconciliationItem, Reconciliation, BonusRule]]] = {}
    for row in rows:
        by_unit.setdefault(row[1].unit_code, []).append(row)
    return {
        "summary": _coverage_payload(rows),
        "units": [
            {"unit_code": unit_code, **_coverage_payload(unit_rows)}
            for unit_code, unit_rows in sorted(by_unit.items())
        ],
        "policy": {
            "name": "Cadeia nativa estrita",
            "positive_confirmation": "NF + título único + baixa MDCMP + MLANF 51 + desconto MDCMP + MLANF 6204, com valores exatos e candidatos únicos.",
            "negative_classification": "Título pago com baixa/51 exata e ausência de desconto, ou desconto/6204 exato com valor diferente da regra.",
            "external_documents_counted": False,
        },
    }


QUEUE_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
QUEUE_REASON_BY_DECISION = {
    "late_payment_forfeits_discount": "Título pago após o vencimento; o desconto contratual não é aplicável.",
    "portal_credit_issued_awaiting_payment": (
        "O crédito contratual já foi emitido no portal Texaco e vinculado por valor exato; "
        "o título ainda aguarda baixa financeira."
    ),
    "missing_title": "Falta localizar o título financeiro desta nota.",
    "ambiguous_title": "Há mais de um título ligado à nota; escolha exige revisão.",
    "awaiting_payment": "O título ainda aguarda baixa financeira.",
    "incomplete_payment_chain": "A baixa existe, mas falta o vínculo financeiro único.",
    "paid_without_discount": "O título foi pago sem desconto contratual identificado.",
    "contract_discount_shortfall": "O desconto identificado ficou abaixo do valor contratual.",
    "unclassified_excess_discount": "Há crédito superior sem composição contratual comprovada.",
    "incomplete_discount_chain": "O desconto existe, mas a cadeia financeira está incompleta.",
}


def _queue_state(status: str, due_date: date | None = None) -> str:
    if status in {"auto_confirmed", "manual_confirmed", "confirmed", "late_payment"}:
        return "confirmed"
    if status == "pending":
        return "waiting"
    # A partial credit before its contractual deadline is visible, but it is
    # not yet an operational charge. It returns to the work queue only if the
    # difference remains after that date.
    if status == "partial" and due_date and due_date >= date.today():
        return "waiting"
    return "actionable"


def _effective_item_due_date(
    item: ReconciliationItem | None,
    fallback: date | None,
    details: dict | None = None,
) -> date | None:
    """Use the ERP title due date for a granular invoice queue item.

    A ``Reconciliation`` is intentionally monthly, so its due date is useful
    for credit, deposit and milestone rules.  Invoice discounts are different:
    each work-queue row represents one NF and must show the due date of its
    linked payable title rather than the month-end aggregate date.
    """
    if item is None:
        return fallback
    if details is None:
        try:
            details = json.loads(item.details_json or "{}")
        except (TypeError, json.JSONDecodeError):
            details = {}
    raw_due_date = details.get("title_due_date")
    if not raw_due_date:
        return fallback
    try:
        return date.fromisoformat(str(raw_due_date)[:10])
    except (TypeError, ValueError):
        return fallback


def _queue_reason(
    rule: BonusRule,
    status: str,
    details: dict,
    exceptions: list[ReconciliationException],
    has_management_adjustment: bool = False,
) -> str:
    if has_management_adjustment:
        return (
            "Competência encerrada por ajuste gerencial histórico aprovado. "
            "O valor não representa crédito emitido pela distribuidora nem lançamento identificado no ERP."
        )
    if status in {"auto_confirmed", "manual_confirmed", "confirmed"}:
        return "Conciliação confirmada: o valor identificado fecha o esperado e a prova vinculada foi preservada."
    if exceptions:
        return exceptions[0].title or exceptions[0].description
    decision = details.get("decision_code")
    if decision in QUEUE_REASON_BY_DECISION:
        return QUEUE_REASON_BY_DECISION[decision]
    if rule.kind == "distributor_credit":
        if rule.unit_code in {"001", "003", "004", "008"} and rule.company_code == "IPIRANGA":
            return (
                "A bonificação do mês aguarda o crédito postecipado no extrato Ipiranga. "
                "As compras desta competência já estão consideradas no valor esperado."
            )
        if status in {"pending", "partial"}:
            return (
                "Aguardando a utilização do crédito na distribuidora ou a próxima atualização do ERP. "
                "Descontos técnicos de títulos são usados somente como prova dessa utilização."
            )
        return "O crédito na distribuidora ainda não possui comprovação completa de utilização."
    if status == "pending":
        return "Aguardando vencimento, baixa ou próxima atualização do ERP."
    if rule.kind == "bank_deposit":
        return "O depósito ainda não foi identificado de forma determinística."
    if rule.kind == "milestone_bonus":
        return "O marco do guarda-chuva precisa de conferência de recebimento."
    if rule.kind == "s10_excess_credit":
        return "O adicional S10 aguarda evidência de crédito com origem comprovada."
    return "A conciliação precisa de revisão documental."


def _queue_action(
    rule: BonusRule,
    state: str,
    status: str,
    details: dict,
    exceptions: list[ReconciliationException],
    has_management_adjustment: bool = False,
) -> str:
    if has_management_adjustment:
        return "Ajuste histórico registrado"
    if status == "late_payment":
        return "Pago com atraso"
    if state == "confirmed":
        return "Conferência concluída"
    if rule.kind == "distributor_credit" and state == "waiting":
        if rule.unit_code in {"001", "003", "004", "008"} and rule.company_code == "IPIRANGA":
            return "Aguardar extrato Ipiranga"
        return "Aguardar crédito na distribuidora"
    if state == "waiting":
        return "Aguardar complemento" if status == "partial" else "Aguardar baixa"
    types = {item.exception_type for item in exceptions}
    decision = details.get("decision_code")
    if decision == "portal_credit_issued_awaiting_payment":
        return "Aguardar baixa"
    if "paid_without_discount" in types or decision == "paid_without_discount":
        return "Cobrar desconto"
    if "missing_title" in types or decision == "missing_title":
        return "Localizar título"
    if status in {"overdue", "divergent"}:
        return "Revisar e cobrar"
    if rule.kind == "distributor_credit":
        return "Conferir crédito na distribuidora"
    return "Conferir vínculo"


@router.get("/work-queue")
def reconciliation_work_queue(
    db: DbSession,
    _: CurrentUser,
    unit: list[str] | None = Query(None),
    company: list[str] | None = Query(None),
    rule_kind: list[str] | None = Query(None),
    reference_month: list[str] | None = Query(None),
    state: list[str] | None = Query(None),
    scope: str = Query("actionable", pattern="^(actionable|waiting|confirmed|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Single operational queue: invoice rows where available, aggregate rows otherwise."""
    units = {value.zfill(3) for value in _filter_values(unit)}
    companies = {value.upper() for value in _filter_values(company)}
    rule_kinds = set(_filter_values(rule_kind))
    reference_months = _filter_months(reference_month)
    states = set(_filter_values(state))
    allowed_states = {"actionable", "waiting", "confirmed"}
    invalid_states = states - allowed_states
    if invalid_states:
        raise HTTPException(
            status_code=422,
            detail=f"Situação inválida: {', '.join(sorted(invalid_states))}.",
        )
    rules = {item.id: item for item in db.scalars(select(BonusRule)).all()}
    reconciliations = db.scalars(
        select(Reconciliation).where(
            Reconciliation.status != "superseded",
        )
    ).all()
    reconciliation_ids = [item.id for item in reconciliations]
    granular_items = db.scalars(
        select(ReconciliationItem).where(
            ReconciliationItem.reconciliation_id.in_(reconciliation_ids) if reconciliation_ids else False,
            ReconciliationItem.item_type.in_(("invoice", "portal_credit_usage")),
        )
    ).all()
    items_by_reconciliation: dict[str, list[ReconciliationItem]] = {}
    for item in granular_items:
        items_by_reconciliation.setdefault(item.reconciliation_id, []).append(item)
    exceptions = db.scalars(
        select(ReconciliationException).where(
            ReconciliationException.reconciliation_id.in_(reconciliation_ids) if reconciliation_ids else False,
            ReconciliationException.status.in_(("open", "in_review")),
        )
    ).all()
    exceptions_by_reconciliation: dict[str, list[ReconciliationException]] = {}
    exceptions_by_item: dict[str, list[ReconciliationException]] = {}
    for item in exceptions:
        exceptions_by_reconciliation.setdefault(item.reconciliation_id, []).append(item)
        if item.item_id:
            exceptions_by_item.setdefault(item.item_id, []).append(item)

    rows: list[dict] = []
    for reconciliation in reconciliations:
        rule = rules.get(reconciliation.rule_id)
        if not rule:
            continue
        if units and reconciliation.unit_code not in units:
            continue
        if companies and rule.company_code not in companies:
            continue
        if rule_kinds and rule.kind not in rule_kinds:
            continue
        if reference_months and reconciliation.reference_month not in reference_months:
            continue
        # A few legacy invoice reconciliations predate granular items. Keep
        # them visible as one aggregate line instead of silently losing them
        # from the operational queue. Unit 004 is deliberately different: its
        # approved proof is the accumulated Ipiranga statement, so only a
        # portal credit explicitly declared by Ipiranga can enter this queue.
        if rule.kind == "invoice_discount":
            source_items = items_by_reconciliation.get(reconciliation.id) or [None]
        elif is_unit_004_portal_credit_rule(rule):
            source_items = items_by_reconciliation.get(reconciliation.id) or []
        else:
            source_items = [None]
        for item in source_items:
            if item and item.review_status == CONTRACTUAL_EXCLUSION_REVIEW_STATUS:
                continue
            details = json.loads(item.details_json or "{}") if item else {}
            try:
                reconciliation_evidence = json.loads(reconciliation.evidence_json or "[]")
            except (TypeError, json.JSONDecodeError):
                reconciliation_evidence = []
            has_management_adjustment = any(
                evidence.get("source") == "MANAGEMENT_ADJUSTMENT"
                for evidence in reconciliation_evidence
                if isinstance(evidence, dict)
            )
            status = item.status if item else reconciliation.status
            expected = Decimal(item.expected_value) if item else Decimal(reconciliation.expected_value)
            observed = Decimal(item.observed_value) if item else Decimal(reconciliation.observed_value) + Decimal(reconciliation.manual_adjustment or 0)
            difference = Decimal(item.difference_value) if item else Decimal(reconciliation.difference_value)
            related_exceptions = (
                exceptions_by_item.get(item.id, []) if item else exceptions_by_reconciliation.get(reconciliation.id, [])
            )
            # A zero expected / zero identified component has no operational
            # decision.  Legacy granular rows can exist below a month that has
            # other valid notes, so filter at item level as well as at the
            # aggregate-reconciliation query above.
            if (
                expected <= Decimal("0.00")
                and observed <= Decimal("0.00")
                and difference == Decimal("0.00")
                and status != "late_payment"
                and not related_exceptions
            ):
                continue
            effective_due_date = _effective_item_due_date(item, reconciliation.due_date, details)
            state = _queue_state(status, effective_due_date)
            severity = min((QUEUE_PRIORITY.get(entry.severity, 3) for entry in related_exceptions), default=None)
            if severity is None:
                severity = 0 if status in {"overdue", "divergent"} else 2 if state == "actionable" else 3 if state == "waiting" else 4
            rows.append(
                {
                    "id": item.id if item else reconciliation.id,
                    "reconciliation_id": reconciliation.id,
                    "item_id": item.id if item else None,
                    "unit_code": reconciliation.unit_code,
                    "company_code": rule.company_code,
                    "rule_kind": rule.kind,
                    "item_type": item.item_type if item else None,
                    "source_date": item.source_date if item and item.source_date else reconciliation.reference_month,
                    "reference_month": reconciliation.reference_month,
                    "document": item.source_document if item else None,
                    "description": item.description if item else f"{rule.kind} • competência {reconciliation.reference_month.strftime('%m/%Y')}",
                    "expected_value": float(money(expected)),
                    "observed_value": float(money(observed)),
                    "difference_value": float(money(difference)),
                    "contractual_value": (
                        float(money(Decimal(str(details.get("contractual_expected_value") or 0))))
                        if status == "late_payment"
                        else None
                    ),
                    "due_date": effective_due_date,
                    "status": status,
                    "state": state,
                    "priority": next((name for name, value in QUEUE_PRIORITY.items() if value == severity), "low"),
                    "reason": _queue_reason(
                        rule, status, details, related_exceptions, has_management_adjustment
                    ),
                    "action_label": _queue_action(
                        rule, state, status, details, related_exceptions, has_management_adjustment
                    ),
                    "open_exception_count": len(related_exceptions),
                    "in_review": any(entry.status == "in_review" for entry in related_exceptions),
                    "confirmation_mode": reconciliation.confirmation_mode,
                }
            )

    summary = {
        "to_treat": sum(item["state"] == "actionable" for item in rows),
        "value_at_risk": float(sum((abs(Decimal(str(item["difference_value"]))) for item in rows if item["state"] == "actionable"), Decimal("0"))),
        "in_review": sum(item["in_review"] for item in rows),
        "waiting": sum(item["state"] == "waiting" for item in rows),
        "confirmed": sum(item["state"] == "confirmed" and item["status"] != "late_payment" for item in rows),
        "late_payment": sum(item["status"] == "late_payment" for item in rows),
        "confirmed_value": float(sum((Decimal(str(item["observed_value"])) for item in rows if item["state"] == "confirmed"), Decimal("0"))),
    }
    if states:
        rows = [item for item in rows if item["state"] in states]
    elif scope != "all":
        rows = [item for item in rows if item["state"] == scope]
    # The queue is a current operational view: newest competence/NF first.
    # Severity still orders items that share the same source date.
    rows.sort(
        key=lambda item: (
            -(item["source_date"] or item["reference_month"] or date.min).toordinal(),
            QUEUE_PRIORITY.get(item["priority"], 4),
            item["due_date"] or date.max,
            -abs(item["difference_value"]),
        )
    )
    total = len(rows)
    selected = rows[(page - 1) * page_size:page * page_size]
    return {
        "items": selected,
        "total": total,
        "page": page,
        "pages": (total + page_size - 1) // page_size,
        "scope": scope,
        "states": sorted(states),
        "summary": summary,
    }


@router.get("/exceptions")
def exception_queue(
    db: DbSession,
    _: CurrentUser,
    unit: str | None = None,
    status: str | None = "open",
    severity: str | None = None,
    exception_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    statement = (
        select(ReconciliationException, Reconciliation, BonusRule, ReconciliationItem)
        .join(Reconciliation, Reconciliation.id == ReconciliationException.reconciliation_id)
        .join(BonusRule, BonusRule.id == Reconciliation.rule_id)
        .outerjoin(ReconciliationItem, ReconciliationItem.id == ReconciliationException.item_id)
        .where(
            ~ReconciliationException.exception_type.in_(("missing_boleto_evidence", "boleto_value_mismatch"))
        )
    )
    if unit:
        statement = statement.where(Reconciliation.unit_code == unit.zfill(3))
    if status:
        statuses = ("open", "in_review") if status == "open" else (status,)
        statement = statement.where(ReconciliationException.status.in_(statuses))
    if severity:
        statement = statement.where(ReconciliationException.severity == severity)
    if exception_type:
        statement = statement.where(ReconciliationException.exception_type == exception_type)
    severity_rank = case(
        (ReconciliationException.severity == "critical", 0),
        (ReconciliationException.severity == "high", 1),
        (ReconciliationException.severity == "medium", 2),
        (ReconciliationException.severity == "low", 3),
        else_=4,
    )
    rows = db.execute(
        statement.order_by(
            severity_rank,
            Reconciliation.due_date,
            Reconciliation.unit_code,
            ReconciliationException.created_at,
        )
    ).all()
    total = len(rows)
    selected = rows[(page - 1) * page_size:page * page_size]
    open_rows = [item for item in rows if item[0].status in {"open", "in_review"}]
    risk_by_item: dict[str, Decimal] = {}
    for exception, reconciliation, _, _ in open_rows:
        key = exception.item_id or reconciliation.id
        risk_by_item[key] = max(
            risk_by_item.get(key, Decimal("0")),
            abs(Decimal(exception.difference_value)),
        )
    return {
        "items": [
            {
                "id": exception.id,
                "reconciliation_id": reconciliation.id,
                "item_id": exception.item_id,
                "unit_code": reconciliation.unit_code,
                "reference_month": reconciliation.reference_month,
                "due_date": _effective_item_due_date(item, reconciliation.due_date),
                "rule_kind": rule.kind,
                "exception_type": exception.exception_type,
                "severity": exception.severity,
                "status": exception.status,
                "title": exception.title,
                "description": exception.description,
                "expected_value": float(exception.expected_value),
                "observed_value": float(exception.observed_value),
                "difference_value": float(exception.difference_value),
                "item_description": item.description if item else None,
                "item_status": item.status if item else None,
                "source_document": item.source_document if item else None,
                "assigned_to": exception.assigned_to,
                "resolution_code": exception.resolution_code,
                "resolution_notes": exception.resolution_notes,
                "created_at": exception.created_at,
                "updated_at": exception.updated_at,
            }
            for exception, reconciliation, rule, item in selected
        ],
        "total": total,
        "page": page,
        "pages": (total + page_size - 1) // page_size,
        "summary": {
            "open": len(open_rows),
            "critical": sum(item[0].severity == "critical" for item in open_rows),
            "high": sum(item[0].severity == "high" for item in open_rows),
            "in_review": sum(item[0].status == "in_review" for item in open_rows),
            "value_at_risk": float(sum(risk_by_item.values(), Decimal("0"))),
            "types": dict(Counter(item[0].exception_type for item in open_rows)),
        },
    }


@router.post("/items/{item_id}/review")
def review_item(item_id: str, payload: ItemReviewInput, db: DbSession, user: AdminUser):
    item = db.get(ReconciliationItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item conciliável não encontrado")
    row = db.get(Reconciliation, item.reconciliation_id)
    if payload.action == "accept" and abs(Decimal(item.difference_value)) > Decimal("0.01"):
        raise HTTPException(
            status_code=409,
            detail="Um item com diferença financeira não pode ser confirmado. Registre o ajuste ou mantenha a cobrança em aberto.",
        )
    item.reviewed_by = user.id
    item.reviewed_at = datetime.now(timezone.utc)
    item.review_notes = f"[{payload.reason_code}] {payload.notes}"
    allocations = db.scalars(
        select(ReconciliationAllocation).where(ReconciliationAllocation.item_id == item.id)
    ).all()
    if payload.action == "accept":
        item.review_status = "accepted"
        item.status = "manual_confirmed"
        for allocation in allocations:
            if allocation.allocated_value > 0:
                allocation.match_status = "accepted"
                allocation.reviewed_by = user.id
                allocation.reviewed_at = item.reviewed_at
    elif payload.action == "reject":
        item.review_status = "rejected"
        item.status = "divergent"
        for allocation in allocations:
            allocation.match_status = "rejected"
            allocation.reviewed_by = user.id
            allocation.reviewed_at = item.reviewed_at
    else:
        item.review_status = "needs_information"
        item.status = "review_required"
        active_request = db.scalar(
            select(ReconciliationInformationRequest)
            .where(
                ReconciliationInformationRequest.item_id == item.id,
                ReconciliationInformationRequest.status == "open",
            )
            .order_by(ReconciliationInformationRequest.requested_at.desc())
        )
        if active_request:
            active_request.reason_code = payload.reason_code
            active_request.request_notes = payload.notes
            active_request.requested_by = user.id
            active_request.requested_at = item.reviewed_at
        else:
            db.add(
                ReconciliationInformationRequest(
                    reconciliation_id=row.id,
                    item_id=item.id,
                    status="open",
                    reason_code=payload.reason_code,
                    request_notes=payload.notes,
                    requested_by=user.id,
                    requested_at=item.reviewed_at,
                )
            )
    exceptions = db.scalars(
        select(ReconciliationException).where(
            ReconciliationException.item_id == item.id,
            ReconciliationException.status.in_(("open", "in_review")),
        )
    ).all()
    for exception in exceptions:
        if payload.action == "accept":
            exception.status = "resolved"
            exception.resolution_code = payload.reason_code
            exception.resolution_notes = payload.notes
            exception.resolved_by = user.id
            exception.resolved_at = item.reviewed_at
        else:
            exception.status = "in_review"
            exception.assigned_to = user.id
            exception.resolution_code = payload.reason_code
            exception.resolution_notes = payload.notes
    recompute_reconciliation_confirmation(db, row)
    if row.confirmation_mode == "manual":
        row.confirmed_by = user.id
    db.flush()
    _record_review(db, row, db.get(BonusRule, row.rule_id), user, f"item_{payload.action}", payload.notes)
    audit(db, user, f"item_{payload.action}", "reconciliation_item", item.id, payload.model_dump())
    db.commit()
    return build_reconciliation_detail(db, row, db.get(BonusRule, row.rule_id))


@router.post("/information-requests/{request_id}/respond")
def respond_information_request(
    request_id: str,
    payload: InformationRequestResponseInput,
    db: DbSession,
    user: AdminUser,
):
    if request_id.startswith("legacy:"):
        item = db.get(ReconciliationItem, request_id.removeprefix("legacy:"))
        if not item or item.review_status != "needs_information":
            raise HTTPException(status_code=404, detail="Solicitação de informação não encontrada")
        request = db.scalar(
            select(ReconciliationInformationRequest)
            .where(
                ReconciliationInformationRequest.item_id == item.id,
                ReconciliationInformationRequest.status == "open",
            )
            .order_by(ReconciliationInformationRequest.requested_at.desc())
        ) or _legacy_information_request(db, item)
    else:
        request = db.get(ReconciliationInformationRequest, request_id)
        if not request:
            raise HTTPException(status_code=404, detail="Solicitação de informação não encontrada")
        item = db.get(ReconciliationItem, request.item_id)
    if request.status != "open":
        raise HTTPException(status_code=409, detail="Esta solicitação já foi encerrada")
    if not item:
        raise HTTPException(status_code=404, detail="Item conciliável não encontrado")

    row = db.get(Reconciliation, request.reconciliation_id)
    now = datetime.now(timezone.utc)
    request.status = "closed"
    request.response_notes = payload.notes
    request.responded_by = user.id
    request.responded_at = now
    request.closed_by = user.id
    request.closed_at = now

    # Encerrar a conversa interna não confirma nenhum valor nem resolve a cobrança.
    item.review_status = "pending"
    item.reviewed_by = None
    item.reviewed_at = None
    item.review_notes = None
    for exception in db.scalars(
        select(ReconciliationException).where(
            ReconciliationException.item_id == item.id,
            ReconciliationException.status == "in_review",
        )
    ).all():
        exception.status = "open"
        exception.assigned_to = None
        exception.resolution_code = None
        exception.resolution_notes = None

    recompute_reconciliation_confirmation(db, row)
    db.flush()
    rule = db.get(BonusRule, row.rule_id)
    _record_review(db, row, rule, user, "item_information_response", payload.notes)
    audit(
        db,
        user,
        "item_information_response",
        "reconciliation_information_request",
        request.id,
        {"item_id": item.id, "notes": payload.notes},
    )
    db.commit()
    return build_reconciliation_detail(db, row, rule)


@router.post("/exceptions/{exception_id}/action")
def exception_action(exception_id: str, payload: ExceptionActionInput, db: DbSession, user: AdminUser):
    exception = db.get(ReconciliationException, exception_id)
    if not exception:
        raise HTTPException(status_code=404, detail="Exceção não encontrada")
    now = datetime.now(timezone.utc)
    if payload.action == "start_review":
        exception.status = "in_review"
        exception.assigned_to = payload.assigned_to or user.id
        exception.resolution_notes = payload.notes
    elif payload.action == "resolve":
        if not payload.resolution_code or not payload.notes:
            raise HTTPException(status_code=422, detail="Informe o motivo e a conclusão da exceção")
        exception.status = "resolved"
        exception.resolution_code = payload.resolution_code
        exception.resolution_notes = payload.notes
        exception.resolved_by = user.id
        exception.resolved_at = now
    else:
        exception.status = "open"
        exception.resolution_code = None
        exception.resolution_notes = payload.notes
        exception.resolved_by = None
        exception.resolved_at = None
    audit(db, user, f"exception_{payload.action}", "reconciliation_exception", exception.id, payload.model_dump())
    db.commit()
    return {"id": exception.id, "status": exception.status}


@router.post("/{reconciliation_id}/boleto-evidence")
async def upload_boleto_evidence(
    reconciliation_id: str,
    db: DbSession,
    actor: AdminUser,
    file: UploadFile = File(...),
):
    row = db.get(Reconciliation, reconciliation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Conciliação não encontrada")
    rule = db.get(BonusRule, row.rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra de bonificação não encontrada")
    try:
        uploaded = await read_validated_upload(
            file,
            max_bytes=MAX_BOLETO_BYTES,
            signatures={".pdf": (b"%PDF-",)},
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    try:
        evidence, already_imported = import_boleto_evidence(
            db,
            row,
            rule,
            filename=uploaded.filename,
            content_type=file.content_type,
            content=uploaded.content,
            uploaded_by=actor.id,
        )
    except BoletoEvidenceError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(
        db,
        actor,
        "invoice_boleto_evidence_upload",
        "invoice_boleto_evidence",
        evidence.id,
        {
            "filename": evidence.original_filename,
            "sha256": evidence.content_sha256,
            "match_status": evidence.match_status,
            "purchase_entry_id": evidence.purchase_entry_id,
            "discount_value": str(evidence.discount_value),
            "already_imported": already_imported,
        },
    )
    rebuild_reconciliations(db)
    refreshed = db.get(Reconciliation, reconciliation_id)
    return {
        "evidence": _boleto_payload(evidence, already_imported),
        "detail": build_reconciliation_detail(db, refreshed, db.get(BonusRule, refreshed.rule_id)),
    }


@router.get("/boleto-evidence/{evidence_id}/file")
def boleto_evidence_file(evidence_id: str, db: DbSession, _: CurrentUser):
    row = db.get(InvoiceBoletoEvidence, evidence_id)
    if not row:
        raise HTTPException(status_code=404, detail="Boleto não encontrado")
    filename = quote(row.original_filename)
    return Response(
        content=row.source_file,
        media_type=row.content_type or "application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{reconciliation_id}/detail")
def reconciliation_detail(reconciliation_id: str, db: DbSession, _: CurrentUser):
    row = db.get(Reconciliation, reconciliation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Conciliação não encontrada")
    rule = db.get(BonusRule, row.rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra de bonificação não encontrada")
    return build_reconciliation_detail(db, row, rule)


@router.get("/{reconciliation_id}/reviews/{review_id}")
def review_snapshot(reconciliation_id: str, review_id: str, db: DbSession, _: CurrentUser):
    review = db.scalar(
        select(ReconciliationReview).where(
            ReconciliationReview.id == review_id,
            ReconciliationReview.reconciliation_id == reconciliation_id,
        )
    )
    if not review:
        raise HTTPException(status_code=404, detail="Revisão auditada não encontrada")
    return json.loads(review.snapshot_json)


@router.post("/{reconciliation_id}/confirm")
def confirm(reconciliation_id: str, payload: ConfirmInput, db: DbSession, user: AdminUser):
    row = db.get(Reconciliation, reconciliation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Conciliação não encontrada")
    items = db.scalars(
        select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id)
    ).all()
    if items:
        unresolved = [item for item in items if item.expected_value > 0 and item.status not in {"auto_confirmed", "manual_confirmed"}]
        open_exceptions = db.scalar(
            select(func.count()).select_from(ReconciliationException).where(
                ReconciliationException.reconciliation_id == row.id,
                ReconciliationException.status.in_(("open", "in_review")),
            )
        ) or 0
        if unresolved or open_exceptions:
            raise HTTPException(
                status_code=409,
                detail=f"Revise os itens individualmente antes de confirmar: {len(unresolved)} item(ns) e {open_exceptions} exceção(ões) em aberto.",
            )
        if abs(Decimal(row.difference_value)) > Decimal("0.01"):
            raise HTTPException(status_code=409, detail="A competência ainda possui diferença financeira em aberto")
    row.status = "confirmed"
    row.confirmation_mode = "manual"
    row.auto_confirmed_at = None
    row.confirmed_by = user.id
    row.confirmed_at = datetime.now(timezone.utc)
    if payload.notes:
        row.notes = payload.notes
    db.flush()
    _record_review(db, row, db.get(BonusRule, row.rule_id), user, "confirm", payload.notes)
    audit(db, user, "confirm", "reconciliation", row.id, {"notes": payload.notes})
    db.commit()
    return _payload(row, db.get(BonusRule, row.rule_id), db)


@router.post("/{reconciliation_id}/adjust")
def adjust(reconciliation_id: str, payload: AdjustInput, db: DbSession, user: AdminUser):
    row = db.get(Reconciliation, reconciliation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Conciliação não encontrada")
    adjustment = ManualAdjustment(
        reconciliation_id=row.id,
        amount=money(payload.amount),
        reason=payload.reason,
        created_by=user.id,
    )
    db.add(adjustment)
    row.manual_adjustment = money(Decimal(row.manual_adjustment or 0) + payload.amount)
    observed = Decimal(row.observed_value) + Decimal(row.manual_adjustment)
    row.difference_value = money(Decimal(row.expected_value) - observed)
    row.status = reconciliation_status(Decimal(row.expected_value), observed, row.due_date, datetime.now().date())
    row.notes = payload.reason
    recompute_reconciliation_confirmation(db, row)
    db.flush()
    _record_review(db, row, db.get(BonusRule, row.rule_id), user, "adjust", payload.reason)
    audit(db, user, "adjust", "reconciliation", row.id, {"amount": str(payload.amount), "reason": payload.reason})
    db.commit()
    return _payload(row, db.get(BonusRule, row.rule_id), db)
