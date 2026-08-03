from __future__ import annotations

import json
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Annotated, Iterable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import selectinload

from app.dependencies import AdminUser, CurrentUser, DbSession
from app.models import (
    FreightCte,
    FreightCteInvoice,
    FreightRate,
    FreightReconciliation,
    FreightReview,
    PayableDocument,
    Purchase,
)
from app.services.audit import audit
from app.services.freight_reconciliation import rebuild_freight_reconciliations


router = APIRouter(prefix="/freights", tags=["conciliação de fretes"])


class FreightReviewInput(BaseModel):
    action: str = Field(pattern="^(confirm|contest|justify|reopen)$")
    notes: str = Field(min_length=5, max_length=2000)
    candidate_purchase_entry_id: int | None = None
    fingerprint: str = Field(min_length=64, max_length=64)


def _month_bounds(competence: str | None) -> tuple[date, date] | None:
    if not competence:
        return None
    try:
        year, month = (int(part) for part in competence.split("-", 1))
        start = date(year, month, 1)
        end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        return start, end
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Competência deve usar o formato AAAA-MM")


def _values(value: str | Iterable[str] | None) -> list[str]:
    """Normalize both repeated and comma-separated multi-select parameters."""
    raw = [value] if isinstance(value, str) else list(value or [])
    return [part.strip() for item in raw for part in item.split(",") if part.strip()]


def _statement(
    competence: str | Iterable[str] | None,
    unit: str | Iterable[str] | None,
    carrier: str | Iterable[str] | None,
    status: str | Iterable[str] | None,
    search: str | None,
):
    statement = select(FreightReconciliation).join(FreightCte).where(
        FreightCte.source_active.is_(True),
        FreightCte.is_canceled.is_(False),
    )
    competence_ranges = [_month_bounds(value) for value in _values(competence)]
    if competence_ranges:
        statement = statement.where(
            or_(
                *[
                    and_(
                        FreightReconciliation.reference_date >= bounds[0],
                        FreightReconciliation.reference_date < bounds[1],
                    )
                    for bounds in competence_ranges
                    if bounds is not None
                ]
            )
        )
    units = [value.zfill(3) for value in _values(unit)]
    if units:
        statement = statement.where(FreightCte.unit_code.in_(units))
    carriers = ["".join(character for character in value if character.isdigit()) for value in _values(carrier)]
    carriers = [value for value in carriers if value]
    if carriers:
        statement = statement.where(FreightCte.carrier_cnpj.in_(carriers))
    statuses = _values(status)
    if statuses:
        statement = statement.where(FreightReconciliation.primary_status.in_(statuses))
    if search:
        term = search.strip()
        purchase_join = (
            (Purchase.erp_entry_id == FreightCteInvoice.resolved_purchase_entry_id)
            | (Purchase.erp_entry_id == FreightCteInvoice.candidate_purchase_entry_id)
        )
        if term.isdigit() and len(term) <= 10:
            matching_ctes = select(FreightCteInvoice.erp_cte_id).outerjoin(
                Purchase, purchase_join,
            ).where(Purchase.invoice_number == term)
            statement = statement.where(
                (FreightCte.cte_number == int(term))
                | FreightCte.erp_cte_id.in_(matching_ctes)
            )
        else:
            matching_ctes = select(FreightCteInvoice.erp_cte_id).outerjoin(
                Purchase, purchase_join,
            ).where(
                FreightCteInvoice.reference_access_key.contains(term)
                | FreightCteInvoice.reference_invoice_number.contains(term)
                | Purchase.invoice_number.contains(term)
            )
            statement = statement.where(
                cast(FreightCte.cte_number, String).contains(term)
                | FreightCte.access_key.contains(term)
                | FreightCte.erp_cte_id.in_(matching_ctes)
            )
    return statement


def _number(value: Decimal | None) -> float:
    return float(value or 0)


def _list_payload(row: FreightReconciliation) -> dict:
    cte = row.cte
    rate_available = row.rate_id is not None
    # A tariff alone is not enough to compare freight.  Without a proven NF-e
    # volume, zero is not an expected volume and charged - zero is not a real
    # difference.  This explicitly prevents the confusing 002-style rows.
    comparison_available = rate_available and Decimal(str(row.matched_liters or 0)) > 0
    return {
        "id": row.id,
        "erp_cte_id": cte.erp_cte_id,
        "source_kind": cte.source_kind,
        "source_entry_id": cte.source_entry_id,
        "cte_number": cte.cte_number,
        "series": cte.series,
        "issue_date": cte.issue_date,
        "reference_date": row.reference_date,
        "unit_code": cte.unit_code,
        "carrier_cnpj": cte.carrier_cnpj,
        "carrier_name": cte.carrier_name,
        "sender_name": cte.sender_name,
        "matched_liters": _number(row.matched_liters),
        "expected_value": _number(row.expected_value) if comparison_available else 0,
        "charged_value": _number(row.charged_value),
        "payable_value": _number(row.payable_value),
        "paid_value": _number(row.paid_value),
        "difference_value": _number(row.difference_value) if comparison_available else 0,
        # Kept for the existing UI. It now means there is a usable price
        # comparison, not merely that some tariff row exists in the database.
        "has_rate": comparison_available,
        "rate_available": rate_available,
        "comparison_available": comparison_available,
        "comparison_reason": (
            None
            if comparison_available
            else "Sem litros comprovados por NF-e; não há base para comparar o valor do frete."
            if rate_available
            else "Sem tarifa cadastrada para esta transportadora, origem e competência."
        ),
        "primary_status": row.primary_status,
        "review_status": row.review_status,
        "severity": row.severity,
        "issues_count": len(json.loads(row.issues_json or "[]")),
        "fingerprint": row.fingerprint,
    }


def _purchase_payload(purchase: Purchase | None) -> dict | None:
    if not purchase:
        return None
    return {
        "erp_entry_id": purchase.erp_entry_id,
        "invoice_number": purchase.invoice_number,
        "invoice_series": purchase.invoice_series,
        "access_key": purchase.access_key,
        "unit_code": purchase.unit_code,
        "issue_date": purchase.invoice_issue_date or purchase.purchase_date,
        "supplier_name": purchase.supplier_name,
        "supplier_cnpj": purchase.supplier_cnpj,
        "total_liters": _number(purchase.total_liters),
        "gross_value": _number(purchase.gross_value),
        "items": [
            {
                "item_code": item.item_code,
                "description": item.description,
                "unit": item.unit,
                "quantity": _number(item.quantity),
                "unit_value": _number(item.unit_value),
                "total_value": _number(item.total_value),
            }
            for item in purchase.items
        ],
    }


def _detail_payload(db: DbSession, row: FreightReconciliation) -> dict:
    cte = row.cte
    purchase_ids = {
        purchase_id
        for reference in cte.invoices
        for purchase_id in (reference.resolved_purchase_entry_id, reference.candidate_purchase_entry_id)
        if purchase_id is not None
    }
    purchases = {
        purchase.erp_entry_id: purchase
        for purchase in db.scalars(
            select(Purchase).where(Purchase.erp_entry_id.in_(purchase_ids)).options(selectinload(Purchase.items))
        ).all()
    } if purchase_ids else {}
    rate = db.get(FreightRate, row.rate_id) if row.rate_id else None
    payable_link = (
        PayableDocument.erp_entry_id == cte.source_entry_id
        if cte.source_kind == "purchase_entry"
        else PayableDocument.erp_cte_id == cte.erp_cte_id
    )
    payable_rows = db.scalars(
        select(PayableDocument)
        .where(payable_link)
        .order_by(PayableDocument.due_date, PayableDocument.id)
    ).all()
    reviews = db.scalars(
        select(FreightReview)
        .where(FreightReview.reconciliation_id == row.id)
        .order_by(FreightReview.created_at.desc())
    ).all()
    payload = _list_payload(row)
    payload.update(
        {
            "cte": {
                "source_kind": cte.source_kind,
                "source_entry_id": cte.source_entry_id,
                "access_key": cte.access_key,
                "purpose": cte.purpose,
                "source_status": cte.source_status,
                "is_canceled": cte.is_canceled,
                "destination_cnpj": cte.destination_cnpj,
                "sender_cnpj": cte.sender_cnpj,
                "cargo_liters": _number(cte.cargo_liters),
                "cargo_value": _number(cte.cargo_value),
                "charged_service_value": _number(cte.charged_service_value),
                "charged_net_value": _number(cte.charged_net_value),
            },
            "invoices": [
                {
                    "id": reference.id,
                    "sequence": reference.sequence,
                    "reference_access_key": reference.reference_access_key,
                    "reference_invoice_number": reference.reference_invoice_number,
                    "reference_issue_date": reference.reference_issue_date,
                    "resolution_source": reference.resolution_source,
                    "match_status": reference.match_status,
                    "match_reason": reference.match_reason,
                    "resolved": _purchase_payload(purchases.get(reference.resolved_purchase_entry_id)),
                    "candidate": _purchase_payload(purchases.get(reference.candidate_purchase_entry_id)),
                }
                for reference in sorted(cte.invoices, key=lambda item: item.sequence)
            ],
            "rate": (
                {
                    "id": rate.id,
                    "rate_per_liter": _number(rate.rate_per_liter),
                    "effective_from": rate.effective_from,
                    "effective_to": rate.effective_to,
                    "scope": {
                        "carrier_cnpj": rate.carrier_cnpj,
                        "origin_cnpj": rate.origin_cnpj,
                        "unit_code": rate.unit_code,
                    },
                }
                if rate else None
            ),
            "payables": [
                {
                    "id": payable.id,
                    "document_id": payable.document_id,
                    "sequence": payable.sequence,
                    "document_value": _number(payable.document_value),
                    "balance": _number(payable.balance),
                    "issue_date": payable.issue_date,
                    "due_date": payable.due_date,
                    "payment_date": payable.payment_date,
                }
                for payable in payable_rows
            ],
            "issues": json.loads(row.issues_json or "[]"),
            "algorithm_version": row.algorithm_version,
            "reviews": [
                {
                    "id": review.id,
                    "action": review.action,
                    "notes": review.notes,
                    "candidate_purchase_entry_id": review.candidate_purchase_entry_id,
                    "fingerprint": review.fingerprint,
                    "created_by": review.created_by,
                    "created_at": review.created_at,
                }
                for review in reviews
            ],
        }
    )
    return payload


@router.get("/summary")
def freight_summary(
    db: DbSession,
    _: CurrentUser,
    competence: Annotated[list[str] | None, Query()] = None,
    unit: Annotated[list[str] | None, Query()] = None,
    carrier: Annotated[list[str] | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    search: str | None = None,
):
    filtered = _statement(competence, unit, carrier, status, search).subquery()
    comparison_available = and_(
        filtered.c.rate_id.is_not(None),
        filtered.c.matched_liters > 0,
    )
    totals = db.execute(
        select(
            func.count().label("total_ctes"),
            func.coalesce(func.sum(filtered.c.matched_liters), 0).label("liters"),
            func.coalesce(
                func.sum(case((comparison_available, filtered.c.expected_value), else_=0)), 0
            ).label("expected"),
            func.coalesce(func.sum(filtered.c.charged_value), 0).label("charged"),
            func.coalesce(
                func.sum(case((comparison_available, filtered.c.difference_value), else_=0)), 0
            ).label("difference"),
            func.coalesce(func.sum(case((comparison_available, 1), else_=0)), 0).label("priced_ctes"),
            func.count(filtered.c.rate_id).label("rate_available_ctes"),
        ).select_from(filtered)
    ).one()
    statuses = Counter(
        {
            primary_status: count
            for primary_status, count in db.execute(
                select(filtered.c.primary_status, func.count().label("count"))
                .select_from(filtered)
                .group_by(filtered.c.primary_status)
            ).all()
        }
    )
    return {
        "total_ctes": totals.total_ctes,
        "liters": _number(totals.liters),
        "expected": _number(totals.expected),
        "charged": _number(totals.charged),
        "difference": _number(totals.difference),
        "priced_ctes": totals.priced_ctes,
        "rate_available_ctes": totals.rate_available_ctes,
        "unpriced_ctes": totals.total_ctes - totals.rate_available_ctes,
        "uncomparable_ctes": totals.rate_available_ctes - totals.priced_ctes,
        "correct": statuses.get("correct", 0),
        "pending": sum(count for key, count in statuses.items() if key != "correct"),
        "canceled": 0,
        "status_counts": dict(statuses),
    }


FREIGHT_CARD_DETAILS = {
    "liters": (
        "Litros comprovados",
        "NF-es de combustível vinculadas de forma comprovada aos CT-es filtrados.",
        "matched_liters",
    ),
    "expected": (
        "Frete esperado",
        "Somente CT-es com tarifa vigente e litros comprovados por NF-e.",
        "expected_value",
    ),
    "charged": (
        "Frete cobrado",
        "Valor a receber informado nos CT-es filtrados.",
        "charged_value",
    ),
    "difference": (
        "Diferença de frete",
        "Cobrado menos esperado, apenas onde existe tarifa e volume comprovado.",
        "difference_value",
    ),
    "correct": (
        "CT-es corretos",
        "Documentos, tarifa, cobrança e título consistentes na conferência atual.",
        None,
    ),
    "pending": (
        "Pendências de frete",
        "CT-es que ainda exigem evidência documental, tarifa ou conferência financeira.",
        None,
    ),
}


def _comparison_expression():
    return and_(FreightReconciliation.rate_id.is_not(None), FreightReconciliation.matched_liters > 0)


def _card_statement(statement, metric: str):
    if metric in {"expected", "difference"}:
        return statement.where(_comparison_expression())
    if metric == "liters":
        return statement.where(FreightReconciliation.matched_liters > 0)
    if metric == "correct":
        return statement.where(FreightReconciliation.primary_status == "correct")
    if metric == "pending":
        return statement.where(FreightReconciliation.primary_status != "correct")
    return statement


@router.get("/card-details")
def freight_card_details(
    db: DbSession,
    _: CurrentUser,
    metric: str = Query(...),
    competence: Annotated[list[str] | None, Query()] = None,
    unit: Annotated[list[str] | None, Query()] = None,
    carrier: Annotated[list[str] | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Drill down one freight KPI without losing the applied filter context."""
    definition = FREIGHT_CARD_DETAILS.get(metric)
    if not definition:
        raise HTTPException(
            status_code=422,
            detail=f"Indicador inválido. Use: {', '.join(FREIGHT_CARD_DETAILS)}.",
        )
    statement = _card_statement(_statement(competence, unit, carrier, status, search), metric)
    filtered = statement.subquery()
    total = db.scalar(select(func.count()).select_from(filtered)) or 0
    value_column = definition[2]
    total_value = (
        db.scalar(
            select(func.coalesce(func.sum(getattr(filtered.c, value_column)), 0)).select_from(filtered)
        )
        if value_column
        else total
    )
    rows = db.scalars(
        statement.options(selectinload(FreightReconciliation.cte))
        .order_by(FreightReconciliation.reference_date.desc(), FreightCte.cte_number.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "metric": metric,
        "title": definition[0],
        "description": definition[1],
        "total": total,
        "total_value": _number(total_value) if value_column else total,
        "items": [_list_payload(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("")
def list_freights(
    db: DbSession,
    _: CurrentUser,
    competence: Annotated[list[str] | None, Query()] = None,
    unit: Annotated[list[str] | None, Query()] = None,
    carrier: Annotated[list[str] | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    filtered_statement = _statement(competence, unit, carrier, status, search)
    total = db.scalar(select(func.count()).select_from(filtered_statement.subquery())) or 0
    statement = filtered_statement.options(
        selectinload(FreightReconciliation.cte)
    )
    rows = db.scalars(
        statement.order_by(FreightReconciliation.reference_date.desc(), FreightCte.cte_number.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_list_payload(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/{reconciliation_id}")
def freight_detail(reconciliation_id: str, db: DbSession, _: CurrentUser):
    row = db.scalar(
        select(FreightReconciliation)
        .where(FreightReconciliation.id == reconciliation_id)
        .options(selectinload(FreightReconciliation.cte).selectinload(FreightCte.invoices))
    )
    if not row:
        raise HTTPException(status_code=404, detail="Conciliação de frete não encontrada")
    return _detail_payload(db, row)


@router.post("/{reconciliation_id}/review")
def review_freight(reconciliation_id: str, payload: FreightReviewInput, db: DbSession, actor: AdminUser):
    row = db.scalar(
        select(FreightReconciliation)
        .where(FreightReconciliation.id == reconciliation_id)
        .options(selectinload(FreightReconciliation.cte).selectinload(FreightCte.invoices))
    )
    if not row:
        raise HTTPException(status_code=404, detail="Conciliação de frete não encontrada")
    if row.fingerprint != payload.fingerprint:
        raise HTTPException(status_code=409, detail="Os dados mudaram desde a abertura. Recarregue antes de decidir.")
    snapshot = _detail_payload(db, row)
    if payload.action == "confirm":
        if payload.candidate_purchase_entry_id is None:
            raise HTTPException(status_code=400, detail="Selecione a NF-e candidata a confirmar")
        reference = next(
            (
                item for item in row.cte.invoices
                if item.candidate_purchase_entry_id == payload.candidate_purchase_entry_id
            ),
            None,
        )
        if not reference:
            raise HTTPException(status_code=409, detail="A NF-e não é mais uma candidata válida")
        reference.resolved_purchase_entry_id = payload.candidate_purchase_entry_id
        reference.candidate_purchase_entry_id = None
        reference.resolution_source = "manual"
        reference.match_status = "matched"
        reference.match_reason = "Vínculo confirmado por revisão administrativa."
        db.flush()
        rebuild_freight_reconciliations(db)
        row.review_status = "resolved"
    elif payload.action == "contest":
        row.review_status = "disputed"
    elif payload.action == "justify":
        row.review_status = "resolved"
    else:
        row.review_status = "pending"
    review = FreightReview(
        reconciliation_id=row.id,
        action=payload.action,
        notes=payload.notes,
        candidate_purchase_entry_id=payload.candidate_purchase_entry_id,
        fingerprint=payload.fingerprint,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, default=str),
        created_by=actor.id,
    )
    db.add(review)
    audit(
        db,
        actor,
        f"freight_{payload.action}",
        "freight_reconciliation",
        row.id,
        {"notes": payload.notes, "candidate_purchase_entry_id": payload.candidate_purchase_entry_id},
    )
    db.commit()
    return _detail_payload(db, row)
