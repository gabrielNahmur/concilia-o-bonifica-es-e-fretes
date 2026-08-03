from __future__ import annotations

import base64
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Purchase, ReportDeliveryAttempt, ReportRecipient, ReportRun, SyncRun
from app.services.analytics import get_dashboard
from app.services.rules import add_months, month_end, month_start


GREEN = colors.HexColor("#0B5B42")
TEXT = colors.HexColor("#26352F")
CONTRACT_STATUS_LABELS = {
    "on_track": "No ritmo",
    "late": "Atrasado",
    "ahead": "Adiantado",
    "completed": "Concluído",
}
DELIVERY_POLLABLE = {"sent", "delivery_delayed"}
DELIVERY_RETRYABLE = {"delivery_delayed", "failed"}
DELIVERY_TERMINAL = {"delivered", "bounced", "complained"}
MAX_DELIVERY_ATTEMPTS = 3
DELIVERY_RETRY_AFTER = timedelta(minutes=60)
logger = logging.getLogger(__name__)


def _br_number(value, digits=0) -> str:
    text = f"{float(value or 0):,.{digits}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def _table(rows, widths=None, header=True) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, -1), TEXT),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C8D8D1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, colors.HexColor("#F7FAF8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GREEN),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    table.setStyle(TableStyle(style))
    return table


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(GREEN)
    canvas.setLineWidth(1)
    canvas.line(15 * mm, 12 * mm, landscape(A4)[0] - 15 * mm, 12 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#66756F"))
    canvas.drawString(15 * mm, 7 * mm, "GBI Combustíveis - Acompanhamento de Contratos")
    canvas.drawRightString(landscape(A4)[0] - 15 * mm, 7 * mm, f"Página {doc.page}")
    canvas.restoreState()


def _report_dashboard(db: Session, reference_month: date) -> dict:
    """Build the directors' report only with data from the requested competence."""
    reference_month = month_start(reference_month)
    cutoff = min(date.today(), month_end(reference_month))
    return get_dashboard(db, reference_month, cutoff)


def _combined_monthly_target(contracts: list[dict]) -> float:
    return sum((row["monthly_target"] for row in contracts), 0)


def _contract_month_liters(contracts: list[dict]) -> float:
    return sum((row["month_actual"] for row in contracts), 0)


def _contract_history(db: Session, contracts: list[dict], reference_month: date) -> list[dict]:
    """Monthly purchases restricted to each unit's active contractual supplier."""
    start = add_months(month_start(reference_month), -11)
    contract_by_unit = {
        row["unit_code"]: (row["company_code"], row["start_date"])
        for row in contracts
    }
    history = {add_months(start, offset): 0 for offset in range(12)}
    purchases = db.scalars(
        select(Purchase).where(
            Purchase.purchase_date >= start,
            Purchase.purchase_date < add_months(month_start(reference_month), 1),
            Purchase.unit_code.in_(contract_by_unit),
        )
    ).all()
    for purchase in purchases:
        company_code, contract_start = contract_by_unit[purchase.unit_code]
        if purchase.mapped_company_code == company_code and purchase.purchase_date >= contract_start:
            history[month_start(purchase.purchase_date)] += float(purchase.total_liters)
    return [{"month": month, "liters": liters} for month, liters in history.items()]


def generate_monthly_pdf(db: Session, reference_month: date) -> Path:
    settings = get_settings()
    reference_month = month_start(reference_month)
    dashboard = _report_dashboard(db, reference_month)
    path = settings.reports_path / f"contratos-{reference_month:%Y-%m}.pdf"
    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=13 * mm,
        bottomMargin=17 * mm,
        title=f"Relatório de contratos - {reference_month:%m/%Y}",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="GBITitle", parent=styles["Title"], textColor=GREEN, fontSize=20, leading=23))
    styles.add(ParagraphStyle(name="GBIHeading", parent=styles["Heading2"], textColor=GREEN, spaceBefore=8, spaceAfter=7))
    styles.add(ParagraphStyle(name="CenterSmall", parent=styles["BodyText"], alignment=TA_CENTER, fontSize=8))
    story = []
    logo = Path(__file__).resolve().parents[2] / "frontend_dist" / "logo-gbi.png"
    if logo.exists():
        story.append(Image(str(logo), width=40 * mm, height=15 * mm, hAlign="LEFT"))
    story.extend(
        [
            Paragraph("Acompanhamento de Contratos de Combustíveis", styles["GBITitle"]),
            Paragraph(f"Compras e ritmo de cumprimento - {reference_month:%m/%Y}", styles["Heading3"]),
            Spacer(1, 5 * mm),
            Paragraph("Resumo executivo", styles["GBIHeading"]),
        ]
    )
    totals = dashboard["totals"]
    contracts = dashboard["contracts"]
    contract_month_liters = _contract_month_liters(contracts)
    monthly_target = _combined_monthly_target(contracts)
    month_percent = (contract_month_liters / monthly_target * 100) if monthly_target else 0
    story.append(
        _table(
            [
                ["Compras no mês", "Meta mensal conjunta", "% da meta", "Acumulado contratual", "Saldo contratual"],
                [
                    f"{_br_number(contract_month_liters)} L",
                    f"{_br_number(monthly_target)} L",
                    f"{_br_number(month_percent, 1)}%",
                    f"{_br_number(totals['contract_liters'])} L",
                    f"{_br_number(totals['remaining_liters'])} L",
                ],
            ],
            [48 * mm, 48 * mm, 48 * mm, 48 * mm, 48 * mm],
        )
    )
    story.extend([Spacer(1, 4 * mm), Paragraph("Situação contratual por unidade", styles["GBIHeading"])])
    contract_rows = [["Unidade", "Companhia", "Comprado", "Meta/mês", "% meta", "Média 3 meses", "Acumulado", "% contrato", "Saldo", "Conclusão projetada", "Situação"]]
    for row in contracts:
        completion = row["projected_completion"]
        contract_rows.append(
            [
                row["unit_code"],
                row["company_code"],
                f"{_br_number(row['month_actual'])} L",
                f"{_br_number(row['monthly_target'])} L",
                f"{_br_number(row['month_percent'], 1)}%",
                f"{_br_number(row['rolling_average'])} L",
                f"{_br_number(row['accumulated_liters'])} L",
                f"{_br_number(row['contract_percent'], 1)}%",
                f"{_br_number(row['remaining_liters'])} L",
                completion.strftime("%d/%m/%Y") if completion else "Sem projeção",
                CONTRACT_STATUS_LABELS.get(row["status"], row["status"]),
            ]
        )
    story.append(_table(contract_rows, [16 * mm, 18 * mm, 24 * mm, 24 * mm, 17 * mm, 25 * mm, 25 * mm, 19 * mm, 25 * mm, 31 * mm, 22 * mm]))
    story.extend([PageBreak(), Paragraph("Histórico consolidado de compras", styles["GBIHeading"])])
    history_rows = [["Competência", "Compras", "Meta mensal conjunta", "% da meta"]]
    for row in _contract_history(db, contracts, reference_month):
        history_rows.append(
            [
                f"{row['month']:%m/%Y}",
                f"{_br_number(row['liters'])} L",
                f"{_br_number(monthly_target)} L",
                f"{_br_number((row['liters'] / monthly_target * 100) if monthly_target else 0, 1)}%",
            ]
        )
    story.append(_table(history_rows, [48 * mm, 55 * mm, 55 * mm, 35 * mm]))
    story.extend(
        [
            Spacer(1, 2 * mm),
            Paragraph(
                "Os números refletem o último snapshot do ERP disponível na data de emissão. "
                "A projeção considera a média das compras dos três últimos meses completos.",
                styles["BodyText"],
            ),
        ]
    )
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return path


def latest_sync_is_fresh(db: Session, max_age_hours: int = 24) -> bool:
    latest = db.scalar(select(SyncRun).where(SyncRun.status == "success").order_by(SyncRun.finished_at.desc()).limit(1))
    if not latest or not latest.finished_at:
        return False
    finished = latest.finished_at
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - finished <= timedelta(hours=max_age_hours)


def _email_summary(db: Session, reference_month: date) -> str:
    dashboard = _report_dashboard(db, reference_month)
    totals = dashboard["totals"]
    contract_month_liters = _contract_month_liters(dashboard["contracts"])
    monthly_target = _combined_monthly_target(dashboard["contracts"])
    month_percent = (contract_month_liters / monthly_target * 100) if monthly_target else 0
    issues = [row for row in dashboard["contracts"] if row["status"] == "late"]
    items = "".join(
        f"<li>Unidade {row['unit_code']}: {CONTRACT_STATUS_LABELS[row['status']]} - "
        f"projeção de conclusão em {row['projected_completion'].strftime('%d/%m/%Y') if row['projected_completion'] else 'sem projeção'}</li>"
        for row in issues[:12]
    ) or "<li>Todos os contratos estão no ritmo esperado ou concluídos.</li>"
    return f"""
    <div style="font-family:Arial,sans-serif;color:#26352f;line-height:1.5">
      <h2 style="color:#0b5b42">Contratos GBI - {reference_month:%m/%Y}</h2>
      <p>Compras nos contratos no mês: <strong>{_br_number(contract_month_liters)} L</strong><br>
         Meta mensal conjunta: <strong>{_br_number(monthly_target)} L</strong><br>
         Atingimento da meta: <strong>{_br_number(month_percent, 1)}%</strong><br>
         Saldo a cumprir nos contratos: <strong>{_br_number(totals['remaining_liters'])} L</strong></p>
      <h3 style="color:#0b5b42">Unidades que merecem atenção</h3><ul>{items}</ul>
      <p>O relatório consolidado segue anexo.</p>
    </div>
    """


def _send_report_email(db: Session, run: ReportRun, path: Path, recipients: list[ReportRecipient]) -> ReportDeliveryAttempt:
    settings = get_settings()
    if not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY não configurada")
    now = datetime.now(timezone.utc)
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json"},
        json={
            "from": settings.resend_from_email,
            "to": [row.email for row in recipients],
            "subject": f"Acompanhamento de contratos GBI - {run.reference_month:%m/%Y}",
            "html": _email_summary(db, run.reference_month),
            "attachments": [{"filename": path.name, "content": base64.b64encode(path.read_bytes()).decode("ascii")}],
        },
        timeout=30,
    )
    response.raise_for_status()
    provider_message_id = response.json().get("id")
    if not provider_message_id:
        raise RuntimeError("Resend não retornou o identificador do e-mail")
    run.attempts = int(run.attempts or 0) + 1
    run.provider_message_id = provider_message_id
    run.status = "sent"
    run.sent_at = now
    run.error_message = None
    attempt = ReportDeliveryAttempt(
        report_run_id=run.id,
        attempt_number=run.attempts,
        provider_message_id=provider_message_id,
        status="sent",
        provider_event="sent",
        sent_at=now,
        checked_at=now,
        next_retry_at=now + DELIVERY_RETRY_AFTER,
    )
    db.add(attempt)
    return attempt


def create_and_optionally_send_report(db: Session, reference_month: date, send: bool = True) -> ReportRun:
    reference_month = month_start(reference_month)
    run = ReportRun(reference_month=reference_month, status="generating")
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        path = generate_monthly_pdf(db, reference_month)
        run.file_path = str(path)
        run.status = "generated"
        db.commit()
        if not send:
            return run
        if not latest_sync_is_fresh(db):
            raise RuntimeError("Envio bloqueado: a última sincronização válida tem mais de 24 horas")
        recipients = db.scalars(select(ReportRecipient).where(ReportRecipient.active.is_(True))).all()
        if not recipients:
            raise RuntimeError("Nenhum destinatário ativo configurado")
        _send_report_email(db, run, path, recipients)
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:4000]
    db.commit()
    db.refresh(run)
    return run


def previous_month(today: date | None = None) -> date:
    return add_months(month_start(today or date.today()), -1)


def _ensure_delivery_attempts(db: Session) -> None:
    """Backfill old sent reports so monitoring starts without losing their provider IDs."""
    existing_report_ids = set(db.scalars(select(ReportDeliveryAttempt.report_run_id)).all())
    runs = db.scalars(
        select(ReportRun).where(ReportRun.provider_message_id.is_not(None), ReportRun.status.notin_(DELIVERY_TERMINAL))
    ).all()
    for run in runs:
        if run.id in existing_report_ids:
            continue
        db.add(
            ReportDeliveryAttempt(
                report_run_id=run.id,
                attempt_number=max(int(run.attempts or 0), 1),
                provider_message_id=run.provider_message_id,
                status="sent",
                provider_event="legacy_sent",
                sent_at=run.sent_at or run.created_at,
            )
        )
    db.flush()


def _apply_delivery_event(db: Session, attempt: ReportDeliveryAttempt, event: str, error_message: str | None = None) -> None:
    attempt.status = event
    attempt.provider_event = event
    attempt.error_message = error_message
    attempt.checked_at = datetime.now(timezone.utc)
    run = db.get(ReportRun, attempt.report_run_id)
    if not run:
        return
    if event == "delivered":
        run.status = "delivered"
        run.error_message = None
    elif event == "delivery_delayed":
        run.status = "delivery_delayed"
        run.error_message = "Entrega atrasada no provedor; o sistema continuará monitorando e poderá reenviar."
    elif event in {"bounced", "complained"}:
        run.status = event
        run.error_message = error_message or "Entrega recusada pelo destinatário; reenvio automático bloqueado."
    elif event == "failed":
        run.status = "failed"
        run.error_message = error_message or "O provedor informou falha de entrega; haverá nova tentativa controlada."
    else:
        run.status = "sent"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def monitor_report_deliveries(db: Session) -> dict[str, int]:
    """Poll Resend and retry only a genuinely delayed/transient failed report.

    A delayed e-mail remains in Resend's own queue. We wait one hour before a
    new attempt and cap it at three sends, avoiding duplicates while retaining
    a recovery path for a real delivery failure.
    """
    _ensure_delivery_attempts(db)
    settings = get_settings()
    result = {"checked": 0, "delivered": 0, "delayed": 0, "retried": 0, "errors": 0}
    if not settings.resend_api_key:
        return result
    attempts = db.scalars(
        select(ReportDeliveryAttempt)
        .where(ReportDeliveryAttempt.status.in_(DELIVERY_POLLABLE))
        .order_by(ReportDeliveryAttempt.sent_at)
    ).all()
    for attempt in attempts:
        try:
            response = httpx.get(
                f"https://api.resend.com/emails/{attempt.provider_message_id}",
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            event = str(payload.get("last_event") or "sent").replace("email.", "")
            if event not in {"sent", "delivered", "delivery_delayed", "failed", "bounced", "complained"}:
                event = "sent"
            _apply_delivery_event(db, attempt, event, payload.get("error"))
            result["checked"] += 1
            if event == "delivered":
                result["delivered"] += 1
            elif event == "delivery_delayed":
                result["delayed"] += 1
        except Exception as exc:
            logger.warning("Não foi possível consultar entrega do relatório %s: %s", attempt.id, exc)
            result["errors"] += 1
    db.flush()

    now = datetime.now(timezone.utc)
    runs = db.scalars(
        select(ReportRun).where(ReportRun.status.in_(DELIVERY_RETRYABLE), ReportRun.attempts < MAX_DELIVERY_ATTEMPTS)
    ).all()
    for run in runs:
        latest = db.scalar(
            select(ReportDeliveryAttempt)
            .where(ReportDeliveryAttempt.report_run_id == run.id)
            .order_by(ReportDeliveryAttempt.attempt_number.desc())
            .limit(1)
        )
        if not latest or latest.status not in DELIVERY_RETRYABLE:
            continue
        eligible_at = _as_utc(latest.next_retry_at or (latest.sent_at + DELIVERY_RETRY_AFTER))
        if eligible_at > now:
            continue
        recipients = db.scalars(select(ReportRecipient).where(ReportRecipient.active.is_(True))).all()
        path = Path(run.file_path) if run.file_path else None
        if not recipients or not path or not path.exists():
            run.error_message = "Reenvio automático não realizado: destinatário ativo ou PDF indisponível."
            result["errors"] += 1
            continue
        try:
            _send_report_email(db, run, path, recipients)
            result["retried"] += 1
        except Exception as exc:
            run.error_message = f"Reenvio automático falhou: {exc}"[:4000]
            result["errors"] += 1
    db.commit()
    return result
