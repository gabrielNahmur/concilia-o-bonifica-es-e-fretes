from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook
from pypdf import PdfReader
import xlrd
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    BonusRule,
    Contract,
    FinancialEntry,
    PayableDocument,
    PayableMovement,
    PortalBonusEvent,
    PortalBonusEventSource,
    PortalBonusMatch,
    PortalStatementImport,
    Purchase,
)
from app.services.rules import add_months, due_date_for_month, money, month_start


ALGORITHM_VERSION = "ipiranga-portal-v1.3"
MAX_PORTAL_BYTES = 5 * 1024 * 1024
MAX_PORTAL_PDF_PAGES = 100
MAX_XLSX_ENTRIES = 2_000
MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
POSTPAID_CATEGORY = "postpaid"
CREDIT_REPORT_CATEGORY = "credit_report"
CONTRACT_PARCELS_REPORT_CATEGORY = "contract_parcels_report"
SUPPLEMENTAL_CATEGORY_LABELS = {
    "supplemental_auto_deposit": "Aviso de credito deposito auto",
    "supplemental_credit_notice": "Aviso de credito",
    "supplemental_price_difference": "Credito diferenca de preco",
    "supplemental_advance_transfer": "Pagamento antecipado/transferencia",
    "supplemental_other_credit": "Outros creditos do relatorio",
}
SUPPLEMENTAL_BUSINESS_CLASSIFICATIONS = {
    "postpaid_regularization": "Regularização da postecipada",
    "upfront": "Antecipacao",
    "price_difference": "Diferenca de preco",
    "commercial_credit": "Credito comercial",
    "other": "Outro credito",
}
ZERO = Decimal("0")
PDF_EVENT_PATTERN = re.compile(
    r"^\s*(?P<date>\d{2}/\d{2}/\d{4})\s+Bonifica.*?Postecipada\s+(?P<value>[\d.]+,\d{2})\s*$",
    re.IGNORECASE,
)
PDF_OWN_NOTE_PATTERN = re.compile(
    r"^\s*(?P<date>\d{2}/\d{2}/\d{4})\s+Nota\s+Pr.pria\s+(?P<value>[\d.]+,\d{2})\s*$",
    re.IGNORECASE,
)
PDF_PRODUCT_INVOICE_PATTERN = re.compile(
    r"^\s*(?P<date>\d{2}/\d{2}/\d{4})\s+Nota\s+Fiscal\s+Produto\s+(?P<value>-?[\d.]+,\d{2})\s*$",
    re.IGNORECASE,
)
PDF_COMMERCIAL_ACTION_PATTERN = re.compile(
    r"^\s*(?P<date>\d{2}/\d{2}/\d{4})\s+A..o\s+comercial\s+Combs\s+(?P<value>[\d.]+,\d{2})\s*$",
    re.IGNORECASE,
)
PDF_DATE_PATTERN = re.compile(r"\d{2}/\d{2}/\d{2,4}")

# O extrato consolidado Texaco mostra somente os doze primeiros dígitos do
# CNPJ.  Eles são uma trava de importação, não uma tentativa de reconstruir o
# dígito verificador ausente no PDF.
PORTAL_CNPJ_PREFIXES = {
    "014": "905896980012",
    "050": "125642760001",
}


class PortalStatementError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedPortalEvent:
    row_number: int
    portal_date: date
    value: Decimal
    product: str | None
    description: str | None
    reference: str | None
    raw: object
    category: str = POSTPAID_CATEGORY
    row_label: str | None = None


@dataclass(frozen=True)
class ParsedPortalStatement:
    client_cnpj: str | None
    category_label: str
    period_start: date | None
    period_end: date | None
    events: list[ParsedPortalEvent]


def supplemental_classification_label(code: str | None) -> str | None:
    if not code:
        return None
    return SUPPLEMENTAL_BUSINESS_CLASSIFICATIONS.get(code, code)


def supplemental_review_recommendation(
    event: PortalBonusEvent, first_postpaid_date: date | None = None
) -> dict:
    recommendation = {
        "classification": None,
        "classification_label": None,
        "confidence": "none",
        "reason": "Sem sugestao automatica segura para este credito.",
    }
    if event.category == "supplemental_advance_transfer":
        recommendation = {
            "classification": "upfront",
            "classification_label": supplemental_classification_label("upfront"),
            "confidence": "high",
            "reason": "O relatorio identifica transferencia/pagamento antecipado explicitamente.",
        }
    elif event.category == "supplemental_price_difference":
        recommendation = {
            "classification": "price_difference",
            "classification_label": supplemental_classification_label("price_difference"),
            "confidence": "high",
            "reason": "O tipo do documento veio como credito por diferenca de preco.",
        }
    elif event.category == "supplemental_credit_notice":
        recommendation = {
            "classification": "commercial_credit",
            "classification_label": supplemental_classification_label("commercial_credit"),
            "confidence": "medium",
            "reason": "Aviso de credito sem prova suficiente de que seja postecipada do contrato.",
        }
    elif event.category == "supplemental_auto_deposit" and first_postpaid_date and event.portal_date < first_postpaid_date:
        recommendation = {
            "classification": None,
            "classification_label": None,
            "confidence": "low",
            "reason": "Credito anterior a primeira postecipada; pode ser regularizacao, mas precisa validacao humana.",
        }
    return recommendation


def _normalized(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(char for char in text if not unicodedata.combining(char)).lower().split())


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _cnpj(value) -> str | None:
    digits = "".join(char for char in _cell_text(value) if char.isdigit())
    return digits.zfill(14) if digits else None


def _decimal_br(value) -> Decimal:
    if isinstance(value, (int, float, Decimal)):
        return money(Decimal(str(value)))
    text = _cell_text(value).replace("R$", "").replace(" ", "")
    if not text:
        raise PortalStatementError("Valor vazio no lançamento do portal")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return money(Decimal(text))
    except InvalidOperation as exc:
        raise PortalStatementError(f"Valor inválido no extrato: {value}") from exc


def _date_value(value, datemode: int = 0, cell_type: int | None = None) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if cell_type == xlrd.XL_CELL_DATE or isinstance(value, (int, float)):
        try:
            return xlrd.xldate_as_datetime(value, datemode).date()
        except (ValueError, TypeError, xlrd.XLDateError):
            pass
    text = _cell_text(value)
    for pattern in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise PortalStatementError(f"Data inválida no extrato: {value}")


def _parse_sheet(sheet, datemode: int = 0) -> ParsedPortalStatement:
    metadata: dict[str, tuple[int, list]] = {}
    header_row = None
    for row_index in range(sheet.nrows):
        values = [sheet.cell_value(row_index, col) for col in range(sheet.ncols)]
        label = _normalized(values[0] if values else "")
        if label:
            metadata[label] = (row_index, values)
        if label == "data" and len(values) > 4 and _normalized(values[4]) in {"valor (r$)", "valor"}:
            header_row = row_index

    if header_row is None:
        raise PortalStatementError("Cabeçalho Data/Valor não localizado no extrato Ipiranga")
    category_values = metadata.get("categoria", (None, []))[1]
    category_label = _cell_text(category_values[1] if len(category_values) > 1 else "")
    if _normalized(category_label) != "bonificacao postecipada":
        raise PortalStatementError("O arquivo não é da categoria Bonificação Postecipada")
    client_values = metadata.get("cnpj do cliente", (None, []))[1]
    period_values = metadata.get("periodo", (None, []))[1]
    period_start = _date_value(period_values[1], datemode) if len(period_values) > 1 else None
    period_end = _date_value(period_values[3], datemode) if len(period_values) > 3 else None

    events: list[ParsedPortalEvent] = []
    for row_index in range(header_row + 1, sheet.nrows):
        values = [sheet.cell_value(row_index, col) for col in range(sheet.ncols)]
        description = _cell_text(values[2] if len(values) > 2 else "")
        if _normalized(description) != "bonificacao":
            continue
        date_cell = sheet.cell(row_index, 0)
        portal_date = _date_value(date_cell.value, datemode, getattr(date_cell, "ctype", None))
        if not portal_date:
            raise PortalStatementError(f"Lançamento sem data na linha {row_index + 1}")
        event_value = _decimal_br(values[4] if len(values) > 4 else None)
        if event_value <= 0:
            raise PortalStatementError(f"Lançamento sem valor positivo na linha {row_index + 1}")
        events.append(
            ParsedPortalEvent(
                row_number=row_index + 1,
                portal_date=portal_date,
                value=event_value,
                product=_cell_text(values[1]) or None,
                description=description or None,
                reference=_cell_text(values[3]) or None,
                raw=[_cell_text(value) for value in values],
                row_label=f"linha {row_index + 1}",
            )
        )
    if not events:
        raise PortalStatementError("Nenhum lançamento de bonificação foi encontrado")
    return ParsedPortalStatement(
        client_cnpj=_cnpj(client_values[1] if len(client_values) > 1 else None),
        category_label=POSTPAID_CATEGORY,
        period_start=period_start,
        period_end=period_end,
        events=events,
    )


def _event_row_label(row_numbers: list[int]) -> str:
    if not row_numbers:
        return "sem linha"
    if len(row_numbers) == 1:
        return f"linha {row_numbers[0]}"
    return f"linhas {row_numbers[0]}-{row_numbers[-1]}"


def _credit_report_event_category(type_code, type_description, note) -> str | None:
    normalized_code = _normalized(type_code)
    normalized_description = _normalized(type_description)
    normalized_note = _normalized(note)
    if normalized_code == "cu" and normalized_note == "bonificacao":
        return POSTPAID_CATEGORY
    if normalized_code == "cz":
        return "supplemental_auto_deposit"
    if normalized_code == "cr":
        return "supplemental_credit_notice"
    if normalized_code == "cd":
        return "supplemental_price_difference"
    if normalized_code == "p#":
        return "supplemental_advance_transfer"
    if "bonificacao" in normalized_note or "credito" in normalized_description or "pagto" in normalized_note:
        return "supplemental_other_credit"
    return None


def _parse_credit_report(content: bytes) -> ParsedPortalStatement:
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # pragma: no cover - defensive wrapper
        raise PortalStatementError("Arquivo .xlsx invalido ou corrompido") from exc
    try:
        if len(workbook.worksheets) != 1:
            raise PortalStatementError("O relatorio de creditos deve possuir exatamente uma planilha")
        sheet = workbook.worksheets[0]
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()
    if not rows:
        raise PortalStatementError("A planilha de creditos esta vazia")

    headers = [_normalized(value) for value in rows[0]]
    required = {
        "numero documen.": "document_number",
        "tipo doc.": "type_code",
        "tipo de documento": "type_description",
        "item pgto": "payment_item",
        "data fatura": "invoice_date",
        "valor bruto": "gross_value",
        "data fech.": "close_date",
        "nome-observacao": "note",
        "documento original": "original_document",
    }
    indexes: dict[str, int] = {}
    for normalized_header, key in required.items():
        try:
            indexes[key] = headers.index(normalized_header)
        except ValueError as exc:
            raise PortalStatementError(
                f"O relatorio de creditos nao possui a coluna obrigatoria '{normalized_header}'"
            ) from exc

    grouped: dict[tuple, dict] = {}
    period_start = None
    period_end = None
    for row_number, values in enumerate(rows[1:], start=2):
        type_code = _cell_text(values[indexes["type_code"]] if indexes["type_code"] < len(values) else None)
        type_description = _cell_text(
            values[indexes["type_description"]] if indexes["type_description"] < len(values) else None
        )
        note = _cell_text(values[indexes["note"]] if indexes["note"] < len(values) else None)
        category = _credit_report_event_category(type_code, type_description, note)
        if not category:
            continue
        close_date = _date_value(values[indexes["close_date"]] if indexes["close_date"] < len(values) else None)
        if not close_date:
            raise PortalStatementError(f"Lancamento sem data de fechamento na linha {row_number}")
        raw_value = _decimal_br(values[indexes["gross_value"]] if indexes["gross_value"] < len(values) else None)
        event_value = money(abs(raw_value))
        if event_value <= ZERO:
            continue
        invoice_date = _date_value(values[indexes["invoice_date"]] if indexes["invoice_date"] < len(values) else None)
        accounting_date = invoice_date or close_date
        period_start = min(period_start, accounting_date) if period_start else accounting_date
        period_end = max(period_end, accounting_date) if period_end else accounting_date
        document_number = _cell_text(
            values[indexes["document_number"]] if indexes["document_number"] < len(values) else None
        )
        original_document = _cell_text(
            values[indexes["original_document"]] if indexes["original_document"] < len(values) else None
        )
        group_key = (
            category,
            close_date,
            document_number,
            original_document,
            invoice_date,
            type_code,
            type_description,
            note,
        )
        group = grouped.setdefault(
            group_key,
            {
                "row_number": row_number,
                "row_numbers": [],
                "portal_date": close_date,
                "invoice_date": invoice_date,
                "value": ZERO,
                "product": type_description or None,
                "description": note or type_description or None,
                "reference": original_document or document_number or None,
                "category": category,
                "type_code": type_code,
                "type_description": type_description,
                "document_number": document_number,
                "original_document": original_document,
                "items": [],
            },
        )
        group["row_numbers"].append(row_number)
        group["value"] += event_value
        group["items"].append(
            {
                "row_number": row_number,
                "item_payment": _cell_text(
                    values[indexes["payment_item"]] if indexes["payment_item"] < len(values) else None
                ),
                "value": str(event_value),
            }
        )

    events: list[ParsedPortalEvent] = []
    for group in sorted(grouped.values(), key=lambda item: (item["portal_date"], item["row_number"])):
        row_label = _event_row_label(group["row_numbers"])
        events.append(
            ParsedPortalEvent(
                row_number=group["row_number"],
                portal_date=group["portal_date"],
                value=money(group["value"]),
                product=group["product"],
                description=group["description"],
                reference=group["reference"],
                raw={
                    "source_report": CREDIT_REPORT_CATEGORY,
                    "row_numbers": group["row_numbers"],
                    "row_label": row_label,
                    "type_doc": group["type_code"],
                    "type_description": group["type_description"],
                    "document_number": group["document_number"],
                    "original_document": group["original_document"],
                    "invoice_date": group["invoice_date"].isoformat() if group["invoice_date"] else None,
                    "event_category": group["category"],
                    "items": group["items"],
                },
                category=group["category"],
                row_label=row_label,
            )
        )
    if not events:
        raise PortalStatementError("Nenhum credito relevante foi encontrado no relatorio Ipiranga")
    return ParsedPortalStatement(
        client_cnpj=None,
        category_label=CREDIT_REPORT_CATEGORY,
        period_start=period_start,
        period_end=period_end,
        events=events,
    )


def _parse_contract_parcels_report(content: bytes) -> ParsedPortalStatement:
    """Read the Ipiranga report listing contractual postpaid installments.

    This is distinct from the credit ledger: one row is a contractual cycle
    and ``Crédito Emitido`` is an explicit confirmation that the distributor
    created that credit. Rows still in progress are retained in the original
    file but deliberately do not become financial evidence.
    """
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # pragma: no cover - defensive wrapper
        raise PortalStatementError("Arquivo .xlsx invalido ou corrompido") from exc
    try:
        if len(workbook.worksheets) != 1:
            raise PortalStatementError("O relatorio de parcelas deve possuir exatamente uma planilha")
        rows = list(workbook.worksheets[0].iter_rows(values_only=True))
    finally:
        workbook.close()
    if not rows:
        raise PortalStatementError("A planilha de parcelas esta vazia")

    headers = [_normalized(value) for value in rows[0]]
    required = {
        "cnpj": "cnpj",
        "parcela": "installment",
        "status": "status",
        "volume real": "volume",
        "periodo inicial": "period_start",
        "periodo final": "period_end",
        "data da emissao": "issued_at",
        "valor credito": "credit_value",
    }
    indexes: dict[str, int] = {}
    for header, key in required.items():
        try:
            indexes[key] = headers.index(header)
        except ValueError as exc:
            raise PortalStatementError(
                f"O relatorio nao possui a coluna obrigatoria '{header}'"
            ) from exc

    client_cnpj = None
    overall_start = None
    overall_end = None
    events: list[ParsedPortalEvent] = []
    for row_number, values in enumerate(rows[1:], start=2):
        installment = _cell_text(values[indexes["installment"]] if indexes["installment"] < len(values) else None)
        if not installment:
            continue
        period_start = _date_value(
            values[indexes["period_start"]] if indexes["period_start"] < len(values) else None
        )
        period_end = _date_value(
            values[indexes["period_end"]] if indexes["period_end"] < len(values) else None
        )
        if not period_start or not period_end:
            raise PortalStatementError(f"Parcela {installment} sem periodo valido na linha {row_number}")
        overall_start = min(overall_start, period_start) if overall_start else period_start
        overall_end = max(overall_end, period_end) if overall_end else period_end
        if client_cnpj is None:
            client_cnpj = _cnpj(values[indexes["cnpj"]] if indexes["cnpj"] < len(values) else None)

        status = _cell_text(values[indexes["status"]] if indexes["status"] < len(values) else None)
        if _normalized(status) != "credito emitido":
            continue
        issued_at = _date_value(
            values[indexes["issued_at"]] if indexes["issued_at"] < len(values) else None
        )
        if not issued_at:
            raise PortalStatementError(f"Parcela emitida sem data de emissao na linha {row_number}")
        credit_value = _decimal_br(
            values[indexes["credit_value"]] if indexes["credit_value"] < len(values) else None
        )
        if credit_value <= ZERO:
            raise PortalStatementError(f"Parcela emitida sem credito positivo na linha {row_number}")
        volume_m3 = _decimal_br(values[indexes["volume"]] if indexes["volume"] < len(values) else None)
        row_label = f"parcela {installment}, linha {row_number}"
        events.append(
            ParsedPortalEvent(
                row_number=row_number,
                portal_date=issued_at,
                value=credit_value,
                product=None,
                description="Credito Emitido",
                reference=f"Parcela {installment}",
                raw={
                    "source_report": CONTRACT_PARCELS_REPORT_CATEGORY,
                    "row_label": row_label,
                    "installment": installment,
                    "status": status,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "volume_m3": str(volume_m3),
                    "credit_value": str(credit_value),
                    "business_classification": "postpaid_issued",
                },
                category=POSTPAID_CATEGORY,
                row_label=row_label,
            )
        )
    if not events:
        raise PortalStatementError("Nenhuma parcela com Crédito Emitido foi encontrada no relatorio")
    return ParsedPortalStatement(
        client_cnpj=client_cnpj,
        category_label=CONTRACT_PARCELS_REPORT_CATEGORY,
        period_start=overall_start,
        period_end=overall_end,
        events=events,
    )


def _parse_portal_pdf_text(page_texts: list[str]) -> ParsedPortalStatement:
    """Read the compact consolidated statement exported by the Ipiranga portal.

    Some units export a PDF listing rather than a tabular statement. Only rows
    explicitly labelled as postpaid bonuses are materialized; invoice debits
    and daily totals remain available in the preserved original PDF.
    """
    if not page_texts:
        raise PortalStatementError("Extrato PDF vazio ou sem texto selecionavel")

    client_cnpj = None
    period_start = None
    period_end = None
    for line in page_texts[0].splitlines():
        lowered = line.lower()
        if "cnpj" in lowered and ":" in line:
            client_cnpj = _cnpj(line.split(":", 1)[1])
        if lowered.startswith("per") and ":" in line:
            dates = PDF_DATE_PATTERN.findall(line)
            if len(dates) >= 2:
                period_start = _date_value(dates[0])
                period_end = _date_value(dates[1])

    product_totals: dict[date, Decimal] = defaultdict(lambda: ZERO)
    product_line_counts: dict[date, int] = defaultdict(int)
    for text in page_texts:
        for line in text.splitlines():
            product_match = PDF_PRODUCT_INVOICE_PATTERN.match(line)
            if not product_match:
                continue
            product_date = _date_value(product_match.group("date"))
            if not product_date:
                continue
            product_totals[product_date] = money(
                product_totals[product_date] + abs(_decimal_br(product_match.group("value")))
            )
            product_line_counts[product_date] += 1

    events: list[ParsedPortalEvent] = []
    for page_number, text in enumerate(page_texts, start=1):
        for line_number, line in enumerate(text.splitlines(), start=1):
            match = PDF_EVENT_PATTERN.match(line)
            description = "Bonificacao Postecipada"
            source_format = "pdf"
            category = POSTPAID_CATEGORY
            if not match:
                match = PDF_OWN_NOTE_PATTERN.match(line)
                description = "Nota Propria"
                source_format = "pdf_consolidated_texaco"
            if not match:
                match = PDF_COMMERCIAL_ACTION_PATTERN.match(line)
                description = "Acao comercial combustiveis"
                source_format = "pdf_consolidated_texaco"
                category = "supplemental_other_credit"
            if not match:
                continue
            portal_date = _date_value(match.group("date"))
            event_value = _decimal_br(match.group("value"))
            if not portal_date or event_value <= ZERO:
                continue
            events.append(
                ParsedPortalEvent(
                    row_number=len(events) + 1,
                    portal_date=portal_date,
                    value=event_value,
                    product=None,
                    description=description,
                    reference=None,
                    raw={
                        "source_format": source_format,
                        "page": page_number,
                        "line": line_number,
                        "text": line.strip(),
                        # A soma da(s) NF(s) de produto do mesmo dia é uma
                        # âncora primária do próprio portal. Ela só é usada
                        # quando fecha centavo a centavo os títulos ERP.
                        "portal_product_total": (
                            str(product_totals[portal_date])
                            if description == "Nota Propria" and product_line_counts[portal_date]
                            else None
                        ),
                        "portal_product_line_count": (
                            product_line_counts[portal_date]
                            if description == "Nota Propria" and product_line_counts[portal_date]
                            else 0
                        ),
                    },
                    category=category,
                    row_label=f"pagina {page_number}, linha {line_number}",
                )
            )
    if not events:
        is_consolidated_statement = any(
            "extrato consolidado" in _normalized(text) for text in page_texts
        )
        if not is_consolidated_statement:
            raise PortalStatementError("Nenhuma Bonificacao Postecipada foi encontrada no extrato PDF")
    return ParsedPortalStatement(
        client_cnpj=client_cnpj,
        category_label=POSTPAID_CATEGORY,
        period_start=period_start,
        period_end=period_end,
        events=events,
    )


def _parse_portal_pdf(content: bytes) -> ParsedPortalStatement:
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise PortalStatementError("Arquivos PDF protegidos por senha nao sao aceitos")
        if not reader.pages or len(reader.pages) > MAX_PORTAL_PDF_PAGES:
            raise PortalStatementError(
                f"O PDF deve possuir entre 1 e {MAX_PORTAL_PDF_PAGES} paginas"
            )
        page_texts = [(page.extract_text() or "") for page in reader.pages]
    except PortalStatementError:
        raise
    except Exception as exc:  # pragma: no cover - defensive wrapper
        raise PortalStatementError("Arquivo PDF invalido ou sem leitura disponivel") from exc
    return _parse_portal_pdf_text(page_texts)


def _validate_xlsx_archive(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_XLSX_ENTRIES:
                raise PortalStatementError("A planilha possui arquivos internos em excesso")
            unpacked_size = sum(entry.file_size for entry in entries)
            if unpacked_size > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise PortalStatementError("A planilha excede o limite descompactado de 50 MB")
    except zipfile.BadZipFile as exc:
        raise PortalStatementError("Arquivo .xlsx invalido ou corrompido") from exc


def parse_ipiranga_statement(content: bytes, filename: str | None = None) -> ParsedPortalStatement:
    lower_name = (filename or "").lower()
    if lower_name.endswith(".pdf") or content[:4] == b"%PDF":
        return _parse_portal_pdf(content)
    if lower_name.endswith(".xlsx") or content[:2] == b"PK":
        _validate_xlsx_archive(content)
        # O portal possui dois layouts .xlsx: o razao de creditos e a matriz
        # de parcelas. O segundo e reconhecido por sua coluna Parcela.
        try:
            return _parse_contract_parcels_report(content)
        except PortalStatementError as parcel_error:
            try:
                return _parse_credit_report(content)
            except PortalStatementError:
                raise parcel_error
    try:
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
    except (xlrd.XLRDError, OSError) as exc:
        raise PortalStatementError("Arquivo .xls inválido ou corrompido") from exc
    try:
        if workbook.nsheets != 1:
            raise PortalStatementError("O extrato deve possuir exatamente uma planilha")
        return _parse_sheet(workbook.sheet_by_index(0), workbook.datemode)
    finally:
        workbook.release_resources()


def _event_key(unit_code: str, company_code: str, event: ParsedPortalEvent) -> str:
    identity = f"{unit_code}|{company_code}|{event.category}|{event.portal_date.isoformat()}|{money(event.value)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def import_ipiranga_statement(
    db: Session,
    *,
    unit_code: str,
    filename: str,
    content_type: str | None,
    content: bytes,
    uploaded_by: str,
) -> tuple[PortalStatementImport, bool]:
    unit_code = unit_code.zfill(3)
    if unit_code not in {"001", "003", "004", "005", "007", "008", "014", "050"}:
        raise PortalStatementError("A importação ainda não foi liberada para esta unidade")
    lower_name = filename.lower()
    if not lower_name.endswith((".xls", ".xlsx", ".pdf")):
        raise PortalStatementError("Envie um extrato .xls ou o relatorio de creditos .xlsx da Ipiranga")
    if not content or len(content) > MAX_PORTAL_BYTES:
        raise PortalStatementError("O arquivo está vazio ou excede o limite de 5 MB")
    digest = hashlib.sha256(content).hexdigest()
    existing_import = db.scalar(
        select(PortalStatementImport).where(PortalStatementImport.content_sha256 == digest)
    )
    if existing_import:
        return existing_import, True

    parsed = parse_ipiranga_statement(content, filename)
    expected_prefix = PORTAL_CNPJ_PREFIXES.get(unit_code)
    if expected_prefix and parsed.client_cnpj and parsed.client_cnpj[-12:] != expected_prefix:
        raise PortalStatementError(
            "O CNPJ abreviado do extrato não pertence à unidade selecionada. "
            "Revise o arquivo antes de importar."
        )
    # O portal Ipiranga também é utilizado pelas unidades Texaco. A
    # identificação vem da regra ativa, e não do código da unidade: a 007,
    # por exemplo, possui CNPJ de portal final 0006.
    texaco_rule = db.scalar(
        select(BonusRule).where(
            BonusRule.unit_code == unit_code,
            BonusRule.company_code == "TEXACO",
            BonusRule.kind == "invoice_discount",
            BonusRule.active.is_(True),
        )
    )
    company_code = "TEXACO" if texaco_rule else "IPIRANGA"
    import_row = PortalStatementImport(
        unit_code=unit_code,
        company_code=company_code,
        category=parsed.category_label[:40],
        client_cnpj=parsed.client_cnpj,
        period_start=parsed.period_start,
        period_end=parsed.period_end,
        original_filename=Path(filename).name[:255],
        content_type=(
            content_type
            or (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if lower_name.endswith(".xlsx")
                else "application/pdf"
                if lower_name.endswith(".pdf")
                else "application/vnd.ms-excel"
            )
        )[:120],
        content_sha256=digest,
        source_file=content,
        row_count=len(parsed.events),
        imported_count=0,
        duplicate_count=0,
        uploaded_by=uploaded_by,
    )
    db.add(import_row)
    db.flush()

    for parsed_event in parsed.events:
        key = _event_key(unit_code, company_code, parsed_event)
        event = db.scalar(select(PortalBonusEvent).where(PortalBonusEvent.event_key == key))
        if event:
            import_row.duplicate_count += 1
        else:
            event = PortalBonusEvent(
                unit_code=unit_code,
                company_code=company_code,
                category=parsed_event.category,
                portal_date=parsed_event.portal_date,
                value=money(parsed_event.value),
                product=parsed_event.product,
                description=parsed_event.description,
                reference=parsed_event.reference,
                client_cnpj=parsed.client_cnpj,
                business_classification=(
                    parsed_event.raw.get("business_classification")
                    if isinstance(parsed_event.raw, dict)
                    else None
                ),
                event_key=key,
            )
            db.add(event)
            db.flush()
            import_row.imported_count += 1
        source = db.scalar(
            select(PortalBonusEventSource).where(
                PortalBonusEventSource.import_id == import_row.id,
                PortalBonusEventSource.event_id == event.id,
            )
        )
        if not source:
            db.add(
                PortalBonusEventSource(
                    import_id=import_row.id,
                    event_id=event.id,
                    row_number=parsed_event.row_number,
                    raw_json=json.dumps(parsed_event.raw, ensure_ascii=False, default=str),
                )
            )
    db.flush()
    match_ipiranga_events(db, unit_code, company_code=company_code)
    return import_row, False


def _title_key(movement: PayableMovement) -> tuple:
    return (
        movement.unit_code,
        movement.person_id,
        movement.title_type,
        movement.document_id,
        movement.document_sequence,
    )


def _candidate_chain(db: Session, event: PortalBonusEvent, movement: PayableMovement) -> dict:
    documents = db.scalars(
        select(PayableDocument).where(
            PayableDocument.unit_code == movement.unit_code,
            PayableDocument.person_id == movement.person_id,
            PayableDocument.title_type == movement.title_type,
            PayableDocument.document_id == movement.document_id,
            PayableDocument.sequence == movement.document_sequence,
        )
    ).all()
    document = documents[0] if len(documents) == 1 else None
    purchase = db.get(Purchase, document.erp_entry_id) if document and document.erp_entry_id else None
    payments = db.scalars(
        select(PayableMovement).where(
            PayableMovement.unit_code == movement.unit_code,
            PayableMovement.person_id == movement.person_id,
            PayableMovement.title_type == movement.title_type,
            PayableMovement.document_id == movement.document_id,
            PayableMovement.document_sequence == movement.document_sequence,
            PayableMovement.movement_type == "B",
            PayableMovement.payment_sequence == movement.payment_sequence,
            PayableMovement.reversal_sequence == movement.reversal_sequence,
        )
    ).all()
    financial = []
    if movement.financial_launch_id:
        financial = db.scalars(
            select(FinancialEntry).where(
                FinancialEntry.erp_launch_id == movement.financial_launch_id,
                FinancialEntry.unit_code == movement.unit_code,
                FinancialEntry.document_id == movement.document_id,
                FinancialEntry.history_code == 6204,
            )
        ).all()
        financial = [row for row in financial if money(abs(Decimal(row.value))) == money(event.value)]
    exact = bool(
        len(documents) == 1
        and purchase
        and purchase.unit_code == event.unit_code
        and purchase.mapped_company_code == event.company_code
        and len(payments) == 1
        and len(financial) == 1
    )
    return {
        "exact": exact,
        "movement_key": movement.erp_key,
        "movement_date": movement.movement_date.isoformat(),
        "movement_value": str(money(abs(Decimal(movement.amount)))),
        "date_difference_days": abs((movement.movement_date - event.portal_date).days),
        "document_count": len(documents),
        "payable_document_id": document.id if document else None,
        "title_document": movement.document_id,
        "title_sequence": movement.document_sequence,
        "payment_count": len(payments),
        "payment_movement_key": payments[0].erp_key if len(payments) == 1 else None,
        "purchase_entry_id": purchase.erp_entry_id if purchase else None,
        "invoice_number": purchase.invoice_number if purchase else None,
        "invoice_issue_date": (
            (purchase.invoice_issue_date or purchase.purchase_date).isoformat() if purchase else None
        ),
        "purchase_entry_date": purchase.purchase_date.isoformat() if purchase else None,
        "supplier_name": purchase.supplier_name if purchase else None,
        "supplier_cnpj": purchase.supplier_cnpj if purchase else None,
        "financial_count": len(financial),
        "financial_entry_id": financial[0].id if len(financial) == 1 else None,
        "financial_launch_id": movement.financial_launch_id,
        "financial_history": financial[0].history_text if len(financial) == 1 else None,
    }


def _texaco_portal_candidates(
    db: Session, unit_code: str, company_code: str
) -> list[dict]:
    """Build documentary Texaco candidates, including titles not yet settled.

    The normal automatic reconciliation only consumes candidates with a
    complete payment chain.  The complete set exists so a portal statement can
    prove that a credit was *issued* for an unpaid title without falsely
    calling it a settled discount.
    """
    rule = db.scalar(
        select(BonusRule).where(
            BonusRule.unit_code == unit_code,
            BonusRule.company_code == company_code,
            BonusRule.kind == "invoice_discount",
            BonusRule.active.is_(True),
        )
    )
    if not rule:
        return []
    applies_to = (rule.applies_to or "all_fuel").strip().lower()
    codes = {
        code.strip()
        for code in applies_to.removeprefix("fuel_codes:").split(",")
        if code.strip()
    } if applies_to.startswith("fuel_codes:") else set()
    purchases = db.scalars(
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(
            Purchase.unit_code == unit_code,
            Purchase.mapped_company_code == company_code,
            Purchase.purchase_date >= rule.effective_from,
        )
    ).all()
    candidates = []
    for purchase in purchases:
        liters = (
            sum((Decimal(item.quantity or 0) for item in purchase.items if str(item.item_code).strip() in codes), ZERO)
            if codes
            else Decimal(purchase.total_liters or 0)
        )
        expected = money(liters * Decimal(rule.rate_per_liter))
        if expected <= ZERO:
            continue
        documents = db.scalars(
            select(PayableDocument).where(PayableDocument.erp_entry_id == purchase.erp_entry_id)
        ).all()
        if len(documents) != 1:
            continue
        document = documents[0]
        payments = db.scalars(
            select(PayableMovement).where(
                PayableMovement.unit_code == document.unit_code,
                PayableMovement.person_id == document.person_id,
                PayableMovement.title_type == document.title_type,
                PayableMovement.document_id == document.document_id,
                PayableMovement.document_sequence == document.sequence,
                PayableMovement.movement_type == "B",
                PayableMovement.reversal_sequence == 0,
            )
        ).all()
        payment = payments[0] if len(payments) == 1 else None
        financial = []
        if payment and payment.financial_launch_id:
            financial = [
                row
                for row in db.scalars(
                    select(FinancialEntry).where(
                        FinancialEntry.erp_launch_id == payment.financial_launch_id,
                        FinancialEntry.unit_code == document.unit_code,
                        FinancialEntry.document_id == document.document_id,
                        FinancialEntry.history_code == 51,
                    )
                ).all()
                if money(abs(Decimal(row.value))) == money(abs(Decimal(payment.amount)))
            ]
        payment_chain_exact = bool(payment and len(financial) == 1 and document.payment_date)
        discounts = db.scalars(
            select(PayableMovement).where(
                PayableMovement.unit_code == document.unit_code,
                PayableMovement.person_id == document.person_id,
                PayableMovement.title_type == document.title_type,
                PayableMovement.document_id == document.document_id,
                PayableMovement.document_sequence == document.sequence,
                PayableMovement.movement_type == "D",
                PayableMovement.reversal_sequence == 0,
            )
        ).all()
        candidates.append(
            {
                "candidate_key": str(purchase.erp_entry_id),
                "movement_key": payment.erp_key if payment else None,
                "movement_date": payment.movement_date.isoformat() if payment else None,
                "movement_value": str(money(abs(Decimal(payment.amount)))) if payment else None,
                "document_count": 1,
                "payable_document_id": document.id,
                "title_document": document.document_id,
                "title_sequence": document.sequence,
                "payment_count": len(payments),
                "payment_movement_key": payment.erp_key if payment else None,
                "purchase_entry_id": purchase.erp_entry_id,
                "invoice_number": purchase.invoice_number,
                "invoice_issue_date": (
                    (purchase.invoice_issue_date or purchase.purchase_date).isoformat()
                    if purchase.invoice_issue_date or purchase.purchase_date else None
                ),
                "purchase_entry_date": purchase.purchase_date.isoformat(),
                "supplier_name": purchase.supplier_name,
                "supplier_cnpj": purchase.supplier_cnpj,
                "financial_count": len(financial),
                "financial_entry_id": financial[0].id if len(financial) == 1 else None,
                "financial_launch_id": payment.financial_launch_id if payment else None,
                "financial_history": financial[0].history_text if len(financial) == 1 else None,
                "expected_for_purchase": str(expected),
                "expected_value": expected,
                "title_document_value": str(money(abs(Decimal(document.document_value or 0)))),
                "title_value": money(abs(Decimal(document.document_value or 0))),
                "title_due_date": document.due_date.isoformat() if document.due_date else None,
                "settlement_confirmed": payment_chain_exact,
                "portal_credit_type": "nota_propria",
                "usage_discount_total": str(
                    money(sum((abs(Decimal(row.amount)) for row in discounts), ZERO))
                ),
                "usage_discount_keys": [row.erp_key for row in discounts],
            }
        )
    return candidates


def _texaco_contract_candidates(
    db: Session, unit_code: str, company_code: str
) -> list[dict]:
    """Build only paid-title candidates eligible for automatic confirmation."""
    return [
        item
        for item in _texaco_portal_candidates(db, unit_code, company_code)
        if item["settlement_confirmed"]
    ]


def _texaco_event_candidates(event: PortalBonusEvent, candidates: list[dict]) -> list[dict]:
    """Return titles whose credit lifecycle includes the portal event date.

    The Texaco statement may issue a ``Nota Propria`` before the payable title
    is settled.  A candidate is therefore allowed from its fuel purchase date
    until the day after payment.  This remains a closed documentary window:
    every candidate has one title, one payment and one MLANF 51 posting.
    """
    result = []
    for candidate in candidates:
        purchase_date = date.fromisoformat(candidate["purchase_entry_date"])
        payment_date = date.fromisoformat(candidate["movement_date"])
        if not (purchase_date <= event.portal_date <= payment_date + timedelta(days=1)):
            continue
        if money(candidate["expected_value"]) > money(event.value):
            continue
        result.append(candidate)
    return result


def _portal_product_total(db: Session, event: PortalBonusEvent) -> Decimal | None:
    """Read the exact daily product-NF total preserved with the source PDF.

    Overlapping statements can provide the same event twice.  A product total
    is usable only when every source that exposes it agrees, which prevents a
    later export from silently changing the documentary anchor.
    """
    values = set()
    for source in db.scalars(
        select(PortalBonusEventSource).where(PortalBonusEventSource.event_id == event.id)
    ).all():
        try:
            raw = json.loads(source.raw_json or "{}")
            value = raw.get("portal_product_total") if isinstance(raw, dict) else None
            if value not in (None, ""):
                values.add(money(value))
        except (json.JSONDecodeError, InvalidOperation, TypeError, ValueError):
            continue
    return next(iter(values)) if len(values) == 1 and next(iter(values)) > ZERO else None


def _texaco_product_anchor_candidates(
    event: PortalBonusEvent, candidates: list[dict]
) -> list[dict]:
    """Limit a product-total anchor to the title lifecycle it can document."""
    result = []
    for candidate in candidates:
        purchase_date = date.fromisoformat(candidate["purchase_entry_date"])
        if purchase_date > event.portal_date:
            continue
        if candidate["settlement_confirmed"]:
            payment_date = date.fromisoformat(candidate["movement_date"])
            if event.portal_date > payment_date + timedelta(days=1):
                continue
        else:
            due_date = candidate.get("title_due_date")
            if not due_date or event.portal_date > date.fromisoformat(due_date):
                continue
        if money(candidate["title_value"]) <= ZERO:
            continue
        result.append(candidate)
    return result


def _texaco_exact_subsets(
    candidates: list[dict], target: Decimal, limit: int = 512, *, value_key: str = "expected_value"
) -> list[tuple[str, ...]]:
    """Find exact invoice groups for one consolidated portal credit."""
    ordered = sorted(candidates, key=lambda item: (money(item[value_key]), item["candidate_key"]))
    subsets: list[tuple[str, ...]] = []

    def visit(start: int, total: Decimal, selected: list[str]) -> None:
        if len(subsets) >= limit:
            return
        if total == target:
            subsets.append(tuple(selected))
            return
        if total > target:
            return
        for index in range(start, len(ordered)):
            candidate = ordered[index]
            visit(
                index + 1,
                money(total + money(candidate[value_key])),
                [*selected, candidate["candidate_key"]],
            )

    visit(0, ZERO, [])
    return subsets


def _texaco_group_components(event_rows: list[dict]) -> list[list[dict]]:
    """Partition temporal candidate overlaps before the exact-cover search."""
    remaining = {row["event"].id: row for row in event_rows}
    components = []
    while remaining:
        _, root = remaining.popitem()
        component = [root]
        candidate_keys = set(root["candidate_keys"])
        changed = True
        while changed:
            changed = False
            for event_id, row in list(remaining.items()):
                if candidate_keys.intersection(row["candidate_keys"]):
                    component.append(row)
                    candidate_keys.update(row["candidate_keys"])
                    remaining.pop(event_id)
                    changed = True
        components.append(component)
    return components


def _texaco_best_assignments(component: list[dict], limit: int = 512) -> tuple[Decimal, list[dict], bool]:
    """Select non-reused exact subsets and retain all best assignments.

    Retaining every equally good solution matters: a title is automatically
    confirmed only when it appears in every exact solution of its connected
    portal block.  This turns an apparent date/value ambiguity into a safe
    aggregate proof without guessing which daily line carried a same-value NF.
    """
    ordered = sorted(
        component,
        key=lambda row: (len(row["options"]), row["event"].portal_date, row["event"].id),
    )
    remaining_values = [ZERO] * (len(ordered) + 1)
    for index in range(len(ordered) - 1, -1, -1):
        remaining_values[index] = money(remaining_values[index + 1] + ordered[index]["event"].value)

    best_value = Decimal("-1")
    best_assignments: list[dict] = []
    overflow = False

    def visit(index: int, used: set[str], assignment: dict[str, tuple[str, ...] | None], value: Decimal) -> None:
        nonlocal best_value, best_assignments, overflow
        if value + remaining_values[index] < best_value:
            return
        if index == len(ordered):
            if value > best_value:
                best_value = value
                best_assignments = [dict(assignment)]
                overflow = False
            elif value == best_value:
                if len(best_assignments) < limit:
                    best_assignments.append(dict(assignment))
                else:
                    overflow = True
            return

        row = ordered[index]
        event_id = row["event"].id
        for option in row["options"]:
            option_keys = set(option)
            if option_keys.intersection(used):
                continue
            assignment[event_id] = option
            visit(
                index + 1,
                used | option_keys,
                assignment,
                money(value + row["event"].value),
            )
            assignment.pop(event_id, None)
        assignment[event_id] = None
        visit(index + 1, used, assignment, value)
        assignment.pop(event_id, None)

    visit(0, set(), {}, ZERO)
    return money(best_value), best_assignments, overflow


def _portal_match(db: Session, event_id: str) -> PortalBonusMatch:
    match = db.scalar(select(PortalBonusMatch).where(PortalBonusMatch.event_id == event_id))
    if not match:
        match = PortalBonusMatch(event_id=event_id)
        db.add(match)
    return match


def _match_texaco_portal_events(db: Session, events: list[PortalBonusEvent]) -> None:
    if not events:
        return
    unit_code = events[0].unit_code
    company_code = events[0].company_code
    # Primeiro tenta a âncora mais forte disponível: o próprio extrato
    # consolidado informa o total das Notas Fiscais de Produto do dia.  Quando
    # esse total fecha títulos únicos do ERP e a soma do crédito também fecha,
    # não há inferência por proximidade de data.
    all_candidates = _texaco_portal_candidates(db, unit_code, company_code)
    anchored_event_ids: set[str] = set()
    anchored_candidate_keys: set[str] = set()
    for event in sorted(events, key=lambda row: (row.portal_date, row.id)):
        product_total = _portal_product_total(db, event)
        if product_total is None:
            continue
        temporal = [
            candidate
            for candidate in _texaco_product_anchor_candidates(event, all_candidates)
            if candidate["candidate_key"] not in anchored_candidate_keys
        ]
        candidate_by_key = {candidate["candidate_key"]: candidate for candidate in temporal}
        options = _texaco_exact_subsets(temporal, product_total, value_key="title_value")
        options = [
            option
            for option in options
            if money(sum((money(candidate_by_key[key]["expected_value"]) for key in option), ZERO))
            == money(event.value)
        ]
        if len(options) != 1:
            continue
        selected = [candidate_by_key[key] for key in options[0]]
        if not selected:
            continue
        anchored_event_ids.add(event.id)
        anchored_candidate_keys.update(candidate["candidate_key"] for candidate in selected)
        all_settled = all(candidate["settlement_confirmed"] for candidate in selected)
        direct = (
            all_settled
            and len(selected) == 1
            and 0 <= (event.portal_date - date.fromisoformat(selected[0]["movement_date"])).days <= 1
        )
        if direct:
            status = "portal_exact"
            basis = (
                "Nota Própria e total de Nota Fiscal Produto do portal Texaco fecham exatamente "
                "uma NF paga, título e baixa ERP."
            )
        elif all_settled:
            status = "portal_group_exact"
            basis = (
                "Total de Notas Fiscais de Produto e Nota Própria do portal Texaco fecham exatamente "
                "o grupo de NFs pagas, sem reutilização de crédito."
            )
        else:
            status = "portal_issued_awaiting_payment"
            basis = (
                "O portal Texaco já emitiu o crédito e o total das Notas Fiscais de Produto fecha exatamente "
                "os títulos ERP; a parcela sem baixa continua aguardando pagamento."
            )
        allocations = [
            {
                **candidate,
                "allocated": str(money(candidate["expected_value"])),
                "allocation_invariant": True,
                "event_allocation_invariant": True,
                "portal_product_anchor_exact": True,
                "credit_issued": True,
                "settlement_confirmed": candidate["settlement_confirmed"],
            }
            for candidate in selected
        ]
        chosen = selected[0] if direct else None
        match = _portal_match(db, event.id)
        match.status = status
        match.movement_key = chosen.get("movement_key") if chosen else None
        match.payable_document_id = chosen.get("payable_document_id") if chosen else None
        match.purchase_entry_id = chosen.get("purchase_entry_id") if chosen else None
        match.financial_entry_id = chosen.get("financial_entry_id") if chosen else None
        match.date_difference_days = (
            abs((event.portal_date - date.fromisoformat(chosen["movement_date"])).days)
            if chosen else None
        )
        match.match_basis = basis
        match.details_json = json.dumps(
            {
                "chosen": chosen,
                "allocations": allocations,
                "candidates": temporal,
                "portal_product_total": str(product_total),
                "portal_product_anchor_exact": True,
                "anchor_solution_count": 1,
                "settlement_confirmed_count": sum(item["settlement_confirmed"] for item in selected),
                "awaiting_payment_count": sum(not item["settlement_confirmed"] for item in selected),
            },
            ensure_ascii=False,
            default=str,
        )
        match.algorithm_version = ALGORITHM_VERSION
        match.matched_at = datetime.now(timezone.utc)

    candidates = _texaco_contract_candidates(db, unit_code, company_code)
    candidates = [
        candidate for candidate in candidates if candidate["candidate_key"] not in anchored_candidate_keys
    ]
    candidate_by_key = {candidate["candidate_key"]: candidate for candidate in candidates}
    event_rows = []
    for event in sorted(events, key=lambda row: (row.portal_date, row.id)):
        if event.id in anchored_event_ids:
            continue
        temporal = _texaco_event_candidates(event, candidates)
        options = _texaco_exact_subsets(temporal, money(event.value))
        event_rows.append(
            {
                "event": event,
                "candidates": temporal,
                "candidate_keys": {candidate["candidate_key"] for candidate in temporal},
                "options": options,
            }
        )

    for component in _texaco_group_components(event_rows):
        _, solutions, overflow = _texaco_best_assignments(component)
        canonical = solutions[0] if solutions else {}
        solution_count = len(solutions)
        component_event_ids = [row["event"].id for row in component]
        candidate_sets = [
            {key for option in solution.values() if option for key in option}
            for solution in solutions
        ]
        invariant_keys = (
            set.intersection(*candidate_sets) if candidate_sets and not overflow else set()
        )

        for row in component:
            event = row["event"]
            selected = canonical.get(event.id)
            selected_candidates = [candidate_by_key[key] for key in selected or ()]
            allocations = []
            for candidate in selected_candidates:
                key = candidate["candidate_key"]
                same_event_in_all_solutions = bool(solutions) and all(
                    key in (solution.get(event.id) or ()) for solution in solutions
                )
                allocation = {
                    **candidate,
                    "allocated": str(money(candidate["expected_value"])),
                    "allocation_invariant": key in invariant_keys,
                    "event_allocation_invariant": same_event_in_all_solutions,
                }
                allocations.append(allocation)

            strict_direct = (
                len(selected_candidates) == 1
                and selected_candidates[0]["candidate_key"] in invariant_keys
                and all(
                    selected_candidates[0]["candidate_key"] in (solution.get(event.id) or ())
                    for solution in solutions
                )
                and money(selected_candidates[0]["expected_value"]) == money(event.value)
                and 0
                <= (event.portal_date - date.fromisoformat(selected_candidates[0]["movement_date"])).days
                <= 1
            )
            if strict_direct:
                status = "portal_exact"
                basis = (
                    "Nota Propria do portal Texaco fecha a formula contratual da NF e ocorre no mesmo dia "
                    "ou no dia seguinte ao pagamento, com titulo e MLANF 51 unicos."
                )
            elif selected_candidates and any(item["allocation_invariant"] for item in allocations):
                status = "portal_group_exact"
                basis = (
                    "Bloco de Notas Proprias do portal Texaco fecha por valor exato as NFs elegiveis, "
                    "com titulo, baixa e MLANF 51 unicos, sem reutilizacao de credito."
                )
            elif row["options"]:
                status = "ambiguous"
                basis = "O bloco possui mais de uma distribuicao exata; nenhuma NF foi confirmada automaticamente."
            else:
                status = "unmatched"
                basis = "Nenhum subconjunto de NFs pagas fecha exatamente o credito Nota Propria do portal Texaco."

            chosen = selected_candidates[0] if strict_direct else None
            match = _portal_match(db, event.id)
            match.status = status
            match.movement_key = chosen.get("movement_key") if chosen else None
            match.payable_document_id = chosen.get("payable_document_id") if chosen else None
            match.purchase_entry_id = chosen.get("purchase_entry_id") if chosen else None
            match.financial_entry_id = chosen.get("financial_entry_id") if chosen else None
            match.date_difference_days = (
                abs((event.portal_date - date.fromisoformat(chosen["movement_date"])).days)
                if chosen
                else None
            )
            match.match_basis = basis
            match.details_json = json.dumps(
                {
                    "chosen": chosen,
                    "allocations": allocations,
                    "candidates": row["candidates"],
                    "component_event_ids": component_event_ids,
                    "component_solution_count": solution_count,
                    "component_solution_overflow": overflow,
                },
                ensure_ascii=False,
                default=str,
            )
            match.algorithm_version = ALGORITHM_VERSION
            match.matched_at = datetime.now(timezone.utc)


def match_ipiranga_events(
    db: Session, unit_code: str = "001", *, company_code: str = "IPIRANGA"
) -> int:
    events = db.scalars(
        select(PortalBonusEvent).where(
            PortalBonusEvent.unit_code == unit_code.zfill(3),
            PortalBonusEvent.company_code == company_code,
        )
    ).all()
    texaco_events = [
        event
        for event in events
        if event.company_code == "TEXACO" and _normalized(event.description) == "nota propria"
    ]
    _match_texaco_portal_events(db, texaco_events)
    for event in events:
        if event in texaco_events:
            continue
        # O relatório de parcelas é uma confirmação primária de emissão: a
        # Ipiranga declara que o crédito daquele ciclo foi criado. O consumo
        # pode ocorrer meses depois e em mais de um título, logo a janela de
        # um dia usada para uma cadeia NF/título não é aplicável aqui.
        if event.business_classification == "postpaid_issued":
            match = _portal_match(db, event.id)
            match.status = "issued"
            match.movement_key = None
            match.payable_document_id = None
            match.purchase_entry_id = None
            match.financial_entry_id = None
            match.date_difference_days = None
            match.match_basis = (
                "Crédito emitido explicitamente no relatório de parcelas da Ipiranga; "
                "a utilização posterior é conciliada separadamente por título."
            )
            match.details_json = json.dumps(
                {"chosen": None, "candidates": []}, ensure_ascii=False
            )
            match.algorithm_version = ALGORITHM_VERSION
            match.matched_at = datetime.now(timezone.utc)
            continue
        movements = db.scalars(
            select(PayableMovement).where(
                PayableMovement.unit_code == event.unit_code,
                PayableMovement.movement_type == "D",
                PayableMovement.movement_date >= event.portal_date - timedelta(days=1),
                PayableMovement.movement_date <= event.portal_date + timedelta(days=1),
            )
        ).all()
        movements = [
            row for row in movements
            if money(abs(Decimal(row.amount))) == money(event.value)
        ]
        candidates = [_candidate_chain(db, event, movement) for movement in movements]
        exact = [candidate for candidate in candidates if candidate["exact"]]
        if len(exact) == 1:
            chosen = exact[0]
            status = "exact"
            basis = "Valor exato, data em até um dia, título único, baixa B, desconto D e MLANF 6204 único."
        elif len(exact) > 1:
            chosen = None
            status = "ambiguous"
            basis = "Mais de uma cadeia documental completa atende ao mesmo evento do portal."
        elif candidates:
            chosen = candidates[0] if len(candidates) == 1 else None
            status = "incomplete" if len(candidates) == 1 else "ambiguous"
            basis = "O valor foi localizado, mas a cadeia documental completa não é única."
        else:
            chosen = None
            status = "unmatched"
            basis = "Nenhum desconto MDCMP do mesmo valor foi localizado na janela exata de data."
        match = db.scalar(select(PortalBonusMatch).where(PortalBonusMatch.event_id == event.id))
        if not match:
            match = PortalBonusMatch(event_id=event.id)
            db.add(match)
        match.status = status
        match.movement_key = chosen.get("movement_key") if chosen else None
        match.payable_document_id = chosen.get("payable_document_id") if chosen else None
        match.purchase_entry_id = chosen.get("purchase_entry_id") if chosen else None
        match.financial_entry_id = chosen.get("financial_entry_id") if chosen else None
        match.date_difference_days = chosen.get("date_difference_days") if chosen else None
        match.match_basis = basis
        match.details_json = json.dumps(
            {"chosen": chosen, "candidates": candidates}, ensure_ascii=False, default=str
        )
        match.algorithm_version = ALGORITHM_VERSION
        match.matched_at = datetime.now(timezone.utc)
    db.flush()
    return len(events)


def _cycles(db: Session, rule: BonusRule, today: date) -> list[dict]:
    result = []
    cycle_start = rule.effective_from
    contractual_date = func.coalesce(Purchase.invoice_issue_date, Purchase.purchase_date)
    while True:
        cycle_end = add_months(cycle_start, 1) - timedelta(days=1)
        if cycle_end > today or (rule.effective_to and cycle_end > rule.effective_to):
            break
        purchases = db.scalars(
            select(Purchase)
            .where(
                Purchase.unit_code == rule.unit_code,
                Purchase.mapped_company_code == rule.company_code,
                contractual_date >= cycle_start,
                contractual_date <= cycle_end,
            )
            .order_by(contractual_date, Purchase.erp_entry_id)
        ).all()
        liters = sum((Decimal(row.total_liters or 0) for row in purchases), ZERO)
        reference_month = month_start(cycle_end)
        result.append(
            {
                "cycle_number": len(result) + 1,
                "period_start": cycle_start,
                "period_end": cycle_end,
                "reference_month": reference_month,
                "due_date": due_date_for_month(reference_month, rule.due_month_offset, rule.due_day),
                "liters": liters,
                "expected": money(liters * Decimal(rule.rate_per_liter)),
                "observed": ZERO,
                "allocations": [],
                "purchase_count": len(purchases),
            }
        )
        cycle_start = add_months(cycle_start, 1)
    return result


def _event_evidence(event: PortalBonusEvent, match: PortalBonusMatch | None, allocated: Decimal) -> dict:
    details = json.loads(match.details_json).get("chosen") if match and match.details_json else None
    details = details or {}
    issued_contract_parcel = event.business_classification == "postpaid_issued"
    return {
        "source": "IPIRANGA_PORTAL",
        "id": event.id,
        "date": event.portal_date.isoformat(),
        "value": float(money(event.value)),
        "source_value": float(money(event.value)),
        "allocated": float(money(allocated)),
        "document": details.get("title_document"),
        "portal_event_key": event.event_key,
        "match_status": match.status if match else ("portal_issued" if issued_contract_parcel else "unmatched"),
        "chain_exact": bool(match and match.status == "exact") or issued_contract_parcel,
        "portal_contract_parcel_issued": issued_contract_parcel,
        "movement_key": details.get("movement_key"),
        "payment_movement_key": details.get("payment_movement_key"),
        "purchase_entry_id": details.get("purchase_entry_id"),
        "invoice_number": details.get("invoice_number"),
        "invoice_issue_date": details.get("invoice_issue_date"),
        "financial_entry_id": details.get("financial_entry_id"),
        "financial_launch_id": details.get("financial_launch_id"),
        "match_basis": match.match_basis if match else "Evento do portal ainda sem cadeia ERP.",
    }


def build_ipiranga_cycle_ledger(db: Session, rule: BonusRule, today: date) -> dict:
    cycles = _cycles(db, rule, today)
    events = db.scalars(
        select(PortalBonusEvent)
        .where(
            PortalBonusEvent.unit_code == rule.unit_code,
            PortalBonusEvent.company_code == rule.company_code,
            PortalBonusEvent.category == POSTPAID_CATEGORY,
            PortalBonusEvent.portal_date <= today,
        )
        .order_by(PortalBonusEvent.portal_date, PortalBonusEvent.id)
    ).all()
    matches = {
        row.event_id: row
        for row in db.scalars(
            select(PortalBonusMatch).where(
                PortalBonusMatch.event_id.in_([event.id for event in events]) if events else False
            )
        ).all()
    }
    event_summaries = []
    for event in events:
        target_indexes = [
            index for index, cycle in enumerate(cycles) if cycle["period_end"] < event.portal_date
        ]
        remaining = money(event.value)
        allocations = []
        target_index = target_indexes[-1] if target_indexes else None
        if target_index is not None:
            target = cycles[target_index]
            deficit = max(ZERO, target["expected"] - target["observed"])
            amount = min(remaining, deficit)
            if amount > ZERO:
                target["observed"] += amount
                target["allocations"].append(_event_evidence(event, matches.get(event.id), amount))
                allocations.append((target_index, amount))
                remaining -= amount
            for index in range(target_index):
                if remaining <= ZERO:
                    break
                older = cycles[index]
                deficit = max(ZERO, older["expected"] - older["observed"])
                amount = min(remaining, deficit)
                if amount <= ZERO:
                    continue
                older["observed"] += amount
                older["allocations"].append(_event_evidence(event, matches.get(event.id), amount))
                allocations.append((index, amount))
                remaining -= amount
        event_summaries.append(
            {
                "event": event,
                "match": matches.get(event.id),
                "allocations": allocations,
                "unallocated": money(remaining),
            }
        )

    for cycle in cycles:
        cycle["observed"] = money(cycle["observed"])
        cycle["difference"] = money(cycle["expected"] - cycle["observed"])
        exact_chain = bool(cycle["allocations"]) and all(
            evidence.get("chain_exact") for evidence in cycle["allocations"]
        )
        cycle["confidence"] = "direct" if exact_chain else "probable" if cycle["observed"] else "none"
        cycle["evidence"] = [
            {
                "source": "calculation",
                "period_start": cycle["period_start"].isoformat(),
                "period_end": cycle["period_end"].isoformat(),
                "date_basis": "MDCHP.Dt_Emis",
                "allocated_liters": float(cycle["liters"]),
                "purchase_count": cycle["purchase_count"],
                "rate_per_liter": float(rule.rate_per_liter),
                "counted": False,
            },
            *cycle["allocations"],
        ]
    return {"cycles": cycles, "events": event_summaries}


def _contract_movements(db: Session, unit_code: str, company_code: str) -> list[tuple[PayableMovement, Purchase]]:
    purchases = db.scalars(
        select(Purchase).where(
            Purchase.unit_code == unit_code,
            Purchase.mapped_company_code == company_code,
        )
    ).all()
    entry_ids = [row.erp_entry_id for row in purchases]
    documents = db.scalars(
        select(PayableDocument).where(
            PayableDocument.unit_code == unit_code,
            PayableDocument.erp_entry_id.in_(entry_ids) if entry_ids else False,
        )
    ).all()
    title_to_entries: dict[tuple, set[int]] = defaultdict(set)
    for document in documents:
        title_to_entries[(
            document.unit_code,
            document.person_id,
            document.title_type,
            document.document_id,
            document.sequence,
        )].add(document.erp_entry_id)
    movements = db.scalars(
        select(PayableMovement).where(
            PayableMovement.unit_code == unit_code,
            PayableMovement.movement_type == "D",
        )
    ).all()
    purchase_by_id = {row.erp_entry_id: row for row in purchases}
    result = []
    for movement in movements:
        targets = title_to_entries.get(_title_key(movement), set())
        if len(targets) == 1 and next(iter(targets)) in purchase_by_id:
            result.append((movement, purchase_by_id[next(iter(targets))]))
    return result


def build_upfront_ledger(db: Session, unit_code: str = "001") -> dict:
    contract = db.scalar(
        select(Contract).where(
            Contract.unit_code == unit_code.zfill(3),
            Contract.company_code == "IPIRANGA",
        )
    )
    contracted = money(contract.upfront_total if contract else ZERO)
    matched_keys = {
        row.movement_key
        for row in db.scalars(
            select(PortalBonusMatch).where(
                PortalBonusMatch.status == "exact",
                PortalBonusMatch.movement_key.is_not(None),
            )
        ).all()
    }
    candidates = []
    for movement, purchase in _contract_movements(db, unit_code.zfill(3), "IPIRANGA"):
        if movement.movement_date < (contract.start_date if contract else date.min):
            continue
        if movement.erp_key in matched_keys:
            continue
        value = money(abs(Decimal(movement.amount)))
        candidates.append(
            {
                "movement_key": movement.erp_key,
                "date": movement.movement_date,
                "value": value,
                "document": movement.document_id,
                "purchase_entry_id": purchase.erp_entry_id,
                "invoice_number": purchase.invoice_number,
                "invoice_issue_date": purchase.invoice_issue_date or purchase.purchase_date,
                "supplier_name": purchase.supplier_name,
                "financial_launch_id": movement.financial_launch_id,
                "classification": "probable_upfront",
            }
        )
    candidates.sort(key=lambda row: (row["date"], row["movement_key"]))
    probable_used = money(sum((row["value"] for row in candidates), ZERO))
    return {
        "contracted": contracted,
        "probable_used": probable_used,
        "estimated_balance": money(contracted - probable_used),
        "candidates": candidates,
        "automatic": False,
        "reason": "O total contratual é conhecido, mas o ERP não identifica individualmente a origem antecipada.",
    }
