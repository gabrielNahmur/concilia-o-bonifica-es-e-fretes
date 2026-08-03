from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import PayableDocument, PayableMovement, Purchase, PurchaseItem, User
from app.security import hash_password


def _purchase(entry_id: int, unit: str, liters: str, issue: date) -> Purchase:
    return Purchase(
        erp_entry_id=entry_id,
        unit_code=unit,
        supplier_person_id=9001,
        supplier_name="DISTRIBUIDORA DE TESTE",
        supplier_cnpj="12345678000190",
        mapped_company_code="IPIRANGA",
        invoice_number=str(entry_id)[-7:],
        invoice_series="1",
        access_key=str(entry_id).zfill(44),
        purchase_date=issue,
        invoice_issue_date=issue,
        total_liters=Decimal(liters),
        s10_liters=Decimal("100"),
        gross_value=Decimal("5000"),
        net_value=Decimal("4900"),
    )


def test_purchases_accept_multiple_filters_sort_and_expose_full_note_detail():
    Base.metadata.create_all(engine)
    entries = (981001, 981002, 981003)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "purchases.viewer@gbi.com"))
        if not user:
            user = User(
                email="purchases.viewer@gbi.com",
                full_name="Consulta de compras",
                role="viewer",
                active=True,
                must_change_password=False,
                password_hash=hash_password("SenhaSegura123!"),
            )
            db.add(user)
        if not db.get(Purchase, entries[0]):
            first = _purchase(entries[0], "001", "2000", date(2026, 6, 5))
            second = _purchase(entries[1], "002", "1000", date(2026, 7, 5))
            outside = _purchase(entries[2], "003", "500", date(2026, 7, 5))
            db.add_all((first, second, outside))
            db.flush()
            db.add_all(
                (
                    PurchaseItem(
                        erp_entry_id=first.erp_entry_id,
                        item_code="2",
                        sequence=2,
                        description="DIESEL S10",
                        unit="L",
                        quantity=Decimal("100"),
                        unit_value=Decimal("5"),
                        total_value=Decimal("500"),
                    ),
                    PurchaseItem(
                        erp_entry_id=first.erp_entry_id,
                        item_code="1",
                        sequence=1,
                        description="GASOLINA",
                        unit="L",
                        quantity=Decimal("1900"),
                        unit_value=Decimal("4.75"),
                        total_value=Decimal("9025"),
                    ),
                    PayableDocument(
                        unit_code="001",
                        person_id=9001,
                        title_type="NF",
                        document_id="981001",
                        sequence="01",
                        erp_entry_id=first.erp_entry_id,
                        invoice_number=first.invoice_number,
                        document_value=Decimal("5000"),
                        other_discount=Decimal("100"),
                        issue_date=date(2026, 6, 5),
                        due_date=date(2026, 6, 12),
                        payment_date=date(2026, 6, 12),
                        balance=Decimal("0"),
                    ),
                    PayableMovement(
                        erp_key="purchase-test-movement:981001",
                        unit_code="001",
                        person_id=9001,
                        title_type="NF",
                        document_id="981001",
                        document_sequence="01",
                        movement_type="PG",
                        movement_date=date(2026, 6, 12),
                        reference_date=date(2026, 6, 5),
                        amount=Decimal("4900"),
                        payment_sequence=1,
                        reversal_sequence=0,
                        financial_launch_id=12345,
                        financial_sequence=1,
                        batch_id=10,
                        payment_method="BOL",
                        operation_id="PGTO",
                        center_code="001",
                        notes="Baixa do boleto de teste",
                    ),
                )
            )
        db.commit()

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login",
            json={"email": "purchases.viewer@gbi.com", "password": "SenhaSegura123!"},
        ).status_code == 200
        response = client.get(
            "/api/purchases",
            params=[
                ("unit", "001,002"),
                ("reference_month", "2026-06,2026-07"),
                ("sort_by", "liters"),
                ("sort_dir", "asc"),
                ("page_size", "200"),
            ],
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        filtered = [row for row in payload["items"] if row["erp_entry_id"] in entries]
        assert [row["erp_entry_id"] for row in filtered] == [entries[1], entries[0]]
        assert payload["sort_by"] == "liters"
        assert payload["sort_dir"] == "asc"

        detail = client.get(f"/api/purchases/{entries[0]}")
        assert detail.status_code == 200, detail.text
        note = detail.json()
        assert note["supplier_person_id"] == 9001
        assert [item["sequence"] for item in note["items"]] == [1, 2]
        assert note["payables"][0]["other_discount"] == 100.0
        assert note["payables"][0]["movements"][0]["financial_launch_id"] == 12345
        assert client.get("/api/purchases?sort_by=unknown").status_code == 422
