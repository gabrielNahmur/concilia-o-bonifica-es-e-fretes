import json
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    BankEntry,
    AccountingEntry,
    BonusRule,
    FinancialEntry,
    PayableDocument,
    PayableMovement,
    PortalBonusEvent,
    PortalBonusMatch,
    Purchase,
    PurchaseItem,
    Reconciliation,
    ReconciliationItem,
)
from app.services.reconciliation import (
    _bank_or_accounting_match,
    _br_receipt_pool,
    _document_discounts_capped,
    _raizen_accrual_evidence,
    rebuild_reconciliations,
    _s10_residual_credit_pool,
    _upsert,
)
from app.services.seed import seed_reference_data


def _add_purchase_chain(
    db: Session, unit: str, entry_id: int, liters: str, discount: str, document: str, item_code: str = "5"
):
    purchase = Purchase(
        erp_entry_id=entry_id, unit_code=unit, supplier_name="IPIRANGA PRODUTOS",
        supplier_cnpj="33337122015906", mapped_company_code="TEXACO", invoice_number=str(entry_id),
        purchase_date=date(2026, 6, 10), total_liters=Decimal(liters), s10_liters=Decimal(liters),
        gross_value=Decimal("1"), net_value=Decimal("1"),
    )
    purchase.items.append(
        PurchaseItem(
            item_code=item_code, sequence=1, description="COMBUSTIVEL", unit="L",
            quantity=Decimal(liters), unit_value=Decimal("1"), total_value=Decimal(liters),
        )
    )
    db.add(purchase)
    db.add(
        PayableDocument(
            unit_code=unit, person_id=99, title_type="NF", document_id=document, sequence="01",
            erp_entry_id=entry_id, document_value=Decimal("1"), other_discount=Decimal("0"),
            issue_date=date(2026, 6, 10), due_date=date(2026, 6, 20), payment_date=date(2026, 6, 20),
            balance=Decimal("0"),
        )
    )
    db.add(
        PayableMovement(
            erp_key=f"movement:{entry_id}", unit_code=unit, person_id=99, title_type="NF",
            document_id=document, document_sequence="01", movement_type="D",
            movement_date=date(2026, 7, 10), amount=Decimal(discount), payment_sequence=1,
            reversal_sequence=0, financial_launch_id=entry_id, financial_sequence=1,
        )
    )
    db.add(
        FinancialEntry(
            erp_launch_id=entry_id, erp_sequence=1, unit_code=unit, entry_date=date(2026, 7, 10),
            history_code=6204, direction="E", value=Decimal(discount), document_id=document,
            history_text="DESCONTO COMERCIAL", origin="CP",
        )
    )
    db.flush()


def test_invoice_discount_rejects_non_contractual_value_in_full():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        _add_purchase_chain(db, "050", 9001, "10000", "1000", "DOC9001")
        base_rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "050", BonusRule.kind == "invoice_discount"))
        observed, evidence = _document_discounts_capped(db, base_rule, [9001])
        assert observed == Decimal("0.00")
        assert not any(item["source"] == "MDCMP" for item in evidence)
        assert any(item["source"] == "unclassified_credit" and item["value"] == 1000.0 for item in evidence)
        s10_rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "050", BonusRule.kind == "s10_excess_credit"))
        pool = _s10_residual_credit_pool(db, s10_rule, date(2026, 7, 31))
        assert len(pool) == 1
        assert pool[0]["remaining"] == Decimal("600.00")


def test_invoice_discount_ignores_orphan_6204_and_uses_eligible_product_codes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount"))
        _add_purchase_chain(db, "005", 9101, "10000", "400", "DOC9101", item_code="1")
        _add_purchase_chain(db, "005", 9102, "10000", "400", "DOC9102", item_code="4")
        # Remove the native movement from the aditivated invoice, leaving only an orphan 6204.
        db.delete(db.get(PayableMovement, "movement:9102"))
        db.flush()
        observed, evidence = _document_discounts_capped(db, rule, [9101, 9102])
        assert observed == Decimal("400.00")
        assert all(item.get("document") != "DOC9102" for item in evidence if item["source"] == "MDCMP")


def test_texaco_portal_credit_closes_partial_native_discount_with_unique_invoice_link():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount"))
        _add_purchase_chain(db, "005", 9201, "10000", "125", "DOC9201", item_code="1")
        event = PortalBonusEvent(
            id="portal-9201", unit_code="005", company_code="TEXACO", category="postpaid",
            portal_date=date(2026, 6, 20), value=Decimal("400"), description="Nota Propria", event_key="portal-key-9201",
        )
        db.add(event)
        db.flush()
        db.add(PortalBonusMatch(
            event_id=event.id, status="portal_exact", purchase_entry_id=9201,
            match_basis="unique formula/title/payment", details_json='{"chosen":{"title_document":"DOC9201"}}',
            algorithm_version="test",
        ))
        db.flush()
        observed, evidence = _document_discounts_capped(db, rule, [9201])
        assert observed == Decimal("400.00")
        portal = next(item for item in evidence if item["source"] == "IPIRANGA_PORTAL")
        assert portal["allocated"] == 400.0
        assert any(item["source"] == "unclassified_credit" for item in evidence)


def test_bank_entry_cannot_be_reused_in_two_competencies():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            BankEntry(
                id="bank-1", entry_date=date(2026, 7, 20), history_text="CREDITO RAIZEN",
                value=Decimal("10000"), bank_code=1,
            )
        )
        db.commit()
        used: set[str] = set()
        first = _bank_or_accounting_match(
            db, "SHELL", "054", date(2026, 7, 1), date(2026, 7, 31), Decimal("10000"), used
        )
        second = _bank_or_accounting_match(
            db, "SHELL", "054", date(2026, 7, 1), date(2026, 7, 31), Decimal("10000"), used
        )
        assert first[0] == Decimal("10000.00")
        assert second[0] == Decimal("0")


def test_close_bank_value_without_company_identity_is_not_counted():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            BankEntry(
                id="unrelated", entry_date=date(2026, 6, 15), history_text="VERO DEBITO",
                value=Decimal("7862.29"), bank_code=41,
            )
        )
        db.flush()
        result = _bank_or_accounting_match(
            db, "SHELL", "054", date(2026, 6, 1), date(2026, 6, 30), Decimal("7800"), set()
        )
        assert result[0] == Decimal("0")
        assert result[2] == []


def test_unique_exact_bank_value_with_document_is_counted_as_value_exact():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            BankEntry(
                id="exact-but-generic",
                entry_date=date(2026, 6, 15),
                history_text="CREDITO DISTRIBUIDORA",
                document="TED-20260615-001",
                value=Decimal("7800"),
                bank_code=41,
            )
        )
        db.flush()
        result = _bank_or_accounting_match(
            db, "SHELL", "054", date(2026, 6, 1), date(2026, 6, 30), Decimal("7800"), set()
        )
        assert result[0] == Decimal("7800.00")
        assert result[1] == "value_exact"
        assert result[2][0]["bank_document"] == "TED-20260615-001"


def test_raizen_requires_bank_receipt_or_receivable_settlement_pair():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                AccountingEntry(
                    erp_key="accrual-d", unit_code="054", entry_date=date(2026, 1, 31),
                    account_code="1170810", operation="D", value=Decimal("8500"),
                    journal_lot=1, journal_entry=1, journal_sequence=1,
                ),
                AccountingEntry(
                    erp_key="accrual-c", unit_code="054", entry_date=date(2026, 1, 31),
                    account_code="3111001000037", operation="C", value=Decimal("8500"),
                    journal_lot=1, journal_entry=1, journal_sequence=2,
                ),
            ]
        )
        db.flush()
        no_payment = _bank_or_accounting_match(
            db, "SHELL", "054", date(2026, 1, 1), date(2026, 1, 31), Decimal("8500"), set()
        )
        assert no_payment[0] == Decimal("0")

        db.add_all(
            [
                AccountingEntry(
                    erp_key="settlement-d", unit_code="054", entry_date=date(2026, 2, 26),
                    account_code="1110202000001", operation="D", value=Decimal("8500"),
                    journal_lot=2, journal_entry=1, journal_sequence=1,
                ),
                AccountingEntry(
                    erp_key="settlement-c", unit_code="054", entry_date=date(2026, 2, 26),
                    account_code="1170810", operation="C", value=Decimal("8500"),
                    journal_lot=2, journal_entry=1, journal_sequence=2,
                ),
            ]
        )
        db.flush()
        payment = _bank_or_accounting_match(
            db, "SHELL", "054", date(2026, 2, 1), date(2026, 2, 28), Decimal("8500"), set()
        )
        assert payment[0] == Decimal("8500.00")
        assert payment[1] == "direct"
        assert payment[2][0]["source"] == "accounting_settlement"


def test_raizen_accrual_requires_contractual_appropriation_pair():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                # Contractual recognition: visible, but deliberately non-counted
                # until a utilization or settlement is identified.
                AccountingEntry(
                    erp_key="contract-d", unit_code="054", entry_date=date(2026, 1, 31),
                    account_code="1170810", operation="D", history_code=6513,
                    value=Decimal("8500"), journal_lot=1, journal_entry=1, journal_sequence=1,
                    account_name="BONIFICACAO RAIZEN",
                ),
                AccountingEntry(
                    erp_key="contract-c", unit_code="054", entry_date=date(2026, 1, 31),
                    account_code="3111001000037", operation="C", history_code=6513,
                    value=Decimal("8500"), journal_lot=1, journal_entry=1, journal_sequence=2,
                    account_name="(-)BONIFICACAO CIAS DISTRIBUID.",
                ),
                # A renovation balance and an annual opening carry use the same
                # receivable account, but cannot enter the active contract trail.
                AccountingEntry(
                    erp_key="reform-d", unit_code="054", entry_date=date(2026, 1, 31),
                    account_code="1170810", operation="D", history_code=95,
                    value=Decimal("25172.65"), journal_lot=2, journal_entry=1, journal_sequence=1,
                ),
                AccountingEntry(
                    erp_key="opening-d", unit_code="054", entry_date=date(2026, 1, 31),
                    account_code="1170810", operation="D", history_code=9900,
                    value=Decimal("85500"), journal_lot=3, journal_entry=1, journal_sequence=1,
                ),
                AccountingEntry(
                    erp_key="wrong-counterpart-c", unit_code="054", entry_date=date(2026, 1, 31),
                    account_code="3111001000038", operation="C", history_code=6513,
                    value=Decimal("1000"), journal_lot=4, journal_entry=1, journal_sequence=1,
                ),
                AccountingEntry(
                    erp_key="wrong-counterpart-d", unit_code="054", entry_date=date(2026, 1, 31),
                    account_code="1170810", operation="D", history_code=6513,
                    value=Decimal("1000"), journal_lot=4, journal_entry=1, journal_sequence=2,
                ),
            ]
        )
        db.flush()

        evidence = _raizen_accrual_evidence(db, date(2026, 1, 1), Decimal("8500"))

        assert len(evidence) == 1
        assert evidence[0]["id"] == "contract-d"
        assert evidence[0]["counterpart_entry"] == "contract-c"
        assert evidence[0]["allocation_status"] == "exact_competence"
        assert evidence[0]["counted"] is False


def test_raizen_never_uses_1170810_from_center_050():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                AccountingEntry(
                    erp_key="shellbox-050-d", unit_code="050", entry_date=date(2026, 2, 26),
                    account_code="1110202000001", operation="D", value=Decimal("8500"),
                    journal_lot=50, journal_entry=1, journal_sequence=1,
                ),
                AccountingEntry(
                    erp_key="shellbox-050-c", unit_code="050", entry_date=date(2026, 2, 26),
                    account_code="1170810", operation="C", value=Decimal("8500"),
                    journal_lot=50, journal_entry=1, journal_sequence=2,
                ),
            ]
        )
        db.flush()
        result = _bank_or_accounting_match(
            db, "SHELL", "054", date(2026, 2, 1), date(2026, 2, 28), Decimal("8500"), set()
        )
        assert result[0] == Decimal("0")


def test_br_receipt_pool_deduplicates_bank_and_accounting_and_keeps_quality():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        def journal(prefix, lot, day, text):
            return [
                AccountingEntry(
                    erp_key=f"{prefix}-d", unit_code="002", entry_date=day,
                    account_code="1110203000001", operation="D", value=Decimal("35000"),
                    history_text=text, journal_lot=lot, journal_entry=1, journal_sequence=1,
                ),
                AccountingEntry(
                    erp_key=f"{prefix}-c", unit_code="002", entry_date=day,
                    account_code="3111001000037", operation="C", value=Decimal("35000"),
                    history_text=text, journal_lot=lot, journal_entry=1, journal_sequence=2,
                ),
            ]
        db.add_all(journal("named", 10, date(2025, 10, 10), "VIBRA ENERGIA"))
        db.add_all(journal("generic", 11, date(2025, 7, 23), "BONIFICACAO CIAS REFORMA POSTO"))
        db.add(
            BankEntry(
                id="duplicate-bank", entry_date=date(2025, 10, 10), history_text="VIBRA ENERGIA",
                value=Decimal("35000"), bank_code=1,
            )
        )
        db.flush()
        pool = _br_receipt_pool(db, date(2026, 7, 10))
        assert len(pool) == 2
        assert {item["confidence"] for item in pool} == {"direct"}


def test_br_umbrella_marks_exact_vibra_receipt_as_automatic_and_auditable():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        db.add(
            Purchase(
                erp_entry_id=9201,
                unit_code="002",
                supplier_name="VIBRA ENERGIA S.A.",
                supplier_cnpj="34274233006801",
                mapped_company_code="BR",
                invoice_number="9201",
                purchase_date=date(2025, 3, 10),
                total_liters=Decimal("440000"),
                s10_liters=Decimal("0"),
                gross_value=Decimal("1"),
                net_value=Decimal("1"),
            )
        )
        db.add(
            BankEntry(
                id="vibra-35000",
                entry_date=date(2025, 6, 3),
                history_text="TED-PAG FORNECEDORES - VIBRA ENERGIA",
                value=Decimal("35000"),
                bank_code=33,
                account_number_masked="***1234",
                document="2025060303500000",
            )
        )
        db.flush()

        rebuild_reconciliations(db, today=date(2025, 6, 30))

        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "002", BonusRule.kind == "milestone_bonus"))
        row = db.scalar(
            select(Reconciliation).where(
                Reconciliation.rule_id == rule.id,
                Reconciliation.reference_month == date(2025, 5, 1),
            )
        )
        item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.reconciliation_id == row.id))
        evidence = json.loads(row.evidence_json)

        assert row.expected_value == Decimal("35000.00")
        assert row.observed_value == Decimal("35000.00")
        assert row.status == "confirmed"
        assert row.confirmation_mode == "automatic"
        assert item.status == "auto_confirmed"
        receipt = next(entry for entry in evidence if entry.get("source") == "MExtratoBancoLanc")
        assert receipt["bank_document"] == "2025060303500000"
        assert receipt["counterparty"] == "Vibra Energia"
        assert receipt["before_period_close"] is False


def test_unit_004_uses_explicit_portal_credit_without_double_counting_mdcmp():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        db.add_all(
            [
                Purchase(
                    erp_entry_id=9401,
                    unit_code="004",
                    supplier_name="IPIRANGA PRODUTOS",
                    supplier_cnpj="33337122015906",
                    mapped_company_code="IPIRANGA",
                    invoice_number="9401",
                    purchase_date=date(2025, 8, 28),
                    total_liters=Decimal("10000"),
                    s10_liters=Decimal("0"),
                    gross_value=Decimal("1"),
                    net_value=Decimal("1"),
                ),
                Purchase(
                    erp_entry_id=9402,
                    unit_code="004",
                    supplier_name="IPIRANGA PRODUTOS",
                    supplier_cnpj="33337122015906",
                    mapped_company_code="IPIRANGA",
                    invoice_number="9402",
                    purchase_date=date(2025, 9, 3),
                    total_liters=Decimal("10000"),
                    s10_liters=Decimal("0"),
                    gross_value=Decimal("1"),
                    net_value=Decimal("1"),
                ),
                PortalBonusEvent(
                    id="portal-004-1",
                    unit_code="004",
                    company_code="IPIRANGA",
                    category="postpaid",
                    portal_date=date(2025, 10, 17),
                    value=Decimal("1400"),
                    description="Bonificacao Postecipada",
                    event_key="portal-004-1",
                ),
            ]
        )
        db.flush()

        rebuild_reconciliations(db, today=date(2025, 10, 20))

        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "004", BonusRule.kind == "distributor_credit")
        )
        rows = db.scalars(
            select(Reconciliation)
            .where(Reconciliation.rule_id == rule.id, Reconciliation.expected_value > 0)
            .order_by(Reconciliation.reference_month)
        ).all()
        assert [(row.expected_value, row.observed_value, row.status) for row in rows] == [
            (Decimal("700.00"), Decimal("700.00"), "confirmed"),
            (Decimal("700.00"), Decimal("700.00"), "confirmed"),
        ]
        evidence = json.loads(rows[0].evidence_json)
        portal = next(item for item in evidence if item.get("source") == "IPIRANGA_PORTAL")
        assert portal["allocated"] == 700.0
        assert portal["value"] == 1400.0


def test_probable_match_requires_review_even_when_values_are_equal():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "054"))
        row = _upsert(
            db, rule, date(2026, 6, 1), date(2026, 7, 20), Decimal("100.00"),
            Decimal("100.00"), "probable", [{"source": "mlanc", "id": "candidate-1"}], date(2026, 7, 10),
        )
        assert row.status == "partial"


def test_erp_change_reopens_a_manually_confirmed_reconciliation():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "054"))
        evidence = [{"source": "MExtratoBancoLanc", "id": "bank-1"}]
        row = _upsert(
            db, rule, date(2026, 6, 1), date(2026, 7, 20), Decimal("100.00"),
            Decimal("100.00"), "direct", evidence, date(2026, 7, 10),
        )
        row.confirmed_at = datetime.now(timezone.utc)
        row.confirmed_by = "admin-1"
        row.status = "confirmed"
        db.flush()

        row = _upsert(
            db, rule, date(2026, 6, 1), date(2026, 7, 20), Decimal("100.00"),
            Decimal("90.00"), "direct", evidence, date(2026, 7, 21),
        )
        assert row.confirmed_at is None
        assert row.confirmed_by is None
        assert row.status == "overdue"
        assert "Reaberta automaticamente" in row.notes
