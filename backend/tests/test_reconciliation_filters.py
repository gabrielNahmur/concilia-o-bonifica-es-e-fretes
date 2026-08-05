from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import (
    BonusRule,
    Company,
    Reconciliation,
    ReconciliationException,
    Unit,
    User,
)
from app.security import hash_password


PASSWORD = "SenhaSegura123!"
QA_UNITS = ("992", "993")


@pytest.fixture
def client():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        reconciliation_ids = db.scalars(
            select(Reconciliation.id).where(Reconciliation.unit_code.in_(QA_UNITS))
        ).all()
        if reconciliation_ids:
            db.execute(
                delete(ReconciliationException).where(
                    ReconciliationException.reconciliation_id.in_(reconciliation_ids)
                )
            )
            db.execute(
                delete(Reconciliation).where(Reconciliation.id.in_(reconciliation_ids))
            )
        db.execute(delete(BonusRule).where(BonusRule.unit_code.in_(QA_UNITS)))
        if not db.get(Company, "QAFILTER"):
            db.add(Company(code="QAFILTER", display_name="Companhia Filtros QA"))
        for unit_code in QA_UNITS:
            if not db.get(Unit, unit_code):
                db.add(
                    Unit(
                        code=unit_code,
                        display_name=f"Unidade {unit_code} QA",
                        state="RS",
                        active=True,
                    )
                )
        user = db.scalar(
            select(User).where(User.email == "reconciliation.filters.qa@gbi.com")
        )
        if not user:
            db.add(
                User(
                    email="reconciliation.filters.qa@gbi.com",
                    full_name="QA Filtros",
                    role="viewer",
                    active=True,
                    must_change_password=False,
                    password_hash=hash_password(PASSWORD),
                )
            )
        db.commit()
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/auth/login",
            json={
                "email": "reconciliation.filters.qa@gbi.com",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 200
        yield test_client


def _rule(db, unit_code: str, kind: str) -> BonusRule:
    row = BonusRule(
        unit_code=unit_code,
        company_code="QAFILTER",
        kind=kind,
        effective_from=date(2035, 1, 1),
        rate_per_liter=Decimal("0.08"),
        due_month_offset=1,
        applies_to="all_fuel",
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def _reconciliation(
    db,
    rule: BonusRule,
    reference_month: date,
    *,
    status: str = "pending",
) -> Reconciliation:
    row = Reconciliation(
        unit_code=rule.unit_code,
        rule_id=rule.id,
        reference_month=reference_month,
        due_date=date(reference_month.year, reference_month.month, 28),
        expected_value=Decimal("100"),
        observed_value=Decimal("0"),
        manual_adjustment=Decimal("0"),
        difference_value=Decimal("100"),
        status=status,
        confidence="none",
        evidence_json="[]",
    )
    db.add(row)
    db.flush()
    return row


def _exception(
    db,
    reconciliation: Reconciliation,
    source_key: str,
    *,
    severity: str = "high",
    exception_type: str = "qa_filter",
) -> ReconciliationException:
    row = ReconciliationException(
        reconciliation_id=reconciliation.id,
        source_key=source_key,
        exception_type=exception_type,
        severity=severity,
        status="open",
        title="Exceção de teste",
        description="Exceção aberta para validar o filtro.",
        expected_value=Decimal("100"),
        observed_value=Decimal("0"),
        difference_value=Decimal("100"),
    )
    db.add(row)
    return row


def test_reconciliation_summary_counts_only_exceptions_from_filtered_unit(client):
    with SessionLocal() as db:
        rule_992 = _rule(db, "992", "distributor_credit")
        rule_993 = _rule(db, "993", "distributor_credit")
        _reconciliation(db, rule_992, date(2035, 1, 1))
        row_993 = _reconciliation(db, rule_993, date(2035, 1, 1))
        _exception(db, row_993, "qa-unit-993")
        db.commit()

    response = client.get("/api/reconciliations", params={"unit": "992"})

    assert response.status_code == 200
    assert response.json()["summary"]["open_exceptions"] == 0


def test_reconciliation_summary_applies_reference_month_filter(client):
    with SessionLocal() as db:
        rule = _rule(db, "992", "distributor_credit")
        _reconciliation(db, rule, date(2035, 1, 1))
        february = _reconciliation(db, rule, date(2035, 2, 1))
        _exception(db, february, "qa-february")
        db.commit()

    response = client.get(
        "/api/reconciliations",
        params={"unit": "992", "reference_month": "2035-01-01"},
    )

    assert response.status_code == 200
    assert response.json()["summary"]["open_exceptions"] == 0


def test_reconciliation_summary_applies_rule_kind_filter(client):
    with SessionLocal() as db:
        distributor = _rule(db, "992", "distributor_credit")
        bank = _rule(db, "992", "bank_deposit")
        _reconciliation(db, distributor, date(2035, 1, 1))
        bank_row = _reconciliation(db, bank, date(2035, 1, 1))
        _exception(db, bank_row, "qa-bank")
        db.commit()

    response = client.get(
        "/api/reconciliations",
        params={"unit": "992", "rule_kind": "distributor_credit"},
    )

    assert response.status_code == 200
    assert response.json()["summary"]["open_exceptions"] == 0


def test_exception_queue_orders_business_severity(client):
    with SessionLocal() as db:
        rule = _rule(db, "992", "distributor_credit")
        reconciliation = _reconciliation(db, rule, date(2035, 1, 1))
        for severity in ("low", "medium", "high", "critical"):
            _exception(
                db,
                reconciliation,
                f"qa-severity-{severity}",
                severity=severity,
                exception_type="qa_severity",
            )
        db.commit()

    response = client.get(
        "/api/reconciliations/exceptions",
        params={"unit": "992", "exception_type": "qa_severity"},
    )

    assert response.status_code == 200
    assert [item["severity"] for item in response.json()["items"]] == [
        "critical",
        "high",
        "medium",
        "low",
    ]
