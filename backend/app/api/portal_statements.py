from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.dependencies import AdminUser, CurrentUser, DbSession
from app.models import (
    BonusRule,
    PortalBonusEvent,
    PortalBonusEventSource,
    PortalBonusMatch,
    PortalStatementImport,
    Reconciliation,
    User,
)
from app.services.audit import audit
from app.services.ipiranga_portal import (
    CONTRACT_PARCELS_REPORT_CATEGORY,
    CREDIT_REPORT_CATEGORY,
    POSTPAID_CATEGORY,
    PortalStatementError,
    MAX_PORTAL_BYTES,
    SUPPLEMENTAL_CATEGORY_LABELS,
    supplemental_classification_label,
    supplemental_review_recommendation,
    build_ipiranga_cycle_ledger,
    build_upfront_ledger,
    import_ipiranga_statement,
    match_ipiranga_events,
)
from app.services.reconciliation import rebuild_reconciliations
from app.services.rules import money
from app.services.uploads import UploadValidationError, read_validated_upload


router = APIRouter(prefix="/portal-statements", tags=["extratos de companhias"])


SUPPLEMENTAL_CLASSIFICATION_CHOICES = {
    "postpaid_regularization",
    "upfront",
    "price_difference",
    "commercial_credit",
    "other",
}


class PortalSupplementalClassificationIn(BaseModel):
    classification: str | None = None
    notes: str | None = None


def _import_category_label(category: str | None) -> str:
    if category == CONTRACT_PARCELS_REPORT_CATEGORY:
        return "Parcelas postecipadas do contrato (.xlsx)"
    if category == CREDIT_REPORT_CATEGORY:
        return "Relatorio de creditos (.xlsx)"
    if category == POSTPAID_CATEGORY:
        return "Extrato postecipado (.xls)"
    if category in SUPPLEMENTAL_CATEGORY_LABELS:
        return SUPPLEMENTAL_CATEGORY_LABELS[category]
    return category or "Arquivo Ipiranga"


def _import_payload(row: PortalStatementImport, already_imported: bool = False) -> dict:
    return {
        "id": row.id,
        "unit_code": row.unit_code,
        "company_code": row.company_code,
        "category": row.category,
        "category_label": _import_category_label(row.category),
        "client_cnpj": row.client_cnpj,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "original_filename": row.original_filename,
        "content_sha256": row.content_sha256,
        "row_count": row.row_count,
        "imported_count": row.imported_count,
        "duplicate_count": row.duplicate_count,
        "uploaded_by": row.uploaded_by,
        "created_at": row.created_at,
        "already_imported": already_imported,
    }


def _event_chain_payload(match: PortalBonusMatch | None) -> dict | None:
    if not match or not match.details_json:
        return None
    payload = json.loads(match.details_json or "{}").get("chosen")
    return payload or None


def _supplemental_event_payload(
    event: PortalBonusEvent,
    match: PortalBonusMatch | None,
    files: list[dict],
    users: dict[str, User],
    first_postpaid_date: date | None,
) -> dict:
    suggestion = supplemental_review_recommendation(event, first_postpaid_date)
    return {
        "id": event.id,
        "category": event.category,
        "category_label": SUPPLEMENTAL_CATEGORY_LABELS.get(event.category, event.category),
        "portal_date": event.portal_date,
        "value": float(event.value),
        "description": event.description,
        "product": event.product,
        "reference": event.reference,
        "files": files,
        "match_status": match.status if match else "unmatched",
        "match_basis": match.match_basis if match else "Ainda sem cadeia ERP comprovada.",
        "chain": _event_chain_payload(match),
        "review": {
            "classification": event.business_classification,
            "classification_label": supplemental_classification_label(event.business_classification),
            "notes": event.review_notes,
            "reviewed_at": event.reviewed_at,
            "reviewed_by": users.get(event.reviewed_by).full_name if event.reviewed_by in users else event.reviewed_by,
        },
        "suggestion": suggestion,
        "before_first_postpaid": bool(first_postpaid_date and event.portal_date < first_postpaid_date),
    }


def _dashboard(db, unit_code: str = "001") -> dict:
    rule = db.scalar(
        select(BonusRule).where(
            BonusRule.unit_code == unit_code,
            BonusRule.company_code == "IPIRANGA",
            BonusRule.kind == "distributor_credit",
            BonusRule.active.is_(True),
        )
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Regra Ipiranga da unidade não encontrada")
    ledger = build_ipiranga_cycle_ledger(db, rule, date.today())
    upfront = build_upfront_ledger(db, unit_code)
    imports = db.scalars(
        select(PortalStatementImport)
        .where(PortalStatementImport.unit_code == unit_code)
        .order_by(PortalStatementImport.created_at.desc())
    ).all()
    source_rows = db.execute(
        select(PortalBonusEventSource, PortalStatementImport)
        .join(PortalStatementImport, PortalStatementImport.id == PortalBonusEventSource.import_id)
    ).all()
    all_events = db.scalars(
        select(PortalBonusEvent)
        .where(
            PortalBonusEvent.unit_code == unit_code,
            PortalBonusEvent.company_code == "IPIRANGA",
        )
        .order_by(PortalBonusEvent.portal_date, PortalBonusEvent.id)
    ).all()
    matches = {
        row.event_id: row
        for row in db.scalars(
            select(PortalBonusMatch).where(
                PortalBonusMatch.event_id.in_([event.id for event in all_events]) if all_events else False
            )
        ).all()
    }
    files_by_event: dict[str, list[dict]] = defaultdict(list)
    for source, import_row in source_rows:
        raw_payload = json.loads(source.raw_json or "null")
        row_label = (
            raw_payload.get("row_label")
            if isinstance(raw_payload, dict) and raw_payload.get("row_label")
            else f"linha {source.row_number}"
        )
        files_by_event[source.event_id].append(
            {
                "import_id": import_row.id,
                "filename": import_row.original_filename,
                "row_number": source.row_number,
                "row_label": row_label,
                "import_category": import_row.category,
                "import_category_label": _import_category_label(import_row.category),
            }
        )
    reviewed_by_ids = sorted({event.reviewed_by for event in all_events if event.reviewed_by})
    users = {
        row.id: row
        for row in db.scalars(select(User).where(User.id.in_(reviewed_by_ids) if reviewed_by_ids else False)).all()
    }

    expected_total = money(sum((cycle["expected"] for cycle in ledger["cycles"]), Decimal("0")))
    portal_total = money(
        sum((summary["event"].value for summary in ledger["events"]), Decimal("0"))
    )
    cycles = []
    for cycle in ledger["cycles"]:
        if cycle["difference"] == 0 and cycle["confidence"] == "direct":
            status = "auto_confirmed"
        elif cycle["difference"] > 0 and cycle["due_date"] < date.today():
            status = "overdue"
        elif cycle["observed"] > 0:
            status = "partial"
        else:
            status = "pending"
        cycles.append(
            {
                "cycle_number": cycle["cycle_number"],
                "period_start": cycle["period_start"],
                "period_end": cycle["period_end"],
                "reference_month": cycle["reference_month"],
                "due_date": cycle["due_date"],
                "liters": float(cycle["liters"]),
                "purchase_count": cycle["purchase_count"],
                "expected_value": float(cycle["expected"]),
                "observed_value": float(cycle["observed"]),
                "difference_value": float(cycle["difference"]),
                "confidence": cycle["confidence"],
                "status": status,
                "allocations": cycle["allocations"],
            }
        )
    events = []
    for summary in ledger["events"]:
        event: PortalBonusEvent = summary["event"]
        match: PortalBonusMatch | None = summary["match"]
        details = json.loads(match.details_json).get("chosen") if match else None
        events.append(
            {
                "id": event.id,
                "portal_date": event.portal_date,
                "value": float(event.value),
                "description": event.description,
                "product": event.product,
                "event_key": event.event_key,
                "match_status": match.status if match else "unmatched",
                "match_basis": match.match_basis if match else "Ainda sem processamento",
                "date_difference_days": match.date_difference_days if match else None,
                "chain": details,
                "files": files_by_event.get(event.id, []),
                "allocations": [
                    {
                        "cycle_number": ledger["cycles"][index]["cycle_number"],
                        "period_start": ledger["cycles"][index]["period_start"],
                        "period_end": ledger["cycles"][index]["period_end"],
                        "value": float(amount),
                    }
                    for index, amount in summary["allocations"]
                ],
                "unallocated_value": float(summary["unallocated"]),
            }
        )
    supplemental_rows = [row for row in all_events if row.category != POSTPAID_CATEGORY]
    first_postpaid_date = min((event["portal_date"] for event in events), default=None)
    supplemental_total = money(sum((row.value for row in supplemental_rows), Decimal("0")))
    pre_postpaid_total = money(
        sum(
            (row.value for row in supplemental_rows if first_postpaid_date and row.portal_date < first_postpaid_date),
            Decimal("0"),
        )
    )
    supplemental_events = [
        _supplemental_event_payload(
            row,
            matches.get(row.id),
            files_by_event.get(row.id, []),
            users,
            first_postpaid_date,
        )
        for row in supplemental_rows
    ]
    supplemental_categories = []
    grouped_supplemental: dict[str, list[PortalBonusEvent]] = defaultdict(list)
    for row in supplemental_rows:
        grouped_supplemental[row.category].append(row)
    for category, rows in grouped_supplemental.items():
        classified = sum(bool(row.business_classification) for row in rows)
        exact_chain = sum(matches.get(row.id).status == "exact" for row in rows if matches.get(row.id))
        supplemental_categories.append(
            {
                "category": category,
                "category_label": SUPPLEMENTAL_CATEGORY_LABELS.get(category, category),
                "event_count": len(rows),
                "value": float(money(sum((row.value for row in rows), Decimal("0")))),
                "first_date": min(row.portal_date for row in rows),
                "last_date": max(row.portal_date for row in rows),
                "classified_count": classified,
                "pending_count": len(rows) - classified,
                "exact_chain_count": exact_chain,
            }
        )
    supplemental_categories.sort(key=lambda item: (item["first_date"], item["category_label"]))
    classification_counts: dict[str, int] = defaultdict(int)
    for row in supplemental_rows:
        classification_counts[row.business_classification or "pending"] += 1
    return {
        "unit_code": unit_code,
        "company_code": "IPIRANGA",
        "date_basis": "MDCHP.Dt_Emis",
        "cycle_basis": "26 a 25",
        "postpaid": {
            "expected_value": float(expected_total),
            "portal_value": float(portal_total),
            "difference_value": float(money(expected_total - portal_total)),
            "event_count": len(events),
            "exact_match_count": sum(event["match_status"] == "exact" for event in events),
            "automatic_cycle_count": sum(cycle["status"] == "auto_confirmed" for cycle in cycles),
            "overdue_cycle_count": sum(cycle["status"] == "overdue" for cycle in cycles),
        },
        "upfront": {
            "contracted_value": float(upfront["contracted"]),
            "probable_used_value": float(upfront["probable_used"]),
            "estimated_balance_value": float(upfront["estimated_balance"]),
            "automatic": upfront["automatic"],
            "reason": upfront["reason"],
            "candidates": [
                {
                    **row,
                    "value": float(row["value"]),
                }
                for row in upfront["candidates"]
            ],
        },
        "cycles": cycles,
        "events": events,
        "supplemental": {
            "total_value": float(supplemental_total),
            "event_count": len(supplemental_events),
            "category_count": len(supplemental_categories),
            "pre_postpaid_value": float(pre_postpaid_total),
            "first_postpaid_date": first_postpaid_date,
            "classified_count": sum(bool(row.business_classification) for row in supplemental_rows),
            "pending_count": sum(not row.business_classification for row in supplemental_rows),
            "exact_chain_count": sum(
                matches.get(row.id).status == "exact" for row in supplemental_rows if matches.get(row.id)
            ),
            "classification_counts": dict(classification_counts),
            "categories": supplemental_categories,
            "events": supplemental_events,
        },
        "imports": [_import_payload(row) for row in imports],
    }


def _texaco_050_dashboard(db) -> dict:
    """Invoice-oriented portal ledger for the Texaco credits of unit 050."""
    rule = db.scalar(
        select(BonusRule).where(
            BonusRule.unit_code == "050",
            BonusRule.company_code == "TEXACO",
            BonusRule.kind == "invoice_discount",
            BonusRule.active.is_(True),
        )
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Regra Texaco da unidade 050 nao encontrada")
    imports = db.scalars(
        select(PortalStatementImport)
        .where(PortalStatementImport.unit_code == "050")
        .order_by(PortalStatementImport.created_at.desc())
    ).all()
    all_events = db.scalars(
        select(PortalBonusEvent)
        .where(
            PortalBonusEvent.unit_code == "050",
            PortalBonusEvent.company_code == "TEXACO",
        )
        .order_by(PortalBonusEvent.portal_date, PortalBonusEvent.id)
    ).all()
    matches = {
        row.event_id: row
        for row in db.scalars(
            select(PortalBonusMatch).where(
                PortalBonusMatch.event_id.in_([event.id for event in all_events]) if all_events else False
            )
        ).all()
    }
    source_rows = db.execute(
        select(PortalBonusEventSource, PortalStatementImport)
        .join(PortalStatementImport, PortalStatementImport.id == PortalBonusEventSource.import_id)
        .where(PortalStatementImport.unit_code == "050")
    ).all()
    files_by_event: dict[str, list[dict]] = defaultdict(list)
    for source, import_row in source_rows:
        try:
            raw = json.loads(source.raw_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        files_by_event[source.event_id].append(
            {
                "filename": import_row.original_filename,
                "row_label": raw.get("row_label") if isinstance(raw, dict) else None,
                "row_number": source.row_number,
            }
        )

    def event_payload(event: PortalBonusEvent) -> dict:
        match = matches.get(event.id)
        try:
            details = json.loads(match.details_json or "{}") if match else {}
        except json.JSONDecodeError:
            details = {}
        allocations = details.get("allocations") or ([] if not details.get("chosen") else [details["chosen"]])
        return {
            "id": event.id,
            "portal_date": event.portal_date,
            "value": float(event.value),
            "description": event.description,
            "category": event.category,
            "match_status": match.status if match else "unmatched",
            "match_basis": match.match_basis if match else "Ainda sem processamento",
            "files": files_by_event.get(event.id, []),
            "allocations": [
                {
                    "invoice_number": allocation.get("invoice_number"),
                    "title_document": allocation.get("title_document"),
                    "payment_date": allocation.get("movement_date"),
                    "expected_value": float(Decimal(str(allocation.get("expected_value") or 0))),
                    "purchase_entry_id": allocation.get("purchase_entry_id"),
                }
                for allocation in allocations
            ],
        }

    postpaid = [
        event
        for event in all_events
        if event.category == POSTPAID_CATEGORY and (event.description or "").lower() == "nota propria"
    ]
    supplemental = [event for event in all_events if event not in postpaid]
    reconciliations = db.scalars(
        select(Reconciliation).where(
            Reconciliation.rule_id == rule.id,
            Reconciliation.status != "superseded",
        )
    ).all()
    exact_statuses = {"portal_exact", "portal_group_exact"}
    return {
        "unit_code": "050",
        "company_code": "TEXACO",
        "effective_from": rule.effective_from,
        "summary": {
            "expected_value": float(sum((row.expected_value for row in reconciliations), Decimal("0"))),
            "observed_value": float(sum((row.observed_value for row in reconciliations), Decimal("0"))),
            "portal_value": float(sum((event.value for event in postpaid), Decimal("0"))),
            "exact_event_count": sum(
                matches.get(event.id).status in exact_statuses
                for event in postpaid
                if matches.get(event.id)
            ),
            "event_count": len(postpaid),
        },
        "events": [event_payload(event) for event in postpaid],
        "supplemental": [event_payload(event) for event in supplemental],
        "imports": [_import_payload(row) for row in imports],
    }


@router.get("/ipiranga/001")
def ipiranga_001_dashboard(db: DbSession, _: AdminUser):
    return _dashboard(db, "001")


@router.get("/texaco/050")
def texaco_050_dashboard(db: DbSession, _: AdminUser):
    return _texaco_050_dashboard(db)


@router.post("/ipiranga/events/{event_id}/classify")
def classify_ipiranga_event(
    event_id: str,
    payload: PortalSupplementalClassificationIn,
    db: DbSession,
    actor: AdminUser,
):
    event = db.get(PortalBonusEvent, event_id)
    if not event or event.company_code != "IPIRANGA" or event.unit_code != "001":
        raise HTTPException(status_code=404, detail="Evento Ipiranga nao encontrado")
    if event.category == POSTPAID_CATEGORY:
        raise HTTPException(status_code=422, detail="A postecipada principal nao pode ser reclassificada por esta tela")

    classification = (payload.classification or "").strip() or None
    notes = (payload.notes or "").strip() or None
    if classification and classification not in SUPPLEMENTAL_CLASSIFICATION_CHOICES:
        raise HTTPException(status_code=422, detail="Classificacao invalida para credito suplementar")

    previous = {
        "classification": event.business_classification,
        "notes": event.review_notes,
        "reviewed_by": event.reviewed_by,
        "reviewed_at": event.reviewed_at,
    }
    event.business_classification = classification
    event.review_notes = notes
    event.reviewed_by = actor.id if classification or notes else None
    event.reviewed_at = datetime.now(timezone.utc) if classification or notes else None
    db.flush()

    audit(
        db,
        actor,
        "portal_supplemental_classification",
        "portal_bonus_event",
        event.id,
        {
            "previous": previous,
            "current": {
                "classification": event.business_classification,
                "notes": event.review_notes,
                "reviewed_by": event.reviewed_by,
                "reviewed_at": event.reviewed_at,
            },
        },
    )
    db.flush()
    db.commit()
    return {
        "event_id": event.id,
        "dashboard": _dashboard(db, "001"),
    }


@router.post("/ipiranga")
async def upload_ipiranga_statement(
    db: DbSession,
    actor: AdminUser,
    unit_code: str = Form("001"),
    file: UploadFile = File(...),
):
    try:
        uploaded = await read_validated_upload(
            file,
            max_bytes=MAX_PORTAL_BYTES,
            signatures={
                ".pdf": (b"%PDF-",),
                ".xlsx": (b"PK\x03\x04",),
                ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
            },
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    try:
        row, already_imported = import_ipiranga_statement(
            db,
            unit_code=unit_code,
            filename=uploaded.filename,
            content_type=file.content_type,
            content=uploaded.content,
            uploaded_by=actor.id,
        )
    except PortalStatementError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(
        db,
        actor,
        "portal_statement_import",
        "portal_statement_import",
        row.id,
        {
            "filename": row.original_filename,
            "sha256": row.content_sha256,
            "already_imported": already_imported,
            "imported_count": row.imported_count,
            "duplicate_count": row.duplicate_count,
        },
    )
    rebuild_reconciliations(db)
    return {
        "import": _import_payload(row, already_imported),
        "dashboard": _texaco_050_dashboard(db) if unit_code.zfill(3) == "050" else _dashboard(db, "001"),
    }


@router.get("/ipiranga/001/supplemental.csv")
def export_ipiranga_supplemental_csv(db: DbSession, _: AdminUser):
    payload = _dashboard(db, "001")
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "data_credito",
            "categoria_tecnica",
            "categoria_tecnica_label",
            "valor",
            "status_cadeia",
            "classificacao",
            "classificacao_label",
            "sugestao",
            "sugestao_label",
            "confianca_sugestao",
            "referencia",
            "documento_titulo",
            "nota_fiscal",
            "entrada_erp",
            "movimento_mdcmp",
            "observacao",
            "revisado_por",
            "revisado_em",
            "arquivos",
        ]
    )
    for event in payload["supplemental"]["events"]:
        chain = event.get("chain") or {}
        review = event.get("review") or {}
        suggestion = event.get("suggestion") or {}
        writer.writerow(
            [
                event["portal_date"],
                event["category"],
                event["category_label"],
                f"{event['value']:.2f}",
                event["match_status"],
                review.get("classification") or "",
                review.get("classification_label") or "",
                suggestion.get("classification") or "",
                suggestion.get("classification_label") or "",
                suggestion.get("confidence") or "",
                event.get("reference") or "",
                chain.get("title_document") or "",
                chain.get("invoice_number") or "",
                chain.get("purchase_entry_id") or "",
                chain.get("movement_key") or "",
                review.get("notes") or event.get("description") or "",
                review.get("reviewed_by") or "",
                review.get("reviewed_at") or "",
                " | ".join(file.get("filename") or "" for file in event.get("files") or []),
            ]
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=ipiranga-001-creditos-suplementares.csv"},
    )


@router.get("/imports/{import_id}/file")
def download_original(import_id: str, db: DbSession, _: CurrentUser):
    row = db.get(PortalStatementImport, import_id)
    if not row:
        raise HTTPException(status_code=404, detail="Importação não encontrada")
    filename = quote(row.original_filename)
    return Response(
        content=row.source_file,
        media_type=row.content_type or "application/vnd.ms-excel",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
