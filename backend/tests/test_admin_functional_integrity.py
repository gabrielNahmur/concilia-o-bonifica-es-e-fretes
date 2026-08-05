from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import BonusRule, Company, Contract, Unit, User
from app.security import hash_password
from app.services.seed import seed_reference_data


PASSWORD = "SenhaSegura123!"
UNIT_CODE = "990"
COMPANY_CODE = "QA"


@pytest.fixture
def admin_client():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        db.execute(delete(Contract).where(Contract.unit_code == UNIT_CODE))
        db.execute(delete(BonusRule).where(BonusRule.unit_code == UNIT_CODE))
        if not db.get(Company, COMPANY_CODE):
            db.add(Company(code=COMPANY_CODE, display_name="Companhia QA"))
        if not db.get(Unit, UNIT_CODE):
            db.add(Unit(code=UNIT_CODE, display_name="Unidade QA", state="RS", active=True))
        user = db.scalar(select(User).where(User.email == "functional.qa@gbi.com"))
        if not user:
            db.add(
                User(
                    email="functional.qa@gbi.com",
                    full_name="QA Funcional",
                    role="admin",
                    active=True,
                    must_change_password=False,
                    password_hash=hash_password(PASSWORD),
                )
            )
        db.commit()
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "functional.qa@gbi.com", "password": PASSWORD},
        )
        assert response.status_code == 200
        yield client


def contract_payload(start_date: str, term_months: int, **changes):
    return {
        "unit_code": UNIT_CODE,
        "company_code": COMPANY_CODE,
        "start_date": start_date,
        "term_months": term_months,
        "total_liters": 100_000,
        "upfront_total": 0,
        "upfront_per_liter": 0,
        "postpaid_per_liter": 0.08,
        "status": "active",
    } | changes


def valid_rule_payload(kind: str = "distributor_credit"):
    payload = {
        "unit_code": UNIT_CODE,
        "company_code": COMPANY_CODE,
        "kind": kind,
        "effective_from": date(2026, 1, 1).isoformat(),
        "rate_per_liter": 0.08,
        "due_month_offset": 1,
        "applies_to": "all_fuel",
        "active": True,
    }
    if kind == "milestone_bonus":
        payload |= {
            "rate_per_liter": 0,
            "milestone_liters": 440_000,
            "milestone_amount": 35_000,
            "period_months": 4,
        }
    elif kind == "invoice_discount":
        payload |= {"rate_per_liter": 0.04, "applies_to": "fuel_codes:1,3,5"}
    elif kind == "s10_excess_credit":
        payload |= {
            "rate_per_liter": 0.10,
            "threshold_liters": 150_000,
            "applies_to": "s10",
        }
    elif kind == "bank_deposit":
        payload |= {"rate_per_liter": 0.05, "due_day": 20}
    return payload


def test_create_contract_rejects_overlapping_active_period(admin_client):
    first = admin_client.post("/api/admin/contracts", json=contract_payload("2026-01-01", 12))
    assert first.status_code == 200

    conflict = admin_client.post("/api/admin/contracts", json=contract_payload("2026-06-01", 12))

    assert conflict.status_code == 409
    assert "sobrepõe" in conflict.json()["detail"]


def test_create_contract_allows_period_after_existing_contract(admin_client):
    first = admin_client.post("/api/admin/contracts", json=contract_payload("2026-01-01", 6))
    after = admin_client.post("/api/admin/contracts", json=contract_payload("2026-07-02", 6))

    assert first.status_code == 200
    assert after.status_code == 200


def test_inactive_contract_does_not_block_new_active_contract(admin_client):
    inactive = admin_client.post(
        "/api/admin/contracts",
        json=contract_payload("2026-01-01", 12, status="inactive"),
    )
    active = admin_client.post("/api/admin/contracts", json=contract_payload("2026-01-01", 12))

    assert inactive.status_code == 200
    assert active.status_code == 200


def test_update_contract_ignores_itself_and_rejects_another_overlap(admin_client):
    first = admin_client.post("/api/admin/contracts", json=contract_payload("2026-01-01", 6))
    second = admin_client.post("/api/admin/contracts", json=contract_payload("2026-08-01", 6))
    assert first.status_code == 200
    assert second.status_code == 200

    unchanged = admin_client.patch(
        f"/api/admin/contracts/{first.json()['id']}",
        json=contract_payload("2026-01-01", 6),
    )
    conflict = admin_client.patch(
        f"/api/admin/contracts/{second.json()['id']}",
        json=contract_payload("2026-06-01", 6),
    )

    assert unchanged.status_code == 200
    assert conflict.status_code == 409


def test_contract_rejects_unknown_status(admin_client):
    response = admin_client.post(
        "/api/admin/contracts",
        json=contract_payload("2026-01-01", 12, status="arquivado"),
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "unknown"},
        {"applies_to": "qualquer_coisa"},
        {
            "kind": "milestone_bonus",
            "milestone_liters": None,
            "milestone_amount": 35_000,
            "period_months": 4,
        },
        {
            "kind": "s10_excess_credit",
            "rate_per_liter": 0.10,
            "threshold_liters": None,
            "applies_to": "s10",
        },
        {"kind": "invoice_discount", "rate_per_liter": 0},
        {"due_month_offset": -1},
    ],
)
def test_rule_rejects_invalid_domain(admin_client, change):
    response = admin_client.post(
        "/api/admin/rules",
        json=valid_rule_payload() | change,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "kind",
    [
        "distributor_credit",
        "milestone_bonus",
        "invoice_discount",
        "s10_excess_credit",
        "bank_deposit",
    ],
)
def test_rule_accepts_each_supported_kind(admin_client, kind):
    response = admin_client.post("/api/admin/rules", json=valid_rule_payload(kind))

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == kind
