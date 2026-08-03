from __future__ import annotations

from datetime import date
from typing import Iterable

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from app.dependencies import CurrentUser, DbSession
from app.models import PayableDocument, PayableMovement, Purchase
from app.services.rules import add_months, month_start


router = APIRouter(prefix="/purchases", tags=["compras"])


# Keep this whitelist close to the HTTP boundary.  Besides avoiding arbitrary
# SQL expressions, it gives the interface stable sort keys even when a column
# is renamed internally later on.
PURCHASE_SORT_FIELDS = {
    "date": Purchase.purchase_date,
    "purchase_date": Purchase.purchase_date,
    "invoice_date": Purchase.invoice_issue_date,
    "invoice_number": Purchase.invoice_number,
    "unit": Purchase.unit_code,
    "unit_code": Purchase.unit_code,
    "company": Purchase.mapped_company_code,
    "company_code": Purchase.mapped_company_code,
    "supplier": Purchase.supplier_name,
    "supplier_name": Purchase.supplier_name,
    "liters": Purchase.total_liters,
    "total_liters": Purchase.total_liters,
    "s10_liters": Purchase.s10_liters,
    "gross_value": Purchase.gross_value,
    "net_value": Purchase.net_value,
}


def _values(value: str | Iterable[str] | None) -> list[str]:
    """Accept repeated query parameters and comma-separated values.

    The UI can send either ``unit=001&unit=002`` or ``unit=001,002``.  The
    second form is useful for bookmarked URLs; both forms intentionally have
    identical semantics.
    """
    raw = [value] if isinstance(value, str) else list(value or [])
    return [part.strip() for item in raw for part in item.split(",") if part.strip()]


def _purchase_payload(row: Purchase) -> dict:
    return {
        "erp_entry_id": row.erp_entry_id,
        "unit_code": row.unit_code,
        "purchase_date": row.purchase_date,
        "invoice_issue_date": row.invoice_issue_date,
        "invoice_number": row.invoice_number,
        "invoice_series": row.invoice_series,
        "access_key": row.access_key,
        "supplier_person_id": row.supplier_person_id,
        "supplier_name": row.supplier_name,
        "supplier_cnpj": row.supplier_cnpj,
        "company_code": row.mapped_company_code,
        "total_liters": float(row.total_liters),
        "s10_liters": float(row.s10_liters),
        "gross_value": float(row.gross_value),
        "net_value": float(row.net_value),
        "mapped": row.mapped_company_code is not None,
    }


def _movement_payload(row: PayableMovement) -> dict:
    return {
        "erp_key": row.erp_key,
        "movement_type": row.movement_type,
        "movement_date": row.movement_date,
        "reference_date": row.reference_date,
        "amount": float(row.amount),
        "payment_sequence": row.payment_sequence,
        "reversal_sequence": row.reversal_sequence,
        "financial_launch_id": row.financial_launch_id,
        "financial_sequence": row.financial_sequence,
        "batch_id": row.batch_id,
        "payment_method": row.payment_method,
        "operation_id": row.operation_id,
        "center_code": row.center_code,
        "notes": row.notes,
    }


def _payable_payload(row: PayableDocument, movements: list[PayableMovement]) -> dict:
    return {
        "id": row.id,
        "person_id": row.person_id,
        "title_type": row.title_type,
        "document_id": row.document_id,
        "sequence": row.sequence,
        "invoice_number": row.invoice_number,
        "document_value": float(row.document_value),
        "other_discount": float(row.other_discount),
        "issue_date": row.issue_date,
        "due_date": row.due_date,
        "payment_date": row.payment_date,
        "balance": float(row.balance),
        "movements": [_movement_payload(movement) for movement in movements],
    }


def _month_filters(reference_months: list[date]):
    ranges = [
        and_(Purchase.purchase_date >= month_start(value), Purchase.purchase_date < add_months(month_start(value), 1))
        for value in reference_months
    ]
    return or_(*ranges) if ranges else None


def _reference_months(value: str | Iterable[str] | None) -> list[date]:
    months: list[date] = []
    for raw in _values(value):
        try:
            months.append(month_start(date.fromisoformat(f"{raw}-01" if len(raw) == 7 else raw)))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Competência deve usar AAAA-MM ou AAAA-MM-DD.",
            ) from exc
    return months


def _purchase_filters(
    unit: str | Iterable[str] | None,
    company: str | Iterable[str] | None,
    reference_month: str | Iterable[str] | None,
    search: str | None,
) -> list:
    filters = []
    units = [value.zfill(3) for value in _values(unit)]
    if units:
        filters.append(Purchase.unit_code.in_(units))
    companies = [value.upper() for value in _values(company)]
    if companies:
        filters.append(Purchase.mapped_company_code.in_(companies))
    reference_months = _reference_months(reference_month)
    if reference_months:
        month_filter = _month_filters(reference_months)
        if month_filter is not None:
            filters.append(month_filter)
    if search and search.strip():
        like = f"%{search.strip()}%"
        filters.append(
            or_(
                Purchase.invoice_number.ilike(like),
                Purchase.supplier_name.ilike(like),
                Purchase.supplier_cnpj.ilike(like),
                Purchase.access_key.ilike(like),
            )
        )
    return filters


def _purchase_order(sort_by: str, sort_dir: str):
    column = PURCHASE_SORT_FIELDS.get(sort_by)
    if column is None:
        allowed = ", ".join(sorted(PURCHASE_SORT_FIELDS))
        raise HTTPException(status_code=422, detail=f"Campo de ordenação inválido. Use: {allowed}.")
    direction = sort_dir.lower()
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="Direção de ordenação deve ser asc ou desc.")
    ordered = column.asc() if direction == "asc" else column.desc()
    # An immutable ERP entry is the deterministic tie-breaker for pagination.
    tie_breaker = Purchase.erp_entry_id.asc() if direction == "asc" else Purchase.erp_entry_id.desc()
    return ordered, tie_breaker


@router.get("")
def purchases(
    db: DbSession,
    _: CurrentUser,
    unit: list[str] | None = Query(None),
    company: list[str] | None = Query(None),
    reference_month: list[str] | None = Query(None),
    search: str | None = None,
    sort_by: str = "purchase_date",
    sort_dir: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """List purchase invoices with multi-select filters and server-side sort.

    ``unit``, ``company`` and ``reference_month`` may be sent more than once.
    Single-value requests keep their original behaviour.
    """
    filters = _purchase_filters(unit, company, reference_month, search)
    total = db.scalar(select(func.count()).select_from(Purchase).where(*filters)) or 0
    order = _purchase_order(sort_by, sort_dir)
    rows = db.scalars(
        select(Purchase)
        .where(*filters)
        .order_by(*order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_purchase_payload(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
        "sort_by": sort_by,
        "sort_dir": sort_dir.lower(),
    }


@router.get("/{erp_entry_id}")
def purchase_detail(erp_entry_id: int, db: DbSession, _: CurrentUser):
    """Return the full local, read-only snapshot for one fuel purchase NF-e."""
    purchase = db.scalar(
        select(Purchase)
        .where(Purchase.erp_entry_id == erp_entry_id)
        .options(selectinload(Purchase.items))
    )
    if not purchase:
        raise HTTPException(status_code=404, detail="Nota de compra não encontrada")

    payables = db.scalars(
        select(PayableDocument)
        .where(PayableDocument.erp_entry_id == purchase.erp_entry_id)
        .order_by(PayableDocument.due_date, PayableDocument.id)
    ).all()
    movements_by_title: dict[tuple[int, str, str, str, str], list[PayableMovement]] = {}
    if payables:
        title_predicates = [
            and_(
                PayableMovement.unit_code == row.unit_code,
                PayableMovement.person_id == row.person_id,
                PayableMovement.title_type == row.title_type,
                PayableMovement.document_id == row.document_id,
                PayableMovement.document_sequence == row.sequence,
            )
            for row in payables
        ]
        movements = db.scalars(
            select(PayableMovement)
            .where(or_(*title_predicates))
            .order_by(PayableMovement.movement_date, PayableMovement.erp_key)
        ).all()
        for movement in movements:
            key = (
                movement.person_id,
                movement.unit_code,
                movement.title_type,
                movement.document_id,
                movement.document_sequence,
            )
            movements_by_title.setdefault(key, []).append(movement)

    payload = _purchase_payload(purchase)
    payload.update(
        {
            "erp_updated_at": purchase.erp_updated_at,
            "synced_at": purchase.synced_at,
            "items": [
                {
                    "id": item.id,
                    "sequence": item.sequence,
                    "item_code": item.item_code,
                    "description": item.description,
                    "unit": item.unit,
                    "quantity": float(item.quantity),
                    "unit_value": float(item.unit_value),
                    "total_value": float(item.total_value),
                }
                for item in sorted(purchase.items, key=lambda item: (item.sequence, item.id))
            ],
            "payables": [
                _payable_payload(
                    row,
                    movements_by_title.get(
                        (row.person_id, row.unit_code, row.title_type, row.document_id, row.sequence), []
                    ),
                )
                for row in payables
            ],
        }
    )
    return payload
