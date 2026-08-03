from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.reconciliations import reconciliation_work_queue
from app.database import Base
from app.models import BonusRule, Reconciliation, ReconciliationItem
from app.services.seed import seed_reference_data


def _invoice_item(row: Reconciliation, source_key: str, source_date: date, expected: str) -> ReconciliationItem:
    value = Decimal(expected)
    return ReconciliationItem(
        reconciliation_id=row.id,
        source_key=source_key,
        item_type="invoice",
        source_date=source_date,
        source_document=source_key,
        description=f"NF {source_key}",
        expected_value=value,
        observed_value=value,
        difference_value=Decimal("0"),
        status="auto_confirmed",
        confidence="direct",
        automatic_eligible=True,
        review_status="pending",
        policy_reason="Conciliação confirmada.",
        details_json="{}",
        fingerprint=source_key,
    )


def test_work_queue_hides_neutral_components_and_orders_newest_first():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount"))
        older = Reconciliation(
            unit_code="005", rule_id=rule.id, reference_month=date(2026, 5, 1), due_date=date(2026, 5, 31),
            expected_value=Decimal("100"), observed_value=Decimal("100"), manual_adjustment=Decimal("0"),
            difference_value=Decimal("0"), status="confirmed", confidence="direct", evidence_json="[]",
        )
        newer = Reconciliation(
            unit_code="005", rule_id=rule.id, reference_month=date(2026, 6, 1), due_date=date(2026, 6, 30),
            expected_value=Decimal("60"), observed_value=Decimal("60"), manual_adjustment=Decimal("0"),
            difference_value=Decimal("0"), status="confirmed", confidence="direct", evidence_json="[]",
        )
        db.add_all((older, newer))
        db.flush()
        db.add_all(
            (
                _invoice_item(older, "NF-OLDER", date(2026, 5, 10), "100"),
                _invoice_item(older, "NF-ZERO", date(2026, 5, 11), "0"),
                _invoice_item(newer, "NF-NEWER", date(2026, 6, 10), "60"),
            )
        )
        db.flush()

        payload = reconciliation_work_queue(
            db=db,
            _=None,
            unit="005",
            scope="all",
            page=1,
            page_size=50,
        )

        assert [item["document"] for item in payload["items"]] == ["NF-NEWER", "NF-OLDER"]
        assert all(item["expected_value"] > 0 for item in payload["items"])
        assert all(item["reason"].startswith("Conciliação confirmada") for item in payload["items"])


def test_work_queue_uses_the_erp_title_due_date_for_each_invoice():
    """Monthly reconciliation due dates must not replace a linked boleto due date."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount"))
        reconciliation = Reconciliation(
            unit_code="005", rule_id=rule.id, reference_month=date(2026, 8, 1), due_date=date(2026, 8, 31),
            expected_value=Decimal("920"), observed_value=Decimal("0"), manual_adjustment=Decimal("0"),
            difference_value=Decimal("920"), status="pending", confidence="none", evidence_json="[]",
        )
        db.add(reconciliation)
        db.flush()
        item = _invoice_item(reconciliation, "3096076", date(2026, 8, 1), "920")
        item.status = "pending"
        item.automatic_eligible = False
        item.details_json = '{"title_due_date":"2026-08-03","decision_code":"awaiting_payment"}'
        db.add(item)
        db.flush()

        payload = reconciliation_work_queue(
            db=db,
            _=None,
            unit="005",
            scope="waiting",
            page=1,
            page_size=50,
        )

        assert payload["total"] == 1
        assert payload["items"][0]["document"] == "3096076"
        assert payload["items"][0]["due_date"] == date(2026, 8, 3)


def test_partial_credit_before_contractual_deadline_waits_instead_of_requiring_action():
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
        row = Reconciliation(
            unit_code="003",
            rule_id=rule.id,
            reference_month=date.today().replace(day=1),
            due_date=date.today() + timedelta(days=14),
            expected_value=Decimal("100"),
            observed_value=Decimal("60"),
            manual_adjustment=Decimal("0"),
            difference_value=Decimal("40"),
            status="partial",
            confidence="probable",
            evidence_json="[]",
        )
        db.add(row)
        db.flush()

        actionable = reconciliation_work_queue(db=db, _=None, unit="003", scope="actionable", page=1, page_size=50)
        waiting = reconciliation_work_queue(db=db, _=None, unit="003", scope="waiting", page=1, page_size=50)

        assert actionable["total"] == 0
        assert waiting["total"] == 1
        assert waiting["items"][0]["action_label"] == "Aguardar extrato Ipiranga"
        assert "crédito postecipado no extrato Ipiranga" in waiting["items"][0]["reason"]
