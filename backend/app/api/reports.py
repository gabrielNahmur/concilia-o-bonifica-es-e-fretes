from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.dependencies import AdminUser, CurrentUser, DbSession
from app.models import ReportRun
from app.services.audit import audit
from app.services.reporting import create_and_optionally_send_report, previous_month


router = APIRouter(prefix="/reports", tags=["relatórios"])


class MonthlyReportInput(BaseModel):
    reference_month: date | None = None
    send: bool = False


def _payload(row: ReportRun) -> dict:
    return {
        "id": row.id,
        "reference_month": row.reference_month,
        "status": row.status,
        "attempts": row.attempts,
        "provider_message_id": row.provider_message_id,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "sent_at": row.sent_at,
        "has_file": bool(row.file_path and Path(row.file_path).exists()),
    }


@router.get("/monthly")
def monthly_reports(db: DbSession, _: CurrentUser):
    rows = db.scalars(select(ReportRun).order_by(ReportRun.created_at.desc()).limit(100)).all()
    return [_payload(row) for row in rows]


@router.post("/monthly")
def generate_monthly(payload: MonthlyReportInput, db: DbSession, user: AdminUser):
    reference = payload.reference_month or previous_month()
    run = create_and_optionally_send_report(db, reference, payload.send)
    audit(db, user, "send" if payload.send else "generate", "report", run.id, {"reference_month": str(reference)})
    db.commit()
    return _payload(run)


@router.get("/{report_id}/download")
def download_report(report_id: str, db: DbSession, _: CurrentUser):
    row = db.get(ReportRun, report_id)
    if not row or not row.file_path or not Path(row.file_path).exists():
        raise HTTPException(status_code=404, detail="Arquivo de relatório não encontrado")
    return FileResponse(row.file_path, media_type="application/pdf", filename=Path(row.file_path).name)
