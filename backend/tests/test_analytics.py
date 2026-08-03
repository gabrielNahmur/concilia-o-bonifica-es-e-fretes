import json
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import BonusRule, PayableDocument, Purchase, PurchaseItem, Reconciliation, User
from app.security import hash_password
from app.services.analytics import get_dashboard
from app.services.seed import seed_reference_data


def _viewer(db):
    user = db.scalar(select(User).where(User.email == "analytics.viewer@gbi.com"))
    if not user:
        user = User(
            email="analytics.viewer@gbi.com",
            full_name="Consulta de métricas",
            role="viewer",
            active=True,
            must_change_password=False,
            password_hash=hash_password("SenhaSegura123!"),
        )
        db.add(user)
    return user


def test_bonus_card_detail_exposes_orders_titles_and_indefinite_agreement():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        _viewer(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.kind == "dashboard_detail_test"))
        if not rule:
            rule = BonusRule(
                unit_code="013",
                company_code="IPIRANGA",
                kind="dashboard_detail_test",
                effective_from=date(2026, 7, 1),
                rate_per_liter=Decimal("0.05"),
                due_month_offset=1,
                applies_to="all_fuel",
                active=True,
            )
            db.add(rule)
            db.flush()
        purchase = db.get(Purchase, 998001)
        if not purchase:
            purchase = Purchase(
                erp_entry_id=998001,
                unit_code="013",
                mapped_company_code="IPIRANGA",
                supplier_name="Fornecedor de teste",
                purchase_date=date(2026, 7, 8),
                invoice_issue_date=date(2026, 7, 8),
                invoice_number="NF-998001",
                total_liters=Decimal("1000"),
                gross_value=Decimal("6000"),
                net_value=Decimal("5950"),
            )
            db.add(purchase)
            db.add(
                PurchaseItem(
                    erp_entry_id=998001,
                    item_code="1",
                    sequence=1,
                    unit="LT",
                    quantity=Decimal("1000"),
                    unit_value=Decimal("6"),
                    total_value=Decimal("6000"),
                )
            )
        document = db.scalar(select(PayableDocument).where(PayableDocument.document_id == "TIT-998001"))
        if not document:
            db.add(
                PayableDocument(
                    unit_code="013",
                    person_id=998001,
                    title_type="NF",
                    document_id="TIT-998001",
                    sequence="1",
                    erp_entry_id=998001,
                    invoice_number="NF-998001",
                    document_value=Decimal("5950"),
                    other_discount=Decimal("50"),
                    due_date=date(2026, 7, 20),
                    payment_date=date(2026, 7, 19),
                )
            )
        reconciliation = db.scalar(
            select(Reconciliation).where(
                Reconciliation.rule_id == rule.id,
                Reconciliation.reference_month == date(2026, 7, 1),
            )
        )
        if not reconciliation:
            reconciliation = Reconciliation(
                unit_code="013",
                rule_id=rule.id,
                reference_month=date(2026, 7, 1),
                due_date=date(2026, 8, 31),
                expected_value=Decimal("50"),
                observed_value=Decimal("50"),
                difference_value=Decimal("0"),
                status="confirmed",
                confidence="direct",
                evidence_json=json.dumps(
                    [
                        {
                            "source": "calculation",
                            "period_start": "2026-07-01",
                            "period_end": "2026-07-31",
                        },
                        {
                            "source": "MDCMP",
                            "date": "2026-07-19",
                            "document": "TIT-998001",
                            "value": 50,
                            "history": "Desconto nativo confirmado",
                        },
                    ]
                ),
            )
            db.add(reconciliation)
        db.commit()

        dashboard = get_dashboard(db, date(2026, 7, 1), date(2026, 7, 30))
        indefinite = next(row for row in dashboard["indefinite_contracts"] if row["unit_code"] == "013")
        assert indefinite["contract_type"] == "indefinite"
        assert indefinite["contract_label"] == "Contrato por tempo indeterminado"
        assert indefinite["monthly_target"] is None
        assert indefinite["bonus_expected"] == 50.0

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login",
            json={"email": "analytics.viewer@gbi.com", "password": "SenhaSegura123!"},
        ).status_code == 200
        response = client.get("/api/dashboard/bonus-details?reference_month=2026-07-01&unit=013")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["totals"] == {
            "expected_value": 50.0,
            "identified_value": 50.0,
            "difference_value": 0.0,
            "items": 1,
        }
        item = payload["items"][0]
        assert item["unit"]["code"] == "013"
        assert item["purchase_summary"]["eligible_liters"] == 1000.0
        assert item["purchases"][0]["invoice_number"] == "NF-998001"
        assert item["purchases"][0]["titles"][0]["document_id"] == "TIT-998001"
        assert item["evidence"][0]["source"] == "MDCMP"

        unit_detail = client.get("/api/units/013?as_of=2026-07-30")
        assert unit_detail.status_code == 200
        assert unit_detail.json()["contract"]["contract_type"] == "indefinite"
