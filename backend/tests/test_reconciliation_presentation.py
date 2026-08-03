import json
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BonusRule, Reconciliation
from app.services.reconciliation_detail import build_reconciliation_detail
from app.services.seed import seed_reference_data


def _row(rule, unit_code: str, expected: str, evidence: list[dict]) -> Reconciliation:
    value = Decimal(expected)
    return Reconciliation(
        unit_code=unit_code,
        rule_id=rule.id,
        reference_month=date(2026, 1, 1),
        due_date=date(2026, 2, 20),
        expected_value=value,
        observed_value=value,
        manual_adjustment=Decimal("0"),
        difference_value=Decimal("0"),
        status="confirmed",
        confidence="direct",
        evidence_json=json.dumps(evidence),
    )


def test_detail_hides_legacy_umbrella_calculation_from_ipiranga_credit():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(BonusRule.unit_code == "008", BonusRule.kind == "distributor_credit")
        )
        row = _row(
            rule,
            "008",
            "6150",
            [
                {
                    "source": "calculation",
                    "period_start": "2025-09-01",
                    "period_end": "2025-12-26",
                    "allocated_liters": 123000,
                    "umbrella_group": "BR_002_006",
                },
                {
                    "source": "IPIRANGA_PORTAL",
                    "id": "portal-008-dec",
                    "date": "2026-01-03",
                    "allocated": 6150,
                },
            ],
        )
        db.add(row)
        db.flush()

        detail = build_reconciliation_detail(db, row, rule)

        assert [item["source"] for item in detail["evidence"]] == ["IPIRANGA_PORTAL"]
        assert all("guarda-chuva" not in str(item).lower() for item in detail["evidence"])
        assert len(detail["technical_evidence"]) == 2


def test_raizen_detail_uses_one_exact_settlement_as_primary_proof():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(select(BonusRule).where(BonusRule.unit_code == "054", BonusRule.kind == "bank_deposit"))
        row = _row(
            rule,
            "054",
            "8500",
            [
                {
                    "source": "accounting_settlement",
                    "id": "settlement-054-jan",
                    "date": "2026-02-26",
                    "value": 8500,
                    "allocated": 8500,
                    "receivable_account": "1170810",
                    "bank_account": "1110202000001",
                },
                {
                    "source": "accounting_accrual",
                    "id": "accrual-054-jan",
                    "date": "2026-01-31",
                    "value": 11500,
                    "receivable_account": "1170810",
                    "counterpart_account": "3111001000037",
                },
            ],
        )
        db.add(row)
        db.flush()

        detail = build_reconciliation_detail(db, row, rule)

        assert len(detail["evidence"]) == 1
        assert detail["evidence"][0]["source"] == "accounting_settlement"
        assert detail["evidence"][0]["value"] == 8500.0
        assert len(detail["technical_evidence"]) == 2
