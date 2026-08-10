from fastapi.testclient import TestClient
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, select

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


def test_work_queue_exposes_native_discount_for_late_payment_without_counting_it():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        user = db.scalar(select(User).where(User.email == "late.queue@gbi.com"))
        if not user:
            user = User(
                email="late.queue@gbi.com",
                full_name="Consulta de Atraso",
                role="viewer",
                active=True,
                must_change_password=False,
                password_hash=hash_password("SenhaSegura123!"),
            )
            db.add(user)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "050", BonusRule.kind == "invoice_discount")
        )
        reconciliation = Reconciliation(
            unit_code="050",
            rule_id=rule.id,
            reference_month=date(2032, 8, 1),
            due_date=date(2032, 8, 3),
            expected_value=Decimal("0"),
            observed_value=Decimal("0"),
            manual_adjustment=Decimal("0"),
            difference_value=Decimal("0"),
            status="late_payment",
            confidence="direct",
            evidence_json="[]",
        )
        db.add(reconciliation)
        db.flush()
        db.add(
            ReconciliationItem(
                reconciliation_id=reconciliation.id,
                item_type="invoice",
                source_key="late-payment:3096076",
                source_date=date(2032, 8, 1),
                source_document="3096076",
                description="NF 3096076 - pagamento em atraso; desconto não aplicável",
                expected_value=Decimal("0"),
                observed_value=Decimal("0"),
                difference_value=Decimal("0"),
                status="late_payment",
                confidence="direct",
                automatic_eligible=False,
                review_status="pending",
                policy_reason="Título liquidado 4 dia(s) após o vencimento; desconto contratual não aplicável.",
                details_json='{"raw_discount_value": 920, "contractual_expected_value": 920, "lost_due_to_late_payment": true}',
                fingerprint="late-payment-3096076",
            )
        )
        db.commit()

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login", json={"email": "late.queue@gbi.com", "password": "SenhaSegura123!"}
        ).status_code == 200
        response = client.get("/api/reconciliations/work-queue?unit=050&scope=confirmed")
        assert response.status_code == 200
        row = next(item for item in response.json()["items"] if item["document"] == "3096076")
        assert row["status"] == "late_payment"
        assert row["observed_value"] == 0.0
        assert row["difference_value"] == 0.0
        assert row["identified_discount_value"] == 920.0


def test_work_queue_filters_by_detailed_situation():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        user = db.scalar(select(User).where(User.email == "situations.queue@gbi.com"))
        if not user:
            user = User(
                email="situations.queue@gbi.com",
                full_name="Consulta de situaÃ§Ãµes",
                role="viewer",
                active=True,
                must_change_password=False,
                password_hash=hash_password("SenhaSegura123!"),
            )
            db.add(user)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "001", BonusRule.kind == "distributor_credit")
        )
        rows = [
            ("OVER", "divergent", "100", "120", "-20", date(2034, 1, 31), "[]"),
            ("UNDER", "divergent", "100", "80", "20", date(2034, 2, 28), "[]"),
            ("PENDING", "pending", "100", "0", "100", date(2034, 3, 31), "[]"),
            ("OVERDUE", "overdue", "100", "0", "100", date(2034, 4, 30), "[]"),
            ("CONFIRMED", "confirmed", "100", "100", "0", date(2034, 5, 31), "[]"),
            ("LATE", "late_payment", "0", "0", "0", date(2034, 6, 30), "[]"),
            ("REVIEW", "review_required", "100", "0", "100", date(2034, 7, 31), "[]"),
            (
                "ADJUSTMENT", "confirmed", "100", "0", "0", date(2034, 8, 31),
                '[{"source":"MANAGEMENT_ADJUSTMENT","amount":100}]',
            ),
        ]
        records = []
        for index, (label, status, expected, observed, difference, due_date, evidence) in enumerate(rows, start=1):
            record = Reconciliation(
                unit_code="001",
                rule_id=rule.id,
                reference_month=date(2034, index, 1),
                due_date=due_date,
                expected_value=Decimal(expected),
                observed_value=Decimal(observed),
                manual_adjustment=Decimal("0"),
                difference_value=Decimal(difference),
                status=status,
                confidence="direct",
                evidence_json=evidence,
            )
            db.add(record)
            records.append((label, record))
        db.flush()
        review = next(record for label, record in records if label == "REVIEW")
        db.add(
            ReconciliationException(
                reconciliation_id=review.id,
                source_key="situations:review",
                exception_type="manual_review_required",
                severity="high",
                status="in_review",
                title="RevisÃ£o iniciada",
                description="Aguardando decisÃ£o humana.",
                expected_value=Decimal("100"),
                observed_value=Decimal("0"),
                difference_value=Decimal("100"),
            )
        )
        db.commit()

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login", json={"email": "situations.queue@gbi.com", "password": "SenhaSegura123!"}
        ).status_code == 200
        expected_months = {
            "overpaid": "2034-01-01",
            "underpaid": "2034-02-01",
            "pending": "2034-03-01",
            "overdue": "2034-04-01",
            "confirmed": "2034-05-01",
            "late_payment": "2034-06-01",
            "in_review": "2034-07-01",
            "approved_adjustment": "2034-08-01",
        }
        for situation, reference_month in expected_months.items():
            response = client.get(f"/api/reconciliations/work-queue?unit=001&state={situation}")
            assert response.status_code == 200
            filtered_rows = response.json()["items"]
            assert all(row["situation"] == situation for row in filtered_rows)
            rows = [row for row in filtered_rows if row["reference_month"] == reference_month]
            assert len(rows) == 1


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
                db.add(row)
                db.flush()
            else:
                row.expected_value = Decimal("100")
                row.observed_value = Decimal("100")
                row.manual_adjustment = Decimal("0")
                row.difference_value = Decimal("0")
                row.status = "pending"
                row.confidence = "direct"
                row.evidence_json = "[]"
            db.execute(
                delete(ReconciliationReview).where(ReconciliationReview.reconciliation_id == row.id)
            )
            db.commit()
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


def test_all_administrative_reconciliation_actions_persist_an_audited_decision():
    """The four actions exposed in the drawer must execute through the real API."""
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        with SessionLocal() as db:
            seed_reference_data(db)
            admin = db.scalar(select(User).where(User.email == "actions.admin@gbi.com"))
            if not admin:
                admin = User(
                    email="actions.admin@gbi.com",
                    full_name="Admin de Ações",
                    role="admin",
                    active=True,
                    must_change_password=False,
                    password_hash=hash_password("SenhaSegura123!"),
                )
                db.add(admin)
            rule = db.scalar(
                select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount")
            )

            def pending_row(reference_month: date) -> Reconciliation:
                row = Reconciliation(
                    unit_code="005", rule_id=rule.id, reference_month=reference_month,
                    due_date=date(2041, 12, 31), expected_value=Decimal("100"),
                    observed_value=Decimal("0"), manual_adjustment=Decimal("0"),
                    difference_value=Decimal("100"), status="pending", confidence="none", evidence_json="[]",
                )
                db.add(row)
                db.flush()
                db.add(
                    ReconciliationItem(
                        reconciliation_id=row.id,
                        item_type="invoice",
                        source_key=f"actions:{reference_month.isoformat()}",
                        source_date=reference_month,
                        source_document=f"NF-{reference_month.month}",
                        description="Item para validar ação administrativa",
                        expected_value=Decimal("100"), observed_value=Decimal("0"),
                        difference_value=Decimal("100"), status="pending", confidence="none",
                        automatic_eligible=False, review_status="pending",
                        policy_reason="Aguardando decisão administrativa.", details_json="{}",
                        fingerprint=f"actions-{reference_month.month}" * 8,
                    )
                )
                db.flush()
                return row

            request_info = pending_row(date(2041, 1, 1))
            reject = pending_row(date(2041, 2, 1))
            adjustment = Reconciliation(
                unit_code="005", rule_id=rule.id, reference_month=date(2041, 3, 1),
                due_date=date(2041, 12, 31), expected_value=Decimal("100"),
                observed_value=Decimal("0"), manual_adjustment=Decimal("0"),
                difference_value=Decimal("100"), status="pending", confidence="none", evidence_json="[]",
            )
            confirmation = Reconciliation(
                unit_code="005", rule_id=rule.id, reference_month=date(2041, 4, 1),
                due_date=date(2041, 12, 31), expected_value=Decimal("100"),
                observed_value=Decimal("100"), manual_adjustment=Decimal("0"),
                difference_value=Decimal("0"), status="pending", confidence="direct", evidence_json="[]",
            )
            db.add_all((adjustment, confirmation))
            db.commit()
            ids = {
                "request_info_item": db.scalar(select(ReconciliationItem.id).where(ReconciliationItem.reconciliation_id == request_info.id)),
                "reject_item": db.scalar(select(ReconciliationItem.id).where(ReconciliationItem.reconciliation_id == reject.id)),
                "adjustment": adjustment.id,
                "confirmation": confirmation.id,
            }

        assert client.post(
            "/api/auth/login", json={"email": "actions.admin@gbi.com", "password": "SenhaSegura123!"},
        ).status_code == 200
        assert client.post(
            f"/api/reconciliations/items/{ids['request_info_item']}/review",
            json={"action": "needs_information", "reason_code": "boleto_pendente", "notes": "Solicitar o boleto e o comprovante ao financeiro."},
        ).status_code == 200
        assert client.post(
            f"/api/reconciliations/items/{ids['reject_item']}/review",
            json={"action": "reject", "reason_code": "vinculo_invalido", "notes": "O documento não pertence à competência conferida."},
        ).status_code == 200
        adjusted = client.post(
            f"/api/reconciliations/{ids['adjustment']}/adjust",
            json={"amount": "100.00", "reason": "Ajuste gerencial de teste para validar a trilha auditável."},
        )
        assert adjusted.status_code == 200
        assert adjusted.json()["difference_value"] == 0.0
        confirmed = client.post(
            f"/api/reconciliations/{ids['confirmation']}/confirm",
            json={"notes": "Competência de teste confirmada pelo gestor."},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["confirmation_mode"] == "manual"

        with SessionLocal() as db:
            assert db.get(ReconciliationItem, ids["request_info_item"]).review_status == "needs_information"
            assert db.get(ReconciliationItem, ids["reject_item"]).review_status == "rejected"
            assert db.get(Reconciliation, ids["adjustment"]).manual_adjustment == Decimal("100.00")
            assert db.get(Reconciliation, ids["confirmation"]).status == "confirmed"
            actions = db.scalars(
                select(ReconciliationReview.action).where(
                    ReconciliationReview.reconciliation_id.in_((ids["adjustment"], ids["confirmation"]))
                )
            ).all()
            assert {"adjust", "confirm"}.issubset(actions)


def test_information_request_can_be_answered_without_closing_the_financial_difference():
    """A resposta interna encerra somente a solicitação, nunca a cobrança."""
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        with SessionLocal() as db:
            seed_reference_data(db)
            requester = db.scalar(select(User).where(User.email == "requester.info@gbi.com"))
            if not requester:
                requester = User(
                    email="requester.info@gbi.com",
                    full_name="Gestor Solicitante",
                    role="admin",
                    active=True,
                    must_change_password=False,
                    password_hash=hash_password("SenhaSegura123!"),
                )
                db.add(requester)
            responder = db.scalar(select(User).where(User.email == "responder.info@gbi.com"))
            if not responder:
                responder = User(
                    email="responder.info@gbi.com",
                    full_name="Gestora Respondente",
                    role="admin",
                    active=True,
                    must_change_password=False,
                    password_hash=hash_password("SenhaSegura123!"),
                )
                db.add(responder)
            rule = db.scalar(
                select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount")
            )
            row = Reconciliation(
                unit_code="005", rule_id=rule.id, reference_month=date(2042, 1, 1),
                due_date=date(2042, 1, 31), expected_value=Decimal("600"),
                observed_value=Decimal("0"), manual_adjustment=Decimal("0"),
                difference_value=Decimal("600"), status="pending", confidence="none", evidence_json="[]",
            )
            db.add(row)
            db.flush()
            item = ReconciliationItem(
                reconciliation_id=row.id,
                item_type="invoice",
                source_key="request-response-2042-01",
                source_date=date(2042, 1, 10),
                source_document="NF-2042",
                description="Item com pedido interno de informação",
                expected_value=Decimal("600"), observed_value=Decimal("0"),
                difference_value=Decimal("600"), status="pending", confidence="none",
                automatic_eligible=False, review_status="pending",
                policy_reason="Aguardando confirmação financeira.", details_json="{}",
                fingerprint="request-response-2042-01" * 4,
            )
            db.add(item)
            db.flush()
            db.add(
                ReconciliationException(
                    reconciliation_id=row.id,
                    item_id=item.id,
                    source_key="request-response-2042-01:paid_without_discount",
                    exception_type="paid_without_discount",
                    severity="critical",
                    status="open",
                    title="Título pago sem desconto esperado",
                    description="A cobrança financeira continua em aberto.",
                    expected_value=Decimal("600"), observed_value=Decimal("0"), difference_value=Decimal("600"),
                )
            )
            db.commit()
            item_id = item.id

        assert client.post(
            "/api/auth/login", json={"email": "requester.info@gbi.com", "password": "SenhaSegura123!"},
        ).status_code == 200
        opened = client.post(
            f"/api/reconciliations/items/{item_id}/review",
            json={
                "action": "needs_information",
                "reason_code": "boleto_pendente",
                "notes": "Confirmar com o financeiro se o desconto foi aplicado.",
            },
        )
        assert opened.status_code == 200
        request = opened.json()["information_requests"][0]
        assert request["status"] == "open"
        assert request["requested_by"] == "Gestor Solicitante"

        assert client.post(
            "/api/auth/login", json={"email": "responder.info@gbi.com", "password": "SenhaSegura123!"},
        ).status_code == 200
        answered = client.post(
            f"/api/reconciliations/information-requests/{request['id']}/respond",
            json={"notes": "Financeiro confirmou que o boleto não recebeu o desconto."},
        )
        assert answered.status_code == 200
        detail = answered.json()
        closed_request = detail["information_requests"][0]
        assert closed_request["status"] == "closed"
        assert closed_request["response"] == "Financeiro confirmou que o boleto não recebeu o desconto."
        assert closed_request["responded_by"] == "Gestora Respondente"
        assert detail["difference_value"] == 600.0
        assert detail["workspace"]["items"][0]["review_status"] == "pending"
        assert detail["workspace"]["exceptions"][0]["status"] == "open"


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
