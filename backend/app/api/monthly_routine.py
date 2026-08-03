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
from app.models import BonusRule, PortalBonusEvent, PortalBonusEventSource, PortalStatementImport, Reconciliation, Unit, User
from app.services.audit import audit
from app.services.ipiranga_portal import MAX_PORTAL_BYTES, PortalStatementError, import_ipiranga_statement
from app.services.reconciliation import RAIZEN_RECEIPT_CATEGORY, rebuild_reconciliations
from app.services.rules import money
from app.services.uploads import UploadValidationError, read_validated_upload


router = APIRouter(prefix="/monthly-routine", tags=["rotina mensal"])

ZERO = Decimal("0")
RAIZEN_UNIT = "054"
RAIZEN_COMPANY = "SHELL"
RAIZEN_PAYER_CNPJ = "033453598000123"
RAIZEN_BENEFICIARY_CNPJ = "12564276000262"

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
) -> dict:
    """Build the read-only operational view from the current reconciliation snapshot."""
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
    imports = db.scalars(select(PortalStatementImport).order_by(PortalStatementImport.created_at.desc())).all()
    imports_by_unit: dict[str, list[PortalStatementImport]] = defaultdict(list)
    for row in imports:
        imports_by_unit[row.unit_code].append(row)

    cards: list[dict] = []
    for reference_month in reference_months:
        for rule in rules:
            if units and rule.unit_code not in units:
                continue
            if companies and rule.company_code not in companies:
                continue
            source_type = _source_type(rule)
            if source_types and source_type not in source_types:
                continue
            reconciliation = reconciliation_by_key.get((rule.id, reference_month))
            expected = money(Decimal(reconciliation.expected_value)) if reconciliation else ZERO
            observed = (
                money(Decimal(reconciliation.observed_value) + Decimal(reconciliation.manual_adjustment or 0))
                if reconciliation
                else ZERO
            )
            difference = money(Decimal(reconciliation.difference_value)) if reconciliation else ZERO
            confirmed = bool(reconciliation and reconciliation.status in {"confirmed", "auto_confirmed", "manual_confirmed"})
            covered_imports = [row for row in imports_by_unit[rule.unit_code] if _covers_month(row, reference_month)]
            if expected <= ZERO:
                situation = "automatic"
                action = "Nenhuma bonificação liberada nesta competência"
                description = "A regra permanece monitorada, mas não há valor a conferir no período selecionado."
            elif confirmed:
                situation = "automatic"
                action = "Conferência concluída"
                description = "O valor esperado foi fechado por evidência exata e a trilha foi preservada."
            elif source_type != "erp" and not covered_imports:
                situation = "awaiting_source"
                action = f"Importar {SOURCE_META[source_type]['label']}"
                description = SOURCE_META[source_type]["description"]
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
                    "queue_url": f"/conciliacoes?unit={rule.unit_code}&reference_month={reference_month.isoformat()}&scope=all",
                }
            )

    order = {"awaiting_source": 0, "analysis": 1, "automatic": 2}
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
