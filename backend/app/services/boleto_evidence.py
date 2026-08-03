from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    BonusRule,
    InvoiceBoletoEvidence,
    PayableDocument,
    Purchase,
    Reconciliation,
)
from app.services.rules import money


MAX_BOLETO_BYTES = 6 * 1024 * 1024
MAX_BOLETO_PAGES = 5


class BoletoEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedBoleto:
    text: str
    cedent_name: str | None
    payer_name: str | None
    payer_cnpj: str | None
    boleto_document_number: str | None
    invoice_number: str | None
    title_document_id: str | None
    nosso_numero: str | None
    issue_date: date | None
    due_date: date | None
    gross_value: Decimal
    discount_value: Decimal


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _date_br(value: str) -> date:
    return datetime.strptime(value, "%d/%m/%Y").date()


def _money_br(value: str) -> Decimal:
    try:
        return money(Decimal(value.replace(".", "").replace(",", ".")))
    except InvalidOperation as exc:
        raise BoletoEvidenceError(f"Valor monetário inválido no boleto: {value}") from exc


def _extract_pdf_text(content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise BoletoEvidenceError("O arquivo não possui uma assinatura PDF válida")
    if len(content) > MAX_BOLETO_BYTES:
        raise BoletoEvidenceError("O boleto excede o limite de 6 MB")
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise BoletoEvidenceError("Boletos protegidos por senha não são aceitos")
        if not reader.pages or len(reader.pages) > MAX_BOLETO_PAGES:
            raise BoletoEvidenceError("O PDF deve possuir entre 1 e 5 páginas")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, OSError, ValueError) as exc:
        raise BoletoEvidenceError("Não foi possível interpretar o PDF informado") from exc
    text = text.strip()
    if len(text) < 80:
        raise BoletoEvidenceError("O PDF não contém texto suficiente para validar o boleto")
    return text[:50000]


def parse_boleto_pdf(content: bytes) -> ParsedBoleto:
    text = _extract_pdf_text(content)
    compact = re.sub(r"[ \t]+", " ", text)

    cnpjs = re.findall(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", compact)
    payer_cnpj = _digits(cnpjs[-1]) if cnpjs else None

    document_match = re.search(r"\b(\d{12,16})\s*-\s*(\d{1,3})\b", compact)
    document_number = None
    invoice_number = None
    title_document_id = None
    if document_match:
        base = document_match.group(1)
        document_number = f"{base} - {document_match.group(2)}"
        invoice_number = base[-7:]
        title_document_id = base[-6:]

    nosso_match = re.search(r"\b(\d{7,12}-\d)\b", compact)
    nosso_numero = nosso_match.group(1) if nosso_match else None

    discount_match = re.search(
        r"CONCEDER\s+DESCONTO\s*:\s*R\$\s*([\d.]+,\d{2})",
        compact,
        re.IGNORECASE,
    )
    if not discount_match:
        raise BoletoEvidenceError("O PDF não declara a instrução 'CONCEDER DESCONTO' com valor")
    discount = _money_br(discount_match.group(1))

    amounts = [_money_br(value) for value in re.findall(r"(?<!\d)(\d{1,3}(?:\.\d{3})+,\d{2})(?!\d)", compact)]
    gross_candidates = [value for value in amounts if value > discount]
    if not gross_candidates:
        raise BoletoEvidenceError("Não foi possível identificar o valor bruto do boleto")
    gross = max(gross_candidates)

    dates = [_date_br(value) for value in re.findall(r"\b\d{2}/\d{2}/\d{4}\b", compact)]
    unique_dates = list(dict.fromkeys(dates))
    issue_date = unique_dates[0] if unique_dates else None
    due_date = unique_dates[1] if len(unique_dates) > 1 else None

    upper = compact.upper()
    cedent = "IPIRANGA PRODUTOS DE PETROLEO SA" if "IPIRANGA PRODUTOS DE PETROLEO" in upper else None
    payer = "GBI COMBUSTIVEIS LTDA." if "GBI COMBUSTIVEIS LTDA" in upper else None
    return ParsedBoleto(
        text=text,
        cedent_name=cedent,
        payer_name=payer,
        payer_cnpj=payer_cnpj,
        boleto_document_number=document_number,
        invoice_number=invoice_number,
        title_document_id=title_document_id,
        nosso_numero=nosso_numero,
        issue_date=issue_date,
        due_date=due_date,
        gross_value=gross,
        discount_value=discount,
    )


def _eligible_liters(purchase: Purchase, rule: BonusRule) -> Decimal:
    applies_to = (rule.applies_to or "all_fuel").strip().lower()
    if not applies_to.startswith("fuel_codes:"):
        return Decimal(purchase.total_liters or 0)
    codes = {value.strip() for value in applies_to.split(":", 1)[1].split(",") if value.strip()}
    return sum(
        (Decimal(item.quantity or 0) for item in purchase.items if str(item.item_code).strip() in codes),
        Decimal("0"),
    )


def _unit_from_cnpj(cnpj: str | None) -> str | None:
    digits = _digits(cnpj)
    if len(digits) != 14:
        return None
    return str(int(digits[8:12])).zfill(3)


def _match_purchase(
    db: Session,
    row: Reconciliation,
    rule: BonusRule,
    parsed: ParsedBoleto,
) -> tuple[Purchase | None, PayableDocument | None, Decimal | None, str, str]:
    start = (parsed.issue_date or row.reference_month) - timedelta(days=10)
    end = (parsed.issue_date or row.reference_month) + timedelta(days=40)
    purchases = db.scalars(
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(
            Purchase.unit_code == row.unit_code,
            Purchase.mapped_company_code == rule.company_code,
            Purchase.purchase_date >= start,
            Purchase.purchase_date <= end,
        )
    ).all()
    invoice_digits = _digits(parsed.invoice_number)
    exact_candidates = [
        purchase
        for purchase in purchases
        if invoice_digits
        and _digits(purchase.invoice_number) == invoice_digits
        and money(purchase.gross_value) == parsed.gross_value
    ]
    if len(exact_candidates) != 1:
        status = "ambiguous" if len(exact_candidates) > 1 else "unmatched"
        return None, None, None, status, (
            f"{len(exact_candidates)} nota(s) com número {parsed.invoice_number or 'não identificado'} "
            f"e valor {parsed.gross_value} na unidade {row.unit_code}."
        )

    purchase = exact_candidates[0]
    expected = money(_eligible_liters(purchase, rule) * Decimal(rule.rate_per_liter))
    documents = db.scalars(
        select(PayableDocument).where(PayableDocument.erp_entry_id == purchase.erp_entry_id)
    ).all()
    title_digits = _digits(parsed.title_document_id)
    exact_documents = [
        document
        for document in documents
        if title_digits
        and _digits(document.document_id) == title_digits
        and money(document.document_value) == parsed.gross_value
        and (not parsed.due_date or document.due_date == parsed.due_date)
    ]
    payer_unit = _unit_from_cnpj(parsed.payer_cnpj)
    checks = {
        "unidade do CNPJ": payer_unit == row.unit_code,
        "nota e valor bruto": True,
        "título único": len(exact_documents) == 1,
        "desconto do boleto": parsed.discount_value == expected,
    }
    exact = all(checks.values())
    status = "exact" if exact else "mismatch"
    basis = "; ".join(f"{name}: {'OK' if ok else 'DIVERGENTE'}" for name, ok in checks.items())
    return purchase, exact_documents[0] if len(exact_documents) == 1 else None, expected, status, basis


def import_boleto_evidence(
    db: Session,
    row: Reconciliation,
    rule: BonusRule,
    *,
    filename: str,
    content_type: str | None,
    content: bytes,
    uploaded_by: str,
) -> tuple[InvoiceBoletoEvidence, bool]:
    if rule.kind != "invoice_discount":
        raise BoletoEvidenceError("Boletos de desconto só podem ser anexados a regras de desconto em boleto")
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(InvoiceBoletoEvidence).where(InvoiceBoletoEvidence.content_sha256 == digest)
    )
    if existing:
        if existing.reconciliation_id != row.id:
            raise BoletoEvidenceError("Este boleto já está vinculado a outra competência")
        return existing, True

    parsed = parse_boleto_pdf(content)
    purchase, document, expected, status, basis = _match_purchase(db, row, rule, parsed)
    evidence = InvoiceBoletoEvidence(
        reconciliation_id=row.id,
        unit_code=row.unit_code,
        company_code=rule.company_code,
        purchase_entry_id=purchase.erp_entry_id if purchase else None,
        payable_document_id=document.id if document else None,
        original_filename=(filename or "boleto.pdf")[:255],
        content_type=(content_type or "application/pdf")[:120],
        content_sha256=digest,
        source_file=content,
        cedent_name=parsed.cedent_name,
        payer_name=parsed.payer_name,
        payer_cnpj=parsed.payer_cnpj,
        boleto_document_number=parsed.boleto_document_number,
        invoice_number=parsed.invoice_number,
        title_document_id=parsed.title_document_id,
        nosso_numero=parsed.nosso_numero,
        issue_date=parsed.issue_date,
        due_date=parsed.due_date,
        gross_value=parsed.gross_value,
        discount_value=parsed.discount_value,
        net_value=money(parsed.gross_value - parsed.discount_value),
        expected_discount_value=expected,
        match_status=status,
        match_basis=basis,
        parsed_text=parsed.text,
        uploaded_by=uploaded_by,
    )
    db.add(evidence)
    db.flush()
    return evidence, False


def boleto_payload(row: InvoiceBoletoEvidence) -> dict:
    return {
        "source": "BOLETO_PDF",
        "id": row.id,
        "date": row.issue_date.isoformat() if row.issue_date else None,
        "document": row.title_document_id,
        "purchase_entry_id": row.purchase_entry_id,
        "invoice_number": row.invoice_number,
        "value": float(row.discount_value),
        "source_value": float(row.discount_value),
        "allocated": 0,
        "counted": False,
        "match_status": row.match_status,
        "gross_value": float(row.gross_value),
        "discount_value": float(row.discount_value),
        "net_value": float(row.net_value),
        "expected_discount_value": (
            float(row.expected_discount_value) if row.expected_discount_value is not None else None
        ),
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "payer_cnpj": row.payer_cnpj,
        "match_basis": row.match_basis,
    }
