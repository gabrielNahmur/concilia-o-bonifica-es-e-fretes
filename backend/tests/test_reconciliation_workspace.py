import json
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    BonusRule,
    FinancialEntry,
    ManualAdjustment,
    PayableDocument,
    PayableMovement,
    PortalBonusEvent,
    PortalBonusMatch,
    Purchase,
    PurchaseItem,
    Reconciliation,
    ReconciliationAllocation,
    ReconciliationEvidence,
    ReconciliationException,
    ReconciliationItem,
)
from app.services.reconciliation import (
    _document_discounts_capped,
    _unit_004_unique_portal_cycle_allocations,
    invoice_discount_forfeited_by_late_payment,
    rebuild_reconciliations,
)
from app.services.reconciliation_workspace import (
    CONTRACTUAL_EXCLUSION_REVIEW_STATUS,
    _base_item_payloads,
    _automatic_policy,
    _invoice_context,
    _item_status,
    rebuild_reconciliation_workspace,
)
from app.services.seed import seed_reference_data
from app.scripts.apply_unit_001_feb_2025_management_adjustment import (
    apply_unit_001_feb_2025_management_adjustment,
)
from app.scripts.apply_unit_001_mar_2025_management_adjustment import (
    apply_unit_001_mar_2025_management_adjustment,
)


def _invoice_chain(db: Session, with_exact_6204: bool = True):
    purchase = Purchase(
        erp_entry_id=88001,
        unit_code="005",
        supplier_person_id=99,
        supplier_name="IPIRANGA PRODUTOS",
        supplier_cnpj="33337122015906",
        mapped_company_code="TEXACO",
        invoice_number="NF88001",
        purchase_date=date(2026, 6, 10),
        total_liters=Decimal("15000"),
        s10_liters=Decimal("0"),
        gross_value=Decimal("90000"),
        net_value=Decimal("90000"),
    )
    purchase.items.append(
        PurchaseItem(
            item_code="1",
            sequence=1,
            description="GASOLINA COMUM",
            unit="L",
            quantity=Decimal("15000"),
            unit_value=Decimal("6"),
            total_value=Decimal("90000"),
        )
    )
    db.add(purchase)
    db.add(
        PayableDocument(
            unit_code="005",
            person_id=99,
            title_type="NF",
            document_id="BOL88001",
            sequence="01",
            erp_entry_id=88001,
            invoice_number="NF88001",
            document_value=Decimal("90000"),
            other_discount=Decimal("0"),
            issue_date=date(2026, 6, 10),
            due_date=date(2026, 6, 20),
            payment_date=date(2026, 6, 20),
            balance=Decimal("0"),
        )
    )
    db.add_all(
        [
            PayableMovement(
                erp_key="B:88001",
                unit_code="005",
                person_id=99,
                title_type="NF",
                document_id="BOL88001",
                document_sequence="01",
                movement_type="B",
                movement_date=date(2026, 6, 20),
                amount=Decimal("89400"),
                payment_sequence=1,
                reversal_sequence=0,
                financial_launch_id=77001,
                financial_sequence=1,
            ),
            PayableMovement(
                erp_key="D:88001",
                unit_code="005",
                person_id=99,
                title_type="NF",
                document_id="BOL88001",
                document_sequence="01",
                movement_type="D",
                movement_date=date(2026, 6, 20),
                amount=Decimal("600"),
                payment_sequence=1,
                reversal_sequence=0,
                financial_launch_id=77002,
                financial_sequence=32,
            ),
        ]
    )
    db.add(
        FinancialEntry(
            erp_launch_id=77001,
            erp_sequence=1,
            unit_code="005",
            entry_date=date(2026, 6, 20),
            history_code=51,
            direction="S",
            value=Decimal("89400"),
            document_id="BOL88001",
            history_text="PAGAMENTO DO TÍTULO",
            origin="CP",
        )
    )
    if with_exact_6204:
        db.add(
            FinancialEntry(
                erp_launch_id=77002,
                erp_sequence=2,
                unit_code="005",
                entry_date=date(2026, 6, 20),
                history_code=6204,
                direction="E",
                value=Decimal("600"),
                document_id="BOL88001",
                history_text="DESCONTO COMERCIAL",
                origin="CP",
            )
        )
    db.flush()


def test_ipiranga_credit_invoice_context_uses_issue_month_not_erp_entry_month():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "003",
                BonusRule.kind == "distributor_credit",
            )
        )
        db.add_all(
            [
                Purchase(
                    erp_entry_id=88030,
                    unit_code="003",
                    supplier_name="IPIRANGA PRODUTOS",
                    supplier_cnpj="33337122015906",
                    mapped_company_code="IPIRANGA",
                    invoice_number="3055164",
                    invoice_issue_date=date(2026, 4, 30),
                    purchase_date=date(2026, 5, 1),
                    total_liters=Decimal("8000"),
                    s10_liters=Decimal("0"),
                    gross_value=Decimal("43892.80"),
                    net_value=Decimal("43892.80"),
                ),
                Reconciliation(
                    id="row-003-april",
                    unit_code="003",
                    rule_id=rule.id,
                    reference_month=date(2026, 4, 1),
                    due_date=date(2026, 5, 31),
                    status="pending",
                ),
            ]
        )
        db.flush()

        purchases, _, _, _ = _invoice_context(db, db.get(Reconciliation, "row-003-april"), rule)

        assert [purchase.invoice_number for purchase in purchases] == ["3055164"]


def test_non_ipiranga_invoice_context_keeps_erp_entry_at_effective_end():
    """Only Ipiranga distributor credits may use invoice emission as competence."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "005",
                BonusRule.kind == "invoice_discount",
            )
        )
        rule.effective_to = date(2026, 5, 1)
        db.add_all(
            [
                Purchase(
                    erp_entry_id=88031,
                    unit_code="005",
                    supplier_name="IPIRANGA PRODUTOS",
                    supplier_cnpj="33337122015906",
                    mapped_company_code="TEXACO",
                    invoice_number="3055165",
                    invoice_issue_date=date(2026, 5, 2),
                    purchase_date=date(2026, 5, 1),
                    total_liters=Decimal("10000"),
                    s10_liters=Decimal("0"),
                    gross_value=Decimal("54000"),
                    net_value=Decimal("54000"),
                ),
                Reconciliation(
                    id="row-005-may",
                    unit_code="005",
                    rule_id=rule.id,
                    reference_month=date(2026, 5, 1),
                    due_date=date(2026, 5, 31),
                    status="pending",
                ),
            ]
        )
        db.flush()

        purchases, _, _, _ = _invoice_context(db, db.get(Reconciliation, "row-005-may"), rule)

        assert [purchase.invoice_number for purchase in purchases] == ["3055165"]


def test_late_payment_forfeits_texaco_invoice_discount_only():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "050",
                BonusRule.kind == "invoice_discount",
            )
        )
        other_texaco_rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "014",
                BonusRule.kind == "invoice_discount",
            )
        )
        ipiranga_credit_rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "004",
                BonusRule.kind == "distributor_credit",
            )
        )
        late = PayableDocument(
            unit_code="050", person_id=1, title_type="NF", document_id="LATE", sequence="01",
            due_date=date(2026, 7, 10), payment_date=date(2026, 7, 14), balance=Decimal("0"),
        )
        on_time = PayableDocument(
            unit_code="050", person_id=1, title_type="NF", document_id="ONTIME", sequence="01",
            due_date=date(2026, 7, 10), payment_date=date(2026, 7, 10), balance=Decimal("0"),
        )
        assert invoice_discount_forfeited_by_late_payment(rule, [late])
        assert invoice_discount_forfeited_by_late_payment(other_texaco_rule, [late])
        assert not invoice_discount_forfeited_by_late_payment(rule, [on_time])
        assert not invoice_discount_forfeited_by_late_payment(ipiranga_credit_rule, [late])


def test_late_payment_item_is_a_non_chargeable_historical_status():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "014",
                BonusRule.kind == "invoice_discount",
            )
        )
        payload = {"details": {"lost_due_to_late_payment": True, "late_payment_days": 4}}
        assert _item_status(
            rule,
            payload,
            Decimal("0"),
            Decimal("0"),
            date(2026, 4, 23),
            date(2026, 5, 1),
            automatic=False,
        ) == "late_payment"


def _reconciliation_from_chain(db: Session):
    rule = db.scalar(
        select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount")
    )
    observed, evidence = _document_discounts_capped(db, rule, [88001])
    row = Reconciliation(
        unit_code="005",
        rule_id=rule.id,
        reference_month=date(2026, 6, 1),
        due_date=date(2026, 6, 30),
        expected_value=Decimal("600"),
        observed_value=observed,
        manual_adjustment=Decimal("0"),
        difference_value=Decimal("600") - observed,
        status="pending",
        confidence="direct",
        evidence_json=json.dumps(evidence),
    )
    db.add(row)
    db.flush()
    return row


def test_complete_internal_invoice_chain_auto_confirms_without_external_document():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        _invoice_chain(db, with_exact_6204=True)
        row = _reconciliation_from_chain(db)
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        assert item.status == "auto_confirmed"
        assert item.automatic_eligible is True
        details = json.loads(item.details_json)
        assert details["payment_chain_exact"] is True
        assert details["decision_code"] == "exact_contract_discount"
        assert row.status == "confirmed"
        assert row.confirmation_mode == "automatic"
        assert db.scalar(select(ReconciliationAllocation).where(ReconciliationAllocation.item_id == item.id)).match_status == "automatic"


def test_numerical_match_without_6204_confirms_by_exact_value():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        _invoice_chain(db, with_exact_6204=False)
        row = _reconciliation_from_chain(db)
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        assert item.status == "auto_confirmed"
        assert item.automatic_eligible is True
        assert "Valor exato confirmado" in item.policy_reason
        assert row.status == "confirmed"
        assert row.confirmation_mode == "automatic"
        assert db.scalar(
            select(ReconciliationException).where(
                ReconciliationException.item_id == item.id,
                ReconciliationException.exception_type == "incomplete_document_chain",
            )
        ) is None


def test_numerical_match_without_unique_history_51_confirms_by_exact_value():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        _invoice_chain(db, with_exact_6204=True)
        db.delete(db.scalar(select(FinancialEntry).where(FinancialEntry.history_code == 51)))
        db.flush()
        row = _reconciliation_from_chain(db)
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        details = json.loads(item.details_json)
        assert details["payment_chain_exact"] is False
        assert details["decision_code"] == "incomplete_payment_chain"
        assert item.status == "auto_confirmed"
        assert item.automatic_eligible is True
        assert row.status == "confirmed"


def test_unit_005_mismatched_native_discount_is_not_counted_as_contract_bonus():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        _invoice_chain(db, with_exact_6204=True)
        discount = db.get(PayableMovement, "D:88001")
        discount.amount = Decimal("577.49")
        financial = db.scalar(select(FinancialEntry).where(FinancialEntry.erp_launch_id == 77002))
        financial.value = Decimal("577.49")
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount")
        )
        observed, evidence = _document_discounts_capped(db, rule, [88001])
        assert observed == Decimal("0.00")
        assert len(evidence) == 1
        assert evidence[0]["source"] == "unclassified_credit"
        assert evidence[0]["value"] == 577.49
        row = _reconciliation_from_chain(db)
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        assert item.status == "divergent"
        assert item.confidence == "direct"
        assert row.status == "divergent"
        assert db.scalar(
            select(ReconciliationException).where(
                ReconciliationException.item_id == item.id,
                ReconciliationException.exception_type == "unclassified_invoice_discount",
            )
        ) is not None


def test_texaco_portal_exact_credit_is_an_automatic_invoice_confirmation_source():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount"))
        payload = {
            "expected": Decimal("400"),
            "details": {"portal_postpaid_exact": True},
            "evidence": [{
                "source": "IPIRANGA_PORTAL",
                "value": 400,
                "source_value": 400,
                "allocated": 400,
                "portal_match_status": "portal_exact",
            }],
        }
        automatic, reason = _automatic_policy(rule, payload, Decimal("400"))
        assert automatic is True
        assert "Nota Propria" in reason


def test_issued_texaco_portal_proof_confirms_without_erp_settlement():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount")
        )
        payload = {
            "expected": Decimal("400"),
            "details": {
                "portal_postpaid_exact": True,
                "portal_credit_issued": True,
            },
            "evidence": [{
                "source": "IPIRANGA_PORTAL",
                "value": 600,
                "source_value": 600,
                "allocated": 400,
                "counted": True,
                "credit_issued": True,
                "portal_match_status": "portal_issued_awaiting_payment",
                "portal_product_anchor_exact": True,
                "portal_group_allocation_exact": True,
                "portal_settlement_confirmed": False,
            }],
        }
        automatic, reason = _automatic_policy(rule, payload, Decimal("400"))
        assert automatic is True
        assert "sem reutilizacao" in reason


def test_paid_invoice_without_discount_is_automatically_divergent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        _invoice_chain(db)
        db.delete(db.get(PayableMovement, "D:88001"))
        db.delete(db.scalar(select(FinancialEntry).where(FinancialEntry.history_code == 6204)))
        db.flush()
        row = _reconciliation_from_chain(db)
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        assert item.status == "divergent"
        assert row.status == "divergent"
        assert db.scalar(
            select(ReconciliationException).where(
                ReconciliationException.item_id == item.id,
                ReconciliationException.exception_type == "paid_without_discount",
            )
        ) is not None


def test_historical_contractual_exclusion_persists_without_claiming_a_bonus():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        _invoice_chain(db)
        db.delete(db.get(PayableMovement, "D:88001"))
        db.delete(db.scalar(select(FinancialEntry).where(FinancialEntry.history_code == 6204)))
        db.flush()
        row = _reconciliation_from_chain(db)
        row.manual_adjustment = Decimal("600")
        row.difference_value = Decimal("0")
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        assert item.status == "divergent"
        item.review_status = CONTRACTUAL_EXCLUSION_REVIEW_STATUS
        item.status = "not_applicable"
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        db.refresh(item)
        db.refresh(row)
        assert item.review_status == CONTRACTUAL_EXCLUSION_REVIEW_STATUS
        assert item.status == "not_applicable"
        assert item.observed_value == Decimal("0")
        assert row.status == "not_applicable"
        assert db.scalar(
            select(ReconciliationException).where(
                ReconciliationException.item_id == item.id,
                ReconciliationException.status.in_(("open", "in_review")),
            )
        ) is None


def test_historical_contractual_exclusion_survives_a_source_fingerprint_change():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        _invoice_chain(db)
        db.delete(db.get(PayableMovement, "D:88001"))
        db.delete(db.scalar(select(FinancialEntry).where(FinancialEntry.history_code == 6204)))
        db.flush()
        row = _reconciliation_from_chain(db)
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        item.review_status = CONTRACTUAL_EXCLUSION_REVIEW_STATUS
        item.status = "not_applicable"
        original_fingerprint = item.fingerprint

        document = db.scalar(select(PayableDocument).where(PayableDocument.erp_entry_id == 88001))
        document.due_date = date(2026, 7, 12)
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        db.refresh(item)
        db.refresh(row)

        assert item.fingerprint != original_fingerprint
        assert item.review_status == CONTRACTUAL_EXCLUSION_REVIEW_STATUS
        assert item.status == "not_applicable"
        assert row.status == "not_applicable"


def test_unpaid_invoice_stays_waiting_even_after_its_title_due_date():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        _invoice_chain(db)
        db.delete(db.get(PayableMovement, "B:88001"))
        db.delete(db.get(PayableMovement, "D:88001"))
        db.delete(db.scalar(select(FinancialEntry).where(FinancialEntry.history_code == 6204)))
        document = db.scalar(select(PayableDocument).where(PayableDocument.erp_entry_id == 88001))
        document.payment_date = None
        document.balance = Decimal("90000")
        document.due_date = date(2026, 7, 20)
        db.flush()
        row = _reconciliation_from_chain(db)
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        assert item.status == "pending"
        assert row.status == "pending"

        rebuild_reconciliation_workspace(db, date(2026, 7, 21))
        db.refresh(item)
        db.refresh(row)
        assert item.status == "pending"
        assert row.status == "pending"


def test_same_bank_evidence_cannot_auto_confirm_two_competencies():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "054", BonusRule.kind == "bank_deposit"))
        evidence = json.dumps(
            [{"source": "MExtratoBancoLanc", "id": "same-bank", "date": "2026-03-20", "value": 100.0}]
        )
        for reference in (date(2026, 1, 1), date(2026, 2, 1)):
            db.add(
                Reconciliation(
                    unit_code="054",
                    rule_id=rule.id,
                    reference_month=reference,
                    due_date=date(2026, 3, 20),
                    expected_value=Decimal("100"),
                    observed_value=Decimal("100"),
                    manual_adjustment=Decimal("0"),
                    difference_value=Decimal("0"),
                    status="pending",
                    confidence="direct",
                    evidence_json=evidence,
                )
            )
        db.flush()
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        items = db.scalars(select(ReconciliationItem)).all()
        assert len(items) == 2
        assert all(item.status == "review_required" for item in items)
        assert db.scalar(
            select(ReconciliationException).where(
                ReconciliationException.exception_type == "evidence_overallocated"
            )
        ) is not None


def test_unclassified_residual_blocks_automatic_invoice_confirmation():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "050", BonusRule.kind == "invoice_discount")
        )
        eligible, reason = _automatic_policy(
            rule,
            {
                "expected": Decimal("400"),
                "automatic_kind": True,
                "evidence": [
                    {"source": "MDCMP", "allocated": 400, "value": 1000},
                    {"source": "unclassified_credit", "counted": False, "value": 600},
                ],
            },
            Decimal("400"),
        )
        assert eligible is False
        assert "não classificada" in reason


def test_unit_003_requires_ipiranga_portal_instead_of_credit_utilization():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "003", BonusRule.kind == "distributor_credit")
        )
        native, native_reason = _automatic_policy(
            rule,
            {
                "expected": Decimal("600"),
                "evidence": [{"source": "MDCMP", "value": 600, "allocated": 600, "document": "TIT-1"}],
            },
            Decimal("600"),
        )
        portal, portal_reason = _automatic_policy(
            rule,
            {
                "expected": Decimal("600"),
                "evidence": [{"source": "IPIRANGA_PORTAL", "value": 600, "allocated": 600}],
            },
            Decimal("600"),
        )
        assert native is False
        assert "exige extrato Ipiranga" in native_reason
        assert portal is True
        assert "portal Ipiranga" in portal_reason


def test_unit_003_without_portal_stays_waiting_after_month_due_date():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "003", BonusRule.kind == "distributor_credit")
        )
        status = _item_status(
            rule,
            {"evidence": []},
            Decimal("600"),
            Decimal("0"),
            date(2026, 4, 30),
            date(2026, 8, 3),
            automatic=False,
        )
        assert status == "pending"


def test_exact_s10_residual_confirms_but_larger_residual_does_not():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "050", BonusRule.kind == "s10_excess_credit")
        )
        exact, _ = _automatic_policy(
            rule,
            {
                "expected": Decimal("1000"),
                "evidence": [{"source": "MDCMP", "value": 1000, "allocated": 1000, "document": "TIT-S10"}],
            },
            Decimal("1000"),
        )
        larger, _ = _automatic_policy(
            rule,
            {
                "expected": Decimal("1000"),
                "evidence": [{"source": "MDCMP", "value": 1200, "allocated": 1000, "document": "TIT-S10"}],
            },
            Decimal("1000"),
        )
        assert exact is True
        assert larger is False


def test_zero_expected_month_is_not_sent_to_manual_review():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "050", BonusRule.kind == "s10_excess_credit")
        )
        row = Reconciliation(
            unit_code="050",
            rule_id=rule.id,
            reference_month=date(2026, 2, 1),
            due_date=date(2026, 3, 31),
            expected_value=Decimal("0"),
            observed_value=Decimal("0"),
            manual_adjustment=Decimal("0"),
            difference_value=Decimal("0"),
            status="pending",
            confidence="none",
            evidence_json="[]",
        )
        db.add(row)
        db.flush()
        rebuild_reconciliation_workspace(db, date(2026, 7, 10))
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        assert item is None
        assert row.status == "not_applicable"
        assert db.scalar(
            select(ReconciliationException).where(ReconciliationException.reconciliation_id == row.id)
        ) is None


def test_non_umbrella_credit_ignores_legacy_br_calculation_evidence():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "008", BonusRule.kind == "distributor_credit")
        )
        row = Reconciliation(
            unit_code="008",
            rule_id=rule.id,
            reference_month=date(2025, 12, 1),
            due_date=date(2026, 1, 31),
            expected_value=Decimal("6150"),
            observed_value=Decimal("6150"),
            manual_adjustment=Decimal("0"),
            difference_value=Decimal("0"),
            status="pending",
            confidence="direct",
            evidence_json=json.dumps([
                {
                    "source": "calculation",
                    "umbrella_group": "BR_002_006",
                    "period_start": "2025-09-01",
                    "period_end": "2025-12-26",
                    "allocated_liters": 123000,
                },
                {
                    "source": "IPIRANGA_PORTAL",
                    "id": "portal-008-dec",
                    "date": "2026-01-03",
                    "value": 6150,
                    "allocated": 6150,
                },
            ]),
        )
        db.add(row)
        db.flush()

        rebuild_reconciliation_workspace(db, date(2026, 7, 10))

        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        assert item.status == "auto_confirmed"
        assert item.observed_value == Decimal("6150")
        assert row.status == "confirmed"
        assert db.scalar(
            select(ReconciliationAllocation)
            .join(ReconciliationEvidence, ReconciliationAllocation.evidence_id == ReconciliationEvidence.id)
            .where(
                ReconciliationAllocation.item_id == item.id,
                ReconciliationEvidence.source_type == "calculation",
            )
        ) is None


def test_unit_004_unassigned_portal_credit_waits_for_competence_not_overdue():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "004",
                BonusRule.kind == "distributor_credit",
            )
        )
        payload = {
            "expected": Decimal("7700"),
            "evidence": [
                {
                    "source": "unassigned_portal_credit",
                    "id": "portal-004-7350",
                    "date": "2026-07-16",
                    "value": Decimal("7350"),
                    "counted": False,
                }
            ],
        }

        automatic, reason = _automatic_policy(rule, payload, Decimal("0"))
        status = _item_status(
            rule,
            payload,
            Decimal("7700"),
            Decimal("0"),
            date(2026, 7, 31),
            date(2026, 8, 4),
            automatic,
        )

        assert automatic is False
        assert "competencia" in reason.lower()
        assert status == "pending"


def _unit_004_purchase(entry_id: int, invoice: str, purchased_on: date, liters: str) -> Purchase:
    return Purchase(
        erp_entry_id=entry_id,
        unit_code="004",
        supplier_person_id=4,
        supplier_name="IPIRANGA PRODUTOS DE PETROLEO SA",
        supplier_cnpj="33337122015906",
        mapped_company_code="IPIRANGA",
        invoice_number=invoice,
        purchase_date=purchased_on,
        total_liters=Decimal(liters),
        s10_liters=Decimal("0"),
        gross_value=Decimal("0"),
        net_value=Decimal("0"),
    )


def test_unit_004_does_not_materialize_an_inferred_portal_cycle_per_nf():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "004",
                BonusRule.kind == "distributor_credit",
            )
        )
        # 50k + 75k L = R$8,750 at the contractual R$0.07/L.  This is the
        # sole contiguous group before the 18 Nov. portal credit.
        db.add_all(
            [
                _unit_004_purchase(40001, "40001", date(2025, 10, 21), "50000"),
                _unit_004_purchase(40002, "40002", date(2025, 11, 17), "75000"),
                # The next portal window contains two possible 125k groups,
                # so it must stay unassigned rather than choosing one.
                _unit_004_purchase(40003, "40003", date(2025, 12, 1), "50000"),
                _unit_004_purchase(40004, "40004", date(2025, 12, 5), "75000"),
                _unit_004_purchase(40005, "40005", date(2025, 12, 10), "50000"),
            ]
        )
        db.add_all(
            [
                PortalBonusEvent(
                    id="portal-004-unique",
                    event_key="portal-004-unique",
                    unit_code="004",
                    company_code="IPIRANGA",
                    category="postpaid",
                    portal_date=date(2025, 11, 18),
                    value=Decimal("8750"),
                ),
                PortalBonusEvent(
                    id="portal-004-ambiguous",
                    event_key="portal-004-ambiguous",
                    unit_code="004",
                    company_code="IPIRANGA",
                    category="postpaid",
                    portal_date=date(2025, 12, 20),
                    value=Decimal("8750"),
                ),
            ]
        )
        db.flush()

        allocations = _unit_004_unique_portal_cycle_allocations(db, rule, date(2025, 12, 20))
        assert set(allocations) == {40001, 40002}
        assert sum(Decimal(str(item["allocated"])) for item in allocations[40001] + allocations[40002]) == Decimal("8750.00")
        assert all(item["portal_cycle_exact"] is True for values in allocations.values() for item in values)

        rebuild_reconciliations(db, date(2025, 12, 20), commit=False)
        confirmed = db.scalars(
            select(ReconciliationItem).where(
                ReconciliationItem.source_document.in_(("40001", "40002"))
            )
        ).all()
        assert confirmed == []
        assert all(item.status == "auto_confirmed" for item in confirmed)
        assert all("ciclo único" in item.policy_reason.lower() for item in confirmed)
        ambiguous = db.scalars(
            select(ReconciliationItem).where(
                ReconciliationItem.source_document.in_(("40003", "40004", "40005"))
            )
        ).all()
        assert all(item.status != "auto_confirmed" for item in ambiguous)


def test_unit_004_assigns_a_portal_credit_to_the_previous_competence_not_to_an_nf():
    """The 004 calendar rule preserves the portal value even when it differs."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        db.add_all(
            [
                _unit_004_purchase(40001, "40001", date(2025, 10, 21), "50000"),
                _unit_004_purchase(40002, "40002", date(2025, 11, 17), "75000"),
            ]
        )
        event = PortalBonusEvent(
            id="portal-004-direct-usage",
            event_key="portal-004-direct-usage",
            unit_code="004",
            company_code="IPIRANGA",
            category="postpaid",
            portal_date=date(2025, 11, 18),
            value=Decimal("8750.00"),
        )
        db.add(event)
        db.flush()
        db.add(
            PortalBonusMatch(
                event_id=event.id,
                status="portal_usage_confirmed",
                purchase_entry_id=40002,
                match_basis="Portal informa NF utilizada e valor exato do crédito.",
                details_json='{"usage_invoice_number":"40002"}',
                algorithm_version="portal-usage-v1",
            )
        )
        db.flush()

        rebuild_reconciliations(db, date(2025, 11, 20), commit=False)

        items = db.scalars(
            select(ReconciliationItem).where(ReconciliationItem.source_document.in_(("40001", "40002")))
        ).all()
        assert items == []

        october = db.scalar(
            select(Reconciliation).where(
                Reconciliation.unit_code == "004",
                Reconciliation.reference_month == date(2025, 10, 1),
            )
        )
        assert october is not None
        assert october.expected_value == Decimal("3500.00")
        assert october.observed_value == Decimal("8750.00")
        assert october.difference_value == Decimal("-5250.00")
        assert october.status == "divergent"


def test_full_management_adjustment_hides_rejected_automatic_evidence_from_workspace():
    """A full historical management adjustment is not a portal-credit proof."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "001",
                BonusRule.kind == "distributor_credit",
            )
        )
        row = Reconciliation(
            unit_code="001",
            rule_id=rule.id,
            reference_month=date(2025, 2, 1),
            due_date=date(2025, 3, 31),
            expected_value=Decimal("9570.00"),
            observed_value=Decimal("1044.02"),
            manual_adjustment=Decimal("9570.00"),
            difference_value=Decimal("0.00"),
            status="confirmed",
            confidence="none",
            confirmation_mode="manual",
            evidence_json="[]",
        )
        db.add(row)
        db.flush()
        db.add(
            ManualAdjustment(
                reconciliation_id=row.id,
                amount=Decimal("1044.02"),
                reason=(
                    "[historical_full_adjustment] Regularização integral aprovada pela gestão; "
                    "não representa crédito do portal ou ERP."
                ),
                created_by="admin",
            )
        )
        db.flush()

        assert _base_item_payloads(db, row, rule) == []


def test_unit_001_historical_adjustment_promotes_the_manual_balance_to_the_full_expected_value():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "001",
                BonusRule.kind == "distributor_credit",
            )
        )
        row = Reconciliation(
            unit_code="001",
            rule_id=rule.id,
            reference_month=date(2025, 2, 1),
            due_date=date(2025, 3, 31),
            expected_value=Decimal("9570.00"),
            observed_value=Decimal("1044.02"),
            manual_adjustment=Decimal("8525.98"),
            difference_value=Decimal("0.00"),
            status="confirmed",
            confidence="direct",
            confirmation_mode="manual",
            evidence_json="[]",
        )
        db.add(row)
        db.flush()
        db.add(
            ManualAdjustment(
                reconciliation_id=row.id,
                amount=Decimal("8525.98"),
                reason="Regularização inicial aprovada pela gestão.",
                created_by="admin",
            )
        )
        db.flush()

        result = apply_unit_001_feb_2025_management_adjustment(db, rebuild=False)

        assert result == {"added": Decimal("1044.02"), "total_adjustment": Decimal("9570.00")}
        assert row.observed_value == Decimal("0.00")
        assert row.manual_adjustment == Decimal("9570.00")
        assert row.difference_value == Decimal("0.00")
        assert db.scalar(
            select(ManualAdjustment).where(
                ManualAdjustment.reconciliation_id == row.id,
                ManualAdjustment.reason.startswith("[historical_full_adjustment]"),
            )
        )


def test_unit_001_march_full_historical_adjustment_replaces_stale_calculation_evidence():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "001",
                BonusRule.kind == "distributor_credit",
            )
        )
        row = Reconciliation(
            unit_code="001",
            rule_id=rule.id,
            reference_month=date(2025, 3, 1),
            due_date=date(2025, 4, 30),
            expected_value=Decimal("10005.00"),
            observed_value=Decimal("0.00"),
            manual_adjustment=Decimal("10005.00"),
            difference_value=Decimal("0.00"),
            status="confirmed",
            confidence="none",
            confirmation_mode="manual",
            evidence_json=json.dumps([{"source": "calculation", "value": 0}]),
        )
        db.add(row)
        db.flush()
        db.add(
            ManualAdjustment(
                reconciliation_id=row.id,
                amount=Decimal("10005.00"),
                reason="Regularização inicial aprovada pela gestão.",
                created_by="admin",
            )
        )
        db.flush()

        result = apply_unit_001_mar_2025_management_adjustment(db, rebuild=False)

        assert result == {"added": Decimal("0.00"), "total_adjustment": Decimal("10005.00")}
        marker = db.scalar(
            select(ManualAdjustment).where(
                ManualAdjustment.reconciliation_id == row.id,
                ManualAdjustment.reason.startswith("[historical_full_adjustment]"),
            )
        )
        assert marker.amount == Decimal("0.00")
        assert _base_item_payloads(db, row, rule) == []
