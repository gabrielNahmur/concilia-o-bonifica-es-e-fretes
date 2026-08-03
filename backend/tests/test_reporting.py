from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ReportDeliveryAttempt, ReportRun
from app.services import reporting


def test_director_email_is_limited_to_contract_progress(monkeypatch):
    monkeypatch.setattr(
        reporting,
        "_report_dashboard",
        lambda _db, _month: {
            "totals": {"month_liters": 180000, "contract_liters": 1200000, "remaining_liters": 3400000},
            "contracts": [{"unit_code": "001", "month_actual": 150000, "monthly_target": 145000, "status": "late", "projected_completion": date(2027, 2, 16)}],
        },
    )

    html = reporting._email_summary(object(), date(2026, 6, 1))

    assert "Compras nos contratos no mês" in html
    assert "Meta mensal conjunta" in html
    assert "Saldo a cumprir nos contratos" in html
    assert "Atrasado" in html
    assert "Bonificação" not in html
    assert "concilia" not in html.lower()


def test_delivery_monitor_marks_resend_delayed_and_delivered(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    class Response:
        def __init__(self, event): self.event = event
        def raise_for_status(self): return None
        def json(self): return {"last_event": self.event}

    monkeypatch.setattr(reporting, "get_settings", lambda: type("Settings", (), {"resend_api_key": "test-key"})())
    with Session(engine) as db:
        run = ReportRun(reference_month=date(2026, 6, 1), status="sent", attempts=1)
        db.add(run); db.flush()
        db.add(ReportDeliveryAttempt(report_run_id=run.id, attempt_number=1, provider_message_id="first", status="sent"))
        db.commit()

        monkeypatch.setattr(reporting.httpx, "get", lambda *_args, **_kwargs: Response("delivery_delayed"))
        result = reporting.monitor_report_deliveries(db)
        db.refresh(run)
        assert result["delayed"] == 1
        assert run.status == "delivery_delayed"

        monkeypatch.setattr(reporting.httpx, "get", lambda *_args, **_kwargs: Response("delivered"))
        result = reporting.monitor_report_deliveries(db)
        db.refresh(run)
        assert result["delivered"] == 1
        assert run.status == "delivered"
