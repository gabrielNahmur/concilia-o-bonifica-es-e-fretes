from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.monthly_routine import _parse_raizen_receipt_pages, build_monthly_routine
from app.database import Base
from app.models import (
    BonusRule,
    ManualAdjustment,
    PortalBonusEvent,
    PortalBonusEventSource,
    PortalBonusMatch,
    PortalStatementImport,
    Purchase,
    Reconciliation,
    ReconciliationAllocation,
    ReconciliationEvidence,
    ReconciliationItem,
)
from app.services.seed import seed_reference_data


def _rule(db: Session, unit: str, kind: str) -> BonusRule:
    return db.scalar(select(BonusRule).where(BonusRule.unit_code == unit, BonusRule.kind == kind))


def _reconciliation(rule: BonusRule, unit: str, *, expected, observed, status, mode="none") -> Reconciliation:
    return Reconciliation(
        unit_code=unit,
        rule_id=rule.id,
        reference_month=date(2026, 7, 1),
        due_date=date(2026, 8, 20),
        expected_value=Decimal(expected),
        observed_value=Decimal(observed),
        difference_value=Decimal(expected) - Decimal(observed),
        status=status,
        confidence="direct" if observed else "none",
        confirmation_mode=mode,
    )


def test_monthly_routine_exposes_only_bonus_rules_and_source_matrix():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule_001 = _rule(db, "001", "distributor_credit")
        rule_003 = _rule(db, "003", "distributor_credit")
        rule_050 = _rule(db, "050", "s10_excess_credit")
        db.add_all(
            [
                _reconciliation(rule_001, "001", expected="100.00", observed="0.00", status="pending"),
                _reconciliation(rule_003, "003", expected="120.00", observed="0.00", status="pending"),
                _reconciliation(rule_050, "050", expected="300.00", observed="0.00", status="pending"),
            ]
        )
        db.commit()

        payload = build_monthly_routine(db, reference_months=[date(2026, 7, 1)])
        cards = {(row["unit_code"], row["rule_kind"]): row for row in payload["cards"]}

        assert cards[("001", "distributor_credit")]["source_type"] == "ipiranga_portal"
        assert cards[("001", "distributor_credit")]["situation"] == "awaiting_source"
        assert cards[("003", "distributor_credit")]["source_type"] == "ipiranga_portal"
        assert cards[("003", "distributor_credit")]["situation"] == "awaiting_source"
        assert cards[("050", "s10_excess_credit")]["source_type"] == "texaco_portal"
        assert cards[("050", "s10_excess_credit")]["situation"] == "awaiting_source"
        assert all(row["unit_code"] not in {"012", "013", "051", "052"} for row in payload["cards"])


def test_monthly_routine_aggregates_ipiranga_statement_units_once_per_rule():
    """The operational view is cumulative, not a repeated card per month."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "003", "distributor_credit")
        db.add_all(
            [
                Reconciliation(
                    unit_code="003",
                    rule_id=rule.id,
                    reference_month=date(2026, 4, 1),
                    due_date=date(2026, 5, 31),
                    expected_value=Decimal("300.00"),
                    observed_value=Decimal("0.00"),
                    difference_value=Decimal("300.00"),
                    status="pending",
                    confidence="none",
                ),
                Reconciliation(
                    unit_code="003",
                    rule_id=rule.id,
                    reference_month=date(2026, 5, 1),
                    due_date=date(2026, 6, 30),
                    expected_value=Decimal("420.00"),
                    observed_value=Decimal("0.00"),
                    difference_value=Decimal("420.00"),
                    status="pending",
                    confidence="none",
                ),
                PortalBonusEvent(
                    id="portal-003-apr",
                    event_key="portal-003-apr",
                    unit_code="003",
                    company_code="IPIRANGA",
                    category="postpaid",
                    portal_date=date(2026, 5, 10),
                    value=Decimal("300.00"),
                ),
                PortalBonusEvent(
                    id="portal-003-may",
                    event_key="portal-003-may",
                    unit_code="003",
                    company_code="IPIRANGA",
                    category="postpaid",
                    portal_date=date(2026, 6, 10),
                    value=Decimal("420.00"),
                ),
            ]
        )
        db.flush()
        reconciliations = db.scalars(
            select(Reconciliation)
            .where(Reconciliation.rule_id == rule.id)
            .order_by(Reconciliation.reference_month)
        ).all()
        items = [
            ReconciliationItem(
                reconciliation_id=row.id,
                item_type="competence",
                source_key=f"competence:{row.reference_month.isoformat()}",
                source_date=row.reference_month,
                source_document=None,
                description=f"Competência {row.reference_month.isoformat()}",
                expected_value=row.expected_value,
                observed_value=Decimal("0.00"),
                difference_value=row.expected_value,
                status="pending",
                confidence="none",
                automatic_eligible=False,
                fingerprint=f"item-{row.reference_month.isoformat()}",
            )
            for row in reconciliations
        ]
        db.add_all(items)
        db.flush()
        evidence = [
            ReconciliationEvidence(
                id="evidence-003-apr",
                source_type="IPIRANGA_PORTAL",
                source_key="portal-003-apr",
                evidence_date=date(2026, 5, 10),
                unit_code="003",
                document_id=None,
                value=Decimal("300.00"),
                counted=True,
                identity_json="{}",
                fingerprint="evidence-003-apr",
            ),
            ReconciliationEvidence(
                id="evidence-003-may",
                source_type="IPIRANGA_PORTAL",
                source_key="portal-003-may",
                evidence_date=date(2026, 6, 10),
                unit_code="003",
                document_id=None,
                value=Decimal("420.00"),
                counted=True,
                identity_json="{}",
                fingerprint="evidence-003-may",
            ),
        ]
        db.add_all(evidence)
        db.flush()
        db.add_all(
            [
                ReconciliationAllocation(
                    item_id=items[0].id,
                    evidence_id=evidence[0].id,
                    allocated_value=Decimal("300.00"),
                    match_status="automatic",
                    confidence="direct",
                    match_basis="Crédito do portal apropriado à competência.",
                    algorithm_version="test",
                ),
                ReconciliationAllocation(
                    item_id=items[1].id,
                    evidence_id=evidence[1].id,
                    allocated_value=Decimal("420.00"),
                    match_status="automatic",
                    confidence="direct",
                    match_basis="Crédito do portal apropriado à competência.",
                    algorithm_version="test",
                ),
            ]
        )
        db.add(
            PortalStatementImport(
                unit_code="003",
                company_code="IPIRANGA",
                category="postpaid",
                client_cnpj="90589698000468",
                period_start=date(2026, 4, 1),
                period_end=date(2026, 6, 30),
                original_filename="extrato-003.pdf",
                content_type="application/pdf",
                content_sha256="c" * 64,
                source_file=b"source",
                row_count=2,
                imported_count=2,
                duplicate_count=0,
                uploaded_by="admin",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        payload = build_monthly_routine(
            db,
            reference_months=[date(2026, 4, 1), date(2026, 5, 1)],
            today=date(2026, 6, 30),
        )
        cards = [
            card for card in payload["cards"]
            if card["unit_code"] == "003" and card["rule_kind"] == "distributor_credit"
        ]

        assert len(cards) == 1
        assert cards[0]["expected_value"] == 720.0
        assert cards[0]["observed_value"] == 720.0
        assert cards[0]["difference_value"] == 0.0
        assert cards[0]["situation"] == "automatic"
        assert cards[0]["credit_event_count"] == 2
        assert cards[0]["imports"][0]["original_filename"] == "extrato-003.pdf"


def test_monthly_routine_cumulative_uses_allocated_portal_credit_not_residual_wallet_balance():
    """A portal residual cannot make a fully appropriated wallet look divergent."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "003", "distributor_credit")
        reconciliations = [
            Reconciliation(
                unit_code="003",
                rule_id=rule.id,
                reference_month=date(2026, month, 1),
                due_date=date(2026, month, 28),
                expected_value=Decimal("100.00"),
                observed_value=Decimal("0.00"),
                manual_adjustment=Decimal("100.00") if month == 4 else Decimal("0.00"),
                difference_value=Decimal("0.00") if month == 4 else Decimal("100.00"),
                status="confirmed" if month == 4 else "pending",
                confidence="direct" if month == 4 else "none",
            )
            for month in (4, 5, 6)
        ]
        db.add_all(reconciliations)
        db.flush()
        items = [
            ReconciliationItem(
                reconciliation_id=reconciliations[index].id,
                item_type="competence",
                source_key=f"competence:2026-{month:02d}",
                source_date=date(2026, month, 1),
                source_document=None,
                description=f"Competência {month:02d}/2026",
                expected_value=Decimal("100.00"),
                observed_value=Decimal("0.00"),
                difference_value=Decimal("100.00"),
                status="pending",
                confidence="none",
                automatic_eligible=False,
                fingerprint=f"item-{month}",
            )
            for index, month in enumerate((4, 5, 6))
        ]
        db.add_all(items)
        db.add_all(
            [
                PortalBonusEvent(
                    id="portal-residual",
                    event_key="portal-residual",
                    unit_code="003",
                    company_code="IPIRANGA",
                    category="postpaid",
                    portal_date=date(2026, 5, 10),
                    value=Decimal("150.00"),
                ),
                PortalBonusEvent(
                    id="portal-full",
                    event_key="portal-full",
                    unit_code="003",
                    company_code="IPIRANGA",
                    category="postpaid",
                    portal_date=date(2026, 6, 10),
                    value=Decimal("100.00"),
                ),
            ]
        )
        db.flush()
        evidence = [
            ReconciliationEvidence(
                id="evidence-residual",
                source_type="IPIRANGA_PORTAL",
                source_key="portal-residual",
                evidence_date=date(2026, 5, 10),
                unit_code="003",
                document_id=None,
                value=Decimal("150.00"),
                counted=True,
                identity_json="{}",
                fingerprint="evidence-residual",
            ),
            ReconciliationEvidence(
                id="evidence-full",
                source_type="IPIRANGA_PORTAL",
                source_key="portal-full",
                evidence_date=date(2026, 6, 10),
                unit_code="003",
                document_id=None,
                value=Decimal("100.00"),
                counted=True,
                identity_json="{}",
                fingerprint="evidence-full",
            ),
        ]
        db.add_all(evidence)
        db.flush()
        db.add_all(
            [
                ReconciliationAllocation(
                    item_id=items[1].id,
                    evidence_id=evidence[0].id,
                    allocated_value=Decimal("100.00"),
                    match_status="automatic",
                    confidence="direct",
                    match_basis="Crédito apropriado à competência de maio.",
                    algorithm_version="test",
                ),
                ReconciliationAllocation(
                    item_id=items[2].id,
                    evidence_id=evidence[1].id,
                    allocated_value=Decimal("100.00"),
                    match_status="automatic",
                    confidence="direct",
                    match_basis="Crédito apropriado à competência de junho.",
                    algorithm_version="test",
                ),
            ]
        )
        db.commit()

        payload = build_monthly_routine(
            db,
            reference_months=[date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)],
            today=date(2026, 6, 30),
        )
        card = next(row for row in payload["cards"] if row["unit_code"] == "003")

        assert card["observed_value"] == 200.0
        assert card["portal_credit_total_value"] == 250.0
        assert card["portal_unallocated_value"] == 50.0
        assert card["difference_value"] == 0.0
        assert card["situation"] == "automatic"


def test_monthly_routine_exposes_the_latest_import_and_issued_credit_dates_separately():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        statement_import = PortalStatementImport(
            unit_code="008",
            company_code="IPIRANGA",
            category="contract_parcels_report",
            client_cnpj="00905896980008",
            period_start=date(2022, 6, 27),
            period_end=date(2026, 6, 26),
            original_filename="portal-008-20260724.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content_sha256="statement-008".ljust(64, "0"),
            source_file=b"source",
            imported_count=48,
            uploaded_by="admin",
            created_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        portal_event = PortalBonusEvent(
            id="portal-008-jun",
            event_key="portal-008-jun",
            unit_code="008",
            company_code="IPIRANGA",
            category="postpaid",
            portal_date=date(2026, 6, 28),
            value=Decimal("4000.00"),
        )
        db.add_all([statement_import, portal_event])
        db.flush()
        db.add(
            PortalBonusEventSource(
                import_id=statement_import.id,
                event_id=portal_event.id,
                row_number=48,
                raw_json="{}",
            )
        )
        db.commit()

        payload = build_monthly_routine(
            db,
            reference_months=[date(2026, 7, 1)],
            today=date(2026, 8, 5),
        )
        card = next(row for row in payload["cards"] if row["unit_code"] == "008")

        assert card["latest_import"]["created_at"].date() == date(2026, 7, 24)
        assert card["latest_import"]["latest_credit_date"] == date(2026, 6, 28)


def test_seed_starts_unit_050_s10_bonus_only_in_august_2026():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "050", "s10_excess_credit")

        assert rule.effective_from == date(2026, 8, 1)


def test_monthly_routine_treats_unit_004_credit_still_in_deadline_as_waiting():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "004", "distributor_credit")
        db.add(_reconciliation(rule, "004", expected="700.00", observed="0.00", status="pending"))
        db.add(
            Purchase(
                erp_entry_id=400101,
                unit_code="004",
                supplier_name="IPIRANGA",
                supplier_cnpj="33337122015906",
                mapped_company_code="IPIRANGA",
                invoice_number="400101",
                purchase_date=date(2026, 7, 15),
                total_liters=Decimal("10000"),
                s10_liters=Decimal("0"),
                gross_value=Decimal("1"),
                net_value=Decimal("1"),
            )
        )
        db.add(
            PortalStatementImport(
                unit_code="004",
                company_code="IPIRANGA",
                category="postpaid",
                client_cnpj="90589698000468",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
                original_filename="extrato.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                content_sha256="a" * 64,
                source_file=b"source",
                row_count=1,
                imported_count=1,
                duplicate_count=0,
                uploaded_by="admin",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        payload = build_monthly_routine(db, reference_months=[date(2026, 7, 1)], today=date(2026, 8, 3))
        card = next(row for row in payload["cards"] if row["unit_code"] == "004")
        assert card["situation"] == "awaiting_due"
        assert card["action"] == "Aguardar competência ainda no prazo"
        assert "dentro do prazo contratual" in card["description"]
        assert "state=waiting" in card["queue_url"]
        assert len(card["imports"]) == 1


def test_monthly_routine_marks_unit_004_matured_balance_for_analysis():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "004", "distributor_credit")
        db.add(_reconciliation(rule, "004", expected="700.00", observed="0.00", status="pending"))
        db.add(
            Purchase(
                erp_entry_id=400102,
                unit_code="004",
                supplier_name="IPIRANGA",
                supplier_cnpj="33337122015906",
                mapped_company_code="IPIRANGA",
                invoice_number="400102",
                purchase_date=date(2026, 7, 15),
                total_liters=Decimal("10000"),
                s10_liters=Decimal("0"),
                gross_value=Decimal("1"),
                net_value=Decimal("1"),
            )
        )
        db.add(
            PortalStatementImport(
                unit_code="004",
                company_code="IPIRANGA",
                category="postpaid",
                client_cnpj="90589698000468",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
                original_filename="extrato.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                content_sha256="b" * 64,
                source_file=b"source",
                row_count=1,
                imported_count=1,
                duplicate_count=0,
                uploaded_by="admin",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        payload = build_monthly_routine(db, reference_months=[date(2026, 7, 1)], today=date(2026, 8, 21))
        card = next(row for row in payload["cards"] if row["unit_code"] == "004")

        assert card["situation"] == "analysis"
        assert "saldo acumulado" in card["description"]
        assert "state=actionable" in card["queue_url"]


def test_monthly_routine_hides_contractually_excluded_reconciliation():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "005", "invoice_discount")
        db.add(_reconciliation(rule, "005", expected="4880.00", observed="4880.00", status="not_applicable"))
        db.commit()

        payload = build_monthly_routine(db, reference_months=[date(2026, 7, 1)])

        assert not [row for row in payload["cards"] if row["unit_code"] == "005"]


def test_monthly_routine_does_not_count_historical_exclusions_as_received_bonus():
    """A historical exclusion must not inflate the value identified in the routine."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "005", "invoice_discount")
        reconciliation = Reconciliation(
            unit_code="005",
            rule_id=rule.id,
            reference_month=date(2026, 4, 1),
            due_date=date(2026, 4, 30),
            expected_value=Decimal("3880.00"),
            observed_value=Decimal("3880.00"),
            manual_adjustment=Decimal("2200.00"),
            difference_value=Decimal("-2200.00"),
            status="divergent",
            confidence="direct",
        )
        db.add(reconciliation)
        db.flush()
        db.add_all(
            [
                ReconciliationItem(
                    reconciliation_id=reconciliation.id,
                    item_type="invoice",
                    source_key=f"entry:{document}",
                    source_date=date(2026, 4, 1),
                    source_document=document,
                    description=f"NF {document}",
                    expected_value=value,
                    observed_value=value,
                    difference_value=Decimal("0.00"),
                    status="auto_confirmed",
                    confidence="direct",
                    automatic_eligible=True,
                    fingerprint=document * 8,
                )
                for document, value in (
                    ("3042886", Decimal("1400.00")),
                    ("3045926", Decimal("1280.00")),
                    ("3055131", Decimal("1200.00")),
                )
            ]
        )
        db.add_all(
            [
                ManualAdjustment(
                    reconciliation_id=reconciliation.id,
                    amount=Decimal("1200.00"),
                    reason="Exclusão histórica aprovada: NF fora da conciliação contratual.",
                    created_by="admin",
                ),
                ManualAdjustment(
                    reconciliation_id=reconciliation.id,
                    amount=Decimal("1000.00"),
                    reason="Exclusão histórica aprovada: NF fora da conciliação contratual.",
                    created_by="admin",
                ),
            ]
        )
        db.commit()

        payload = build_monthly_routine(db, reference_months=[date(2026, 4, 1)])
        card = next(row for row in payload["cards"] if row["unit_code"] == "005")

        assert card["observed_value"] == 3880.0
        assert card["difference_value"] == 0.0
        assert card["situation"] == "automatic"
        assert "state=confirmed" in card["queue_url"]


def test_monthly_routine_marks_unpaid_invoice_remainder_as_awaiting_settlement():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "014", "invoice_discount")
        reconciliation = _reconciliation(rule, "014", expected="400.00", observed="0.00", status="pending")
        reconciliation.due_date = date(2026, 7, 31)
        db.add(reconciliation)
        db.flush()
        db.add(
            ReconciliationItem(
                reconciliation_id=reconciliation.id,
                item_type="invoice",
                source_key="entry:1",
                source_date=date(2026, 7, 31),
                source_document="NF 3095263",
                description="NF 3095263",
                expected_value=Decimal("400.00"),
                observed_value=Decimal("0.00"),
                difference_value=Decimal("400.00"),
                status="pending",
                confidence="none",
                automatic_eligible=False,
                fingerprint="c" * 64,
            )
        )
        db.commit()

        payload = build_monthly_routine(db, reference_months=[date(2026, 7, 1)], today=date(2026, 8, 4))
        card = next(row for row in payload["cards"] if row["unit_code"] == "014")

        assert card["situation"] == "awaiting_settlement"
        assert card["action"] == "Aguardar baixa"
        assert "não representa cobrança" in card["description"]
        assert "state=waiting" in card["queue_url"]


def test_monthly_routine_004_separates_historical_residual_from_next_statement():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        db.add_all(
            [
                Purchase(
                    erp_entry_id=400001,
                    unit_code="004",
                    supplier_name="IPIRANGA",
                    supplier_cnpj="33337122015906",
                    mapped_company_code="IPIRANGA",
                    invoice_number="400001",
                    purchase_date=date(2026, 5, 26),
                    total_liters=Decimal("1122000"),
                    s10_liters=Decimal("0"),
                    gross_value=Decimal("1"),
                    net_value=Decimal("1"),
                ),
                Purchase(
                    erp_entry_id=400002,
                    unit_code="004",
                    supplier_name="IPIRANGA",
                    supplier_cnpj="33337122015906",
                    mapped_company_code="IPIRANGA",
                    invoice_number="400002",
                    purchase_date=date(2026, 6, 15),
                    total_liters=Decimal("10000"),
                    s10_liters=Decimal("0"),
                    gross_value=Decimal("1"),
                    net_value=Decimal("1"),
                ),
            ]
        )
        event = PortalBonusEvent(
            id="portal-004-history",
            event_key="portal-004-history",
            unit_code="004",
            company_code="IPIRANGA",
            category="postpaid",
            portal_date=date(2026, 7, 16),
            value=Decimal("78470.01"),
        )
        db.add(event)
        db.flush()
        db.add(
            PortalBonusMatch(
                event_id=event.id,
                status="portal_usage_confirmed",
                purchase_entry_id=400001,
                match_basis="Detalhe do portal informa a NF utilizada.",
                details_json='{"usage_invoice_number":"400001"}',
                algorithm_version="portal-usage-v1",
            )
        )
        db.commit()

        payload = build_monthly_routine(
            db,
            reference_months=[date(2026, 7, 1)],
            today=date(2026, 8, 4),
        )
        card = next(row for row in payload["cards"] if row["unit_code"] == "004")

        assert card["historical_adjustment_value"] == 69.99
        assert card["next_statement_expected_value"] == 700.0
        assert card["observed_value"] == 78470.01
        assert card["situation"] == "awaiting_statement"


def test_monthly_routine_uses_one_unit_004_card_when_no_source_has_been_imported():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "004", "distributor_credit")
        row = _reconciliation(rule, "004", expected="7700.00", observed="0.00", status="pending")
        row.due_date = date(2026, 7, 31)
        db.add(row)
        db.add(
            Purchase(
                erp_entry_id=400103,
                unit_code="004",
                supplier_name="IPIRANGA",
                supplier_cnpj="33337122015906",
                mapped_company_code="IPIRANGA",
                invoice_number="400103",
                purchase_date=date(2026, 7, 15),
                total_liters=Decimal("110000"),
                s10_liters=Decimal("0"),
                gross_value=Decimal("1"),
                net_value=Decimal("1"),
            )
        )
        db.commit()

        payload = build_monthly_routine(db, reference_months=[date(2026, 7, 1)], today=date(2026, 8, 4))
        card = next(row for row in payload["cards"] if row["unit_code"] == "004")

        assert card["situation"] == "awaiting_source"
        assert "Ainda não há extrato externo" in card["description"]
        assert "state=waiting" in card["queue_url"]


def test_raizen_receipt_parser_reads_auditable_payment_identity():
    page = """
    Empresa Pagadora\nNome: Raizen S.A.\nCNPJ: 033.453.598/0001-23\n
    Beneficiário: STILO COMERCIO DE COMBUSTIVEIS\nCPF/CNPJ: 012.564.276/0002-62\n
    Nº compromisso banco Nº compromisso Cliente Data do Crédito Valor\n
    901088379 2777175813 27/03/2026 8.450,00
    """

    # The parser is isolated from pypdf so the exact textual contract can be
    # validated without generating a PDF fixture in the test suite.
    from unittest.mock import MagicMock, patch

    fake_reader = MagicMock()
    fake_page = MagicMock()
    fake_page.extract_text.return_value = page
    fake_reader.pages = [fake_page]
    with patch("app.api.monthly_routine.PdfReader", return_value=fake_reader):
        rows = _parse_raizen_receipt_pages(b"%PDF-test")

    assert rows == [
        {
            "page": 1,
            "bank_commitment": "901088379",
            "client_commitment": "2777175813",
            "credit_date": date(2026, 3, 27),
            "value": Decimal("8450.00"),
            "payer_name": "Raízen S.A.",
            "payer_cnpj": "033453598000123",
            "beneficiary_cnpj": "12564276000262",
        }
    ]
