from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BonusRule, Reconciliation
from app.services.reconciliation import _next_month_ipiranga_portal_credit_evidence, rebuild_reconciliations
from app.services.seed import seed_reference_data


def test_deactivated_rule_supersedes_operational_reconciliations_without_deleting_history():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "001",
                BonusRule.kind == "distributor_credit",
            )
        )
        rebuild_reconciliations(db, today=date(2025, 1, 31))
        rows = db.scalars(select(Reconciliation).where(Reconciliation.rule_id == rule.id)).all()
        assert rows

        rule.active = False
        rebuild_reconciliations(db, today=date(2025, 1, 31), commit=False)

        assert all(row.status == "superseded" for row in rows)
        assert all("desativada" in row.notes for row in rows)


def test_unit_003_maps_every_postpaid_credit_from_the_next_calendar_month():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "003",
                BonusRule.kind == "distributor_credit",
            )
        )
        pool = [
            {"id": "may", "date": date(2026, 5, 7), "remaining": Decimal("5580"), "raw_value": Decimal("5580"), "source": "IPIRANGA_PORTAL", "document": None},
            {"id": "june", "date": date(2026, 6, 8), "remaining": Decimal("4980"), "raw_value": Decimal("4980"), "source": "IPIRANGA_PORTAL", "document": None},
            {"id": "july", "date": date(2026, 7, 9), "remaining": Decimal("5400"), "raw_value": Decimal("5400"), "source": "IPIRANGA_PORTAL", "document": "084522", "invoice_number": "3084522", "chain_exact": True},
        ]
        specs = [
            {"reference_month": date(2026, 3, 1), "expected": Decimal("2760"), "due": date(2026, 4, 30)},
            {"reference_month": date(2026, 4, 1), "expected": Decimal("5100"), "due": date(2026, 5, 31)},
            {"reference_month": date(2026, 5, 1), "expected": Decimal("5460"), "due": date(2026, 6, 30)},
            {"reference_month": date(2026, 6, 1), "expected": Decimal("5400"), "due": date(2026, 7, 31)},
        ]

        april_observed, april_evidence = _next_month_ipiranga_portal_credit_evidence(
            pool, date(2026, 4, 1), date(2026, 5, 31)
        )
        june_observed, june_evidence = _next_month_ipiranga_portal_credit_evidence(
            pool, date(2026, 6, 1), date(2026, 7, 31)
        )

        assert april_observed == Decimal("5580.00")
        assert april_evidence[0]["id"] == "may"
        assert june_observed == Decimal("5400.00")
        assert june_evidence[0]["id"] == "july"
        assert june_evidence[0]["document"] == "084522"
        assert june_evidence[0]["invoice_number"] == "3084522"
        assert pool[2]["remaining"] == Decimal("5400")


def test_unit_004_keeps_a_non_exact_next_month_credit_as_a_real_difference():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "004",
                BonusRule.kind == "distributor_credit",
            )
        )
        pool = [
            {
                "id": "july-7350",
                "date": date(2026, 7, 16),
                "remaining": Decimal("7350"),
                "raw_value": Decimal("7350"),
                "source": "IPIRANGA_PORTAL",
                "document": None,
                "portal_event_key": "portal:004:7350",
            }
        ]
        observed, evidence = _next_month_ipiranga_portal_credit_evidence(
            pool, date(2026, 6, 1), date(2026, 7, 31)
        )

        assert observed == Decimal("7350.00")
        assert evidence[0]["source"] == "IPIRANGA_PORTAL"
        assert evidence[0]["value"] == 7350.0
        assert evidence[0]["date"] == "2026-07-16"
        assert pool[0]["remaining"] == Decimal("7350")
