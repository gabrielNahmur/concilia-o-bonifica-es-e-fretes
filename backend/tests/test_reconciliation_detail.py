import json
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    BonusRule,
    FinancialEntry,
    PayableDocument,
    PayableMovement,
    Purchase,
    PurchaseItem,
    Reconciliation,
    ReconciliationAllocation,
    ReconciliationEvidence,
    ReconciliationException,
    ReconciliationItem,
)
from app.services.reconciliation_detail import (
    _account_label,
    _allocation_evidence_payload,
    _enrich_evidence,
    build_reconciliation_detail,
)
from app.services.seed import seed_reference_data


def test_detail_builds_exact_note_title_financial_chain():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount"))
        purchase = Purchase(
            erp_entry_id=5001,
            unit_code="005",
            supplier_person_id=99,
            supplier_name="IPIRANGA PRODUTOS",
            supplier_cnpj="33337122015906",
            mapped_company_code="TEXACO",
            invoice_number="1234",
            invoice_series="1",
            purchase_date=date(2026, 6, 10),
            total_liters=Decimal("15000"),
            s10_liters=Decimal("0"),
            gross_value=Decimal("90000"),
            net_value=Decimal("90000"),
        )
        purchase.items.append(
            PurchaseItem(
                item_code="1", sequence=1, description="GASOLINA", unit="L",
                quantity=Decimal("15000"), unit_value=Decimal("6"), total_value=Decimal("90000"),
            )
        )
        db.add(purchase)
        db.add(
            PayableDocument(
                unit_code="005", person_id=99, title_type="NF", document_id="BOL123", sequence="01",
                erp_entry_id=5001, invoice_number="1234", document_value=Decimal("89400"),
                other_discount=Decimal("0"), issue_date=date(2026, 6, 10), due_date=date(2026, 6, 20),
                payment_date=date(2026, 6, 20), balance=Decimal("0"),
            )
        )
        db.add_all([
            FinancialEntry(
                erp_launch_id=7001, erp_sequence=1, unit_code="005", entry_date=date(2026, 6, 20),
                history_code=51, direction="S", value=Decimal("89400"), document_id="BOL123",
                history_text="PAGAMENTO DO TITULO", origin="CP",
            ),
            FinancialEntry(
                erp_launch_id=7001, erp_sequence=2, unit_code="005", entry_date=date(2026, 6, 20),
                history_code=6204, direction="E", value=Decimal("600"), document_id="BOL123",
                history_text="DESCONTO COMERCIAL COMBUSTIVEL", origin="CP",
            ),
        ])
        db.add_all([
            PayableMovement(
                erp_key="B:5001", unit_code="005", person_id=99, title_type="NF",
                document_id="BOL123", document_sequence="01", movement_type="B",
                movement_date=date(2026, 6, 20), amount=Decimal("89400"), payment_sequence=1,
                reversal_sequence=0, financial_launch_id=7001,
            ),
            PayableMovement(
                erp_key="D:5001", unit_code="005", person_id=99, title_type="NF",
                document_id="BOL123", document_sequence="01", movement_type="D",
                movement_date=date(2026, 6, 20), amount=Decimal("600"), payment_sequence=1,
                reversal_sequence=0, financial_launch_id=7001,
            ),
        ])
        db.flush()
        reconciliation = Reconciliation(
            unit_code="005", rule_id=rule.id, reference_month=date(2026, 6, 1), due_date=date(2026, 6, 30),
            expected_value=Decimal("600"), observed_value=Decimal("600"), manual_adjustment=Decimal("0"),
            difference_value=Decimal("0"), status="confirmed", confidence="direct",
            evidence_json=json.dumps([{"source": "MLANF", "launch": 7001, "date": "2026-06-20", "value": 600}]),
        )
        db.add(reconciliation)
        db.flush()
        selected_item = ReconciliationItem(
            reconciliation_id=reconciliation.id, item_type="invoice", source_key="purchase:5001",
            source_date=date(2026, 6, 10), source_document="1234", description="NF 1234",
            expected_value=Decimal("600"), observed_value=Decimal("600"), difference_value=Decimal("0"),
            status="auto_confirmed", confidence="direct", automatic_eligible=True,
            review_status="pending", details_json="{}", fingerprint="selected-item",
        )
        other_item = ReconciliationItem(
            reconciliation_id=reconciliation.id, item_type="invoice", source_key="purchase:other",
            source_date=date(2026, 6, 11), source_document="9999", description="NF 9999",
            expected_value=Decimal("400"), observed_value=Decimal("400"), difference_value=Decimal("0"),
            status="auto_confirmed", confidence="direct", automatic_eligible=True,
            review_status="pending", details_json="{}", fingerprint="other-item",
        )
        db.add_all((selected_item, other_item))
        db.flush()
        selected_evidence = ReconciliationEvidence(
            source_type="MDCMP", source_key="D:5001", evidence_date=date(2026, 6, 20),
            unit_code="005", document_id="BOL123", value=Decimal("600"), counted=True,
            identity_json=json.dumps({"source": "MDCMP", "id": "D:5001", "date": "2026-06-20", "document": "BOL123", "value": 600, "corroborated_by_6204": True}),
            fingerprint="selected-evidence",
        )
        supplemental_evidence = ReconciliationEvidence(
            source_type="UNCLASSIFIED_CREDIT", source_key="credit:5001", evidence_date=date(2026, 6, 20),
            unit_code="005", document_id="UNRELATED", value=Decimal("123"), counted=False,
            identity_json=json.dumps({"source": "UNCLASSIFIED_CREDIT", "id": "credit:5001", "date": "2026-06-20", "document": "UNRELATED", "value": 123}),
            fingerprint="supplemental-evidence",
        )
        other_evidence = ReconciliationEvidence(
            source_type="MDCMP", source_key="D:other", evidence_date=date(2026, 6, 21),
            unit_code="005", document_id="OTHER", value=Decimal("400"), counted=True,
            identity_json=json.dumps({"source": "MDCMP", "id": "D:other", "date": "2026-06-21", "document": "OTHER", "value": 400}),
            fingerprint="other-evidence",
        )
        db.add_all((selected_evidence, supplemental_evidence, other_evidence))
        db.flush()
        db.add_all((
            ReconciliationAllocation(item_id=selected_item.id, evidence_id=selected_evidence.id, allocated_value=Decimal("600"), match_status="automatic", confidence="direct", match_basis="Título da NF 1234", algorithm_version="test"),
            ReconciliationAllocation(item_id=selected_item.id, evidence_id=supplemental_evidence.id, allocated_value=Decimal("0"), match_status="proposed", confidence="none", match_basis="Vínculo auxiliar sem valor alocado", algorithm_version="test"),
            ReconciliationAllocation(item_id=other_item.id, evidence_id=other_evidence.id, allocated_value=Decimal("400"), match_status="automatic", confidence="direct", match_basis="Título da NF 9999", algorithm_version="test"),
        ))
        db.add_all([
            ReconciliationException(
                reconciliation_id=reconciliation.id,
                source_key="test:active",
                exception_type="active_test",
                severity="medium",
                status="open",
                title="Pendência ativa",
                description="Deve aparecer no detalhe operacional.",
                expected_value=Decimal("600"),
                observed_value=Decimal("0"),
                difference_value=Decimal("600"),
            ),
            ReconciliationException(
                reconciliation_id=reconciliation.id,
                source_key="test:resolved",
                exception_type="resolved_test",
                severity="medium",
                status="auto_resolved",
                title="Pendência resolvida",
                description="Deve permanecer apenas no histórico de auditoria.",
                expected_value=Decimal("600"),
                observed_value=Decimal("600"),
                difference_value=Decimal("0"),
            ),
        ])
        db.commit()

        detail = build_reconciliation_detail(db, reconciliation, rule)
        assert detail["score"] == 100
        assert detail["match_summary"]["purchase_count"] == 1
        assert detail["match_summary"]["document_count"] == 1
        assert detail["chains"][0]["status"] == "discount_exact"
        assert detail["chains"][0]["actual_discount"] == 600.0
        assert {item["kind"] for item in detail["chains"][0]["documents"][0]["financial_entries"]} == {"payment", "discount"}
        assert detail["chains"][0]["documents"][0]["payment_chain_exact"] is True
        assert detail["chains"][0]["purchase"]["expected_bonus"] == 600.0
        scoped_item = next(item for item in detail["workspace"]["items"] if item["source_document"] == "1234")
        assert [item["document"] for item in scoped_item["display_evidence"]] == ["BOL123"]
        assert scoped_item["display_evidence"][0]["value"] == 600.0
        assert len(scoped_item["allocations"]) == 2
        assert "Cd_Entrada" in detail["erp_order_note"]
        assert [item["title"] for item in detail["workspace"]["exceptions"]] == ["Pendência ativa"]
        assert detail["workspace"]["summary"]["open_exceptions"] == 1
        assert detail["workspace"]["summary"]["resolved_exceptions"] == 1


def test_accounting_evidence_uses_manager_friendly_raizen_account_labels():
    assert _account_label("1170810") == "Bonificação Raízen a receber (1170810)"
    assert _account_label("1110202000001") == "Banco Bradesco C/C (1110202000001)"


def test_detail_exposes_native_discount_for_late_payment_without_counting_it():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "050", BonusRule.kind == "invoice_discount")
        )
        reconciliation = Reconciliation(
            unit_code="050",
            rule_id=rule.id,
            reference_month=date(2026, 8, 1),
            due_date=date(2026, 8, 3),
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
                source_key="late-detail:3096076",
                source_date=date(2026, 8, 1),
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
                fingerprint="late-detail-3096076",
            )
        )
        db.commit()

        detail = build_reconciliation_detail(db, reconciliation, rule)
        item = detail["workspace"]["items"][0]

        assert item["status"] == "late_payment"
        assert item["observed_value"] == 0.0
        assert item["difference_value"] == 0.0
        assert item["identified_discount_value"] == 920.0


def test_distributor_credit_calls_native_discount_credit_utilization():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        rule = BonusRule(unit_code="003", company_code="IPIRANGA", kind="distributor_credit")
        display = _enrich_evidence(
            db,
            [{"source": "MDCMP", "id": "D:003", "value": 770, "allocated": 770}],
            rule,
        )[0]

    assert display["label"] == "Crédito utilizado na distribuidora"
    assert "utilização do crédito" in display["match_basis"]


def test_unit_004_portal_usage_evidence_explains_that_the_nf_used_the_credit():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        rule = BonusRule(unit_code="004", company_code="IPIRANGA", kind="distributor_credit")
        display = _enrich_evidence(
            db,
            [{
                "source": "IPIRANGA_PORTAL_USAGE",
                "id": "portal-004-jan",
                "date": "2026-01-16",
                "document": "3008303",
                "value": 10360.01,
                "allocated": 10360.01,
                "purchase_entry_id": 900090260,
                "access_key": "43260133337122015906550030030083031951343959",
                "match_basis": "Detalhe do portal Ipiranga declara a Nota Fiscal Utilizada.",
            }],
            rule,
        )[0]

    assert display["kind"] == "portal"
    assert display["label"] == "Crédito postecipado usado no portal Ipiranga"
    assert "NF 3008303" in display["history"]
    assert "não identifica a compra que gerou" in display["history"]


def test_portal_evidence_card_shows_only_the_current_invoice_share():
    evidence = ReconciliationEvidence(
        source_type="IPIRANGA_PORTAL",
        source_key="portal-600",
        evidence_date=date(2026, 7, 9),
        unit_code="014",
        document_id="084524",
        value=Decimal("600"),
        counted=True,
        identity_json=json.dumps({
            "source": "IPIRANGA_PORTAL",
            "document": "084524",
            "value": 600,
            "allocated": 400,
            "invoice_number": "3084524",
            "credit_issued": False,
            "portal_settlement_confirmed": True,
        }),
        fingerprint="portal-600",
    )
    item = ReconciliationItem(
        item_type="invoice",
        source_key="purchase:900096779",
        source_document="3084453",
        expected_value=Decimal("400"),
        observed_value=Decimal("400"),
        difference_value=Decimal("0"),
        status="auto_confirmed",
        details_json=json.dumps({"erp_entry_id": 900096779, "portal_credit_issued": True}),
        fingerprint="item-3084453",
    )
    allocation = ReconciliationAllocation(
        allocated_value=Decimal("400"),
        match_basis="Original allocation",
        algorithm_version="test",
    )

    payload = _allocation_evidence_payload(evidence, allocation, item)

    assert payload["value"] == 400.0
    assert payload["allocated"] == 400.0
    assert payload["document"] is None
    assert payload["invoice_number"] == "3084453"
    assert payload["credit_issued"] is True
    assert payload["portal_settlement_confirmed"] is False
    assert "esta NF" in payload["match_basis"]
