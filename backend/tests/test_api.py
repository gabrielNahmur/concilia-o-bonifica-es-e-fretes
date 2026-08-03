from fastapi.testclient import TestClient
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import (
    BonusRule,
    PortalBonusEvent,
    Reconciliation,
    ReconciliationException,
    ReconciliationItem,
    ReconciliationReview,
    User,
)
from app.security import hash_password
from app.services.seed import seed_reference_data


def test_login_dashboard_and_viewer_permissions():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.email == "viewer@gbi.com")):
            db.add(
                User(
                    email="viewer@gbi.com",
                    full_name="Usuário de Teste",
                    role="viewer",
                    active=True,
                    must_change_password=False,
                    password_hash=hash_password("SenhaSegura123!"),
                )
            )
            db.commit()
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"email": "viewer@gbi.com", "password": "SenhaSegura123!"})
        assert login.status_code == 200
        assert login.json()["role"] == "viewer"
        assert client.get("/api/dashboard").status_code == 200
        assert client.post("/api/admin/sync").status_code == 403
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_reconciliation_coverage_exposes_strict_internal_policy():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "coverage.viewer@gbi.com"))
        if not user:
            db.add(
                User(
                    email="coverage.viewer@gbi.com",
                    full_name="Consulta de Cobertura",
                    role="viewer",
                    active=True,
                    must_change_password=False,
                    password_hash=hash_password("SenhaSegura123!"),
                )
            )
            db.commit()
    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login",
            json={"email": "coverage.viewer@gbi.com", "password": "SenhaSegura123!"},
        ).status_code == 200
        response = client.get("/api/reconciliations/coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["policy"]["external_documents_counted"] is False
        assert "automatic_integrity_violations" in payload["summary"]
        assert "labels" in payload["summary"]


def test_work_queue_is_deduplicated_and_hides_confirmed_by_default():
    """The operational queue is one row per invoice, not one row per exception."""
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        user = db.scalar(select(User).where(User.email == "queue.viewer@gbi.com"))
        if not user:
            user = User(
                email="queue.viewer@gbi.com",
                full_name="Consulta da fila",
                role="viewer",
                active=True,
                must_change_password=False,
                password_hash=hash_password("SenhaSegura123!"),
            )
            db.add(user)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount")
        )
        actionable = Reconciliation(
            unit_code="005",
            rule_id=rule.id,
            reference_month=date(2031, 1, 1),
            due_date=date(2031, 2, 10),
            expected_value=Decimal("600"),
            observed_value=Decimal("0"),
            manual_adjustment=Decimal("0"),
            difference_value=Decimal("600"),
            status="divergent",
            confidence="none",
            evidence_json="[]",
        )
        confirmed = Reconciliation(
            unit_code="005",
            rule_id=rule.id,
            reference_month=date(2031, 2, 1),
            due_date=date(2031, 3, 10),
            expected_value=Decimal("200"),
            observed_value=Decimal("200"),
            manual_adjustment=Decimal("0"),
            difference_value=Decimal("0"),
            status="confirmed",
            confidence="direct",
            confirmation_mode="manual",
            evidence_json="[]",
        )
        db.add_all((actionable, confirmed))
        db.flush()
        item = ReconciliationItem(
            reconciliation_id=actionable.id,
            item_type="invoice",
            source_key="queue:invoice:2031-01",
            source_date=date(2031, 1, 15),
            source_document="NF-2031-01",
            description="NF Texaco com desconto pendente",
            expected_value=Decimal("600"),
            observed_value=Decimal("0"),
            difference_value=Decimal("600"),
            status="divergent",
            confidence="none",
            automatic_eligible=False,
            review_status="pending",
            policy_reason="Desconto não identificado.",
            details_json='{"decision_code":"paid_without_discount"}',
            fingerprint="q" * 64,
        )
        db.add(item)
        db.flush()
        db.add_all(
            (
                ReconciliationException(
                    reconciliation_id=actionable.id,
                    item_id=item.id,
                    source_key="queue:invoice:2031-01:discount",
                    exception_type="paid_without_discount",
                    severity="high",
                    status="open",
                    title="Desconto não localizado",
                    description="A baixa não possui o desconto esperado.",
                    expected_value=Decimal("600"),
                    observed_value=Decimal("0"),
                    difference_value=Decimal("600"),
                ),
                ReconciliationException(
                    reconciliation_id=actionable.id,
                    item_id=item.id,
                    source_key="queue:invoice:2031-01:chain",
                    exception_type="incomplete_payment_chain",
                    severity="medium",
                    status="open",
                    title="Cadeia financeira incompleta",
                    description="Falta conferir o lançamento financeiro.",
                    expected_value=Decimal("600"),
                    observed_value=Decimal("0"),
                    difference_value=Decimal("600"),
                ),
            )
        )
        db.commit()

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login", json={"email": "queue.viewer@gbi.com", "password": "SenhaSegura123!"}
        ).status_code == 200
        response = client.get("/api/reconciliations/work-queue?unit=005")
        assert response.status_code == 200
        payload = response.json()
        rows = [row for row in payload["items"] if row["document"] == "NF-2031-01"]
        assert len(rows) == 1
        assert rows[0]["open_exception_count"] == 2
        assert rows[0]["action_label"] == "Cobrar desconto"
        assert all(row["status"] != "confirmed" for row in payload["items"])

        history = client.get("/api/reconciliations/work-queue?unit=005&scope=confirmed")
        assert history.status_code == 200
        assert any(row["reference_month"] == "2031-02-01" for row in history.json()["items"])


def test_admin_confirmation_creates_immutable_review_snapshot():
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.email == "review.admin@gbi.com"))
            if not admin:
                admin = User(
                    email="review.admin@gbi.com", full_name="Admin Revisor", role="admin", active=True,
                    must_change_password=False, password_hash=hash_password("SenhaSegura123!"),
                )
                db.add(admin); db.commit()
            rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount"))
            row = db.scalar(select(Reconciliation).where(Reconciliation.rule_id == rule.id, Reconciliation.reference_month == date(2026, 1, 1)))
            if not row:
                row = Reconciliation(
                    unit_code="005", rule_id=rule.id, reference_month=date(2026, 1, 1), due_date=date(2026, 1, 31),
                    expected_value=Decimal("100"), observed_value=Decimal("100"), manual_adjustment=Decimal("0"),
                    difference_value=Decimal("0"), status="pending", confidence="direct", evidence_json="[]",
                )
                db.add(row); db.commit()
            row_id = row.id
        login = client.post("/api/auth/login", json={"email": "review.admin@gbi.com", "password": "SenhaSegura123!"})
        assert login.status_code == 200
        response = client.post(f"/api/reconciliations/{row_id}/confirm", json={"notes": "Documentos revisados"})
        assert response.status_code == 200
        detail = client.get(f"/api/reconciliations/{row_id}/detail")
        assert detail.status_code == 200
        history = detail.json()["review"]["history"]
        assert len(history) >= 1
        snapshot = client.get(f"/api/reconciliations/{row_id}/reviews/{history[0]['id']}")
        assert snapshot.status_code == 200
        assert snapshot.json()["expected_value"] == 100.0
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(ReconciliationReview).where(ReconciliationReview.reconciliation_id == row_id)) >= 1


def test_admin_can_review_an_exact_item_from_exception_queue():
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.email == "granular.admin@gbi.com"))
            if not admin:
                admin = User(
                    email="granular.admin@gbi.com",
                    full_name="Admin Granular",
                    role="admin",
                    active=True,
                    must_change_password=False,
                    password_hash=hash_password("SenhaSegura123!"),
                )
                db.add(admin)
            rule = db.scalar(
                select(BonusRule).where(BonusRule.unit_code == "003", BonusRule.kind == "distributor_credit")
            )
            row = Reconciliation(
                unit_code="003",
                rule_id=rule.id,
                reference_month=date(2026, 2, 1),
                due_date=date(2026, 3, 31),
                expected_value=Decimal("600"),
                observed_value=Decimal("600"),
                manual_adjustment=Decimal("0"),
                difference_value=Decimal("0"),
                status="partial",
                confidence="probable",
                evidence_json="[]",
            )
            db.add(row)
            db.flush()
            item = ReconciliationItem(
                reconciliation_id=row.id,
                item_type="distributor_credit",
                source_key="test:granular-credit",
                source_date=date(2026, 2, 1),
                description="Crédito de teste",
                expected_value=Decimal("600"),
                observed_value=Decimal("600"),
                difference_value=Decimal("0"),
                status="review_required",
                confidence="probable",
                automatic_eligible=False,
                review_status="pending",
                policy_reason="Uso comprovado; geração depende de revisão.",
                details_json="{}",
                fingerprint="a" * 64,
            )
            db.add(item)
            db.flush()
            exception = ReconciliationException(
                reconciliation_id=row.id,
                item_id=item.id,
                source_key="test:granular-credit:origin",
                exception_type="origin_not_proven",
                severity="medium",
                status="open",
                title="Origem não comprovada",
                description="Revisar comprovante",
                expected_value=Decimal("600"),
                observed_value=Decimal("600"),
                difference_value=Decimal("0"),
            )
            db.add(exception)
            db.commit()
            item_id = item.id
            row_id = row.id

        login = client.post(
            "/api/auth/login",
            json={"email": "granular.admin@gbi.com", "password": "SenhaSegura123!"},
        )
        assert login.status_code == 200
        queue = client.get("/api/reconciliations/exceptions?status=open&unit=003")
        assert queue.status_code == 200
        assert any(value["item_id"] == item_id for value in queue.json()["items"])
        review = client.post(
            f"/api/reconciliations/items/{item_id}/review",
            json={
                "action": "accept",
                "reason_code": "comprovante_validado",
                "notes": "Comprovante conferido pelo gestor responsável.",
            },
        )
        assert review.status_code == 200
        assert review.json()["workspace"]["summary"]["manual_confirmed"] == 1
        with SessionLocal() as db:
            refreshed = db.get(Reconciliation, row_id)
            assert refreshed.status == "confirmed"
            assert refreshed.confirmation_mode == "manual"


def test_admin_can_classify_ipiranga_supplemental_event():
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        with SessionLocal() as db:
            seed_reference_data(db)
            admin = db.scalar(select(User).where(User.email == "portal.admin@gbi.com"))
            if not admin:
                admin = User(
                    email="portal.admin@gbi.com",
                    full_name="Admin Portal",
                    role="admin",
                    active=True,
                    must_change_password=False,
                    password_hash=hash_password("SenhaSegura123!"),
                )
                db.add(admin)
                db.flush()
            event = db.scalar(
                select(PortalBonusEvent).where(
                    PortalBonusEvent.event_key == "test:supplemental:auto-deposit"
                )
            )
            if not event:
                event = PortalBonusEvent(
                    unit_code="001",
                    company_code="IPIRANGA",
                    category="supplemental_auto_deposit",
                    portal_date=date(2025, 1, 19),
                    value=Decimal("4263.00"),
                    description="PAGTO - 26/12/2024",
                    reference="test-auto-deposit",
                    event_key="test:supplemental:auto-deposit",
                )
                db.add(event)
            db.commit()
            event_id = event.id

        login = client.post(
            "/api/auth/login",
            json={"email": "portal.admin@gbi.com", "password": "SenhaSegura123!"},
        )
        assert login.status_code == 200
        response = client.post(
            f"/api/portal-statements/ipiranga/events/{event_id}/classify",
            json={
                "classification": "postpaid_regularization",
                "notes": "Regularização do período anterior ao primeiro crédito CU.",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        refreshed = next(
            row for row in payload["dashboard"]["supplemental"]["events"] if row["id"] == event_id
        )
        assert refreshed["review"]["classification"] == "postpaid_regularization"
        assert refreshed["review"]["classification_label"] == "Regularização da postecipada"
        assert refreshed["review"]["reviewed_by"] == "Admin Portal"
        with SessionLocal() as db:
            saved = db.get(PortalBonusEvent, event_id)
            assert saved.business_classification == "postpaid_regularization"
            assert saved.review_notes == "Regularização do período anterior ao primeiro crédito CU."
            assert saved.reviewed_by == admin.id
            assert saved.reviewed_at is not None


def test_unit_update_rejects_null_non_nullable_fields_instead_of_failing_with_500():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        admin = db.scalar(select(User).where(User.email == "unit.admin@gbi.com"))
        if not admin:
            admin = User(
                email="unit.admin@gbi.com",
                full_name="Admin de Unidades",
                role="admin",
                active=True,
                must_change_password=False,
                password_hash=hash_password("SenhaSegura123!"),
            )
            db.add(admin)
            db.commit()

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"email": "unit.admin@gbi.com", "password": "SenhaSegura123!"},
        )
        assert login.status_code == 200
        invalid = client.patch("/api/admin/units/002", json={"state": None})
        assert invalid.status_code == 422
        valid = client.patch("/api/admin/units/002", json={"display_name": "002 — Bagé"})
        assert valid.status_code == 200
