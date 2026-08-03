import json
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BonusRule, PortalBonusEvent, Purchase, Reconciliation
from app.scripts.import_raizen_054_receipts import RECEIPTS
from app.services.reconciliation import RAIZEN_RECEIPT_CATEGORY, rebuild_reconciliations
from app.services.reconciliation_detail import build_reconciliation_detail
from app.services.reconciliation_workspace import ALGORITHM_VERSION
from app.services.seed import seed_reference_data


def test_transcribed_raizen_receipt_allocations_exhaust_every_real_ted():
    assert len(ALGORITHM_VERSION) <= 30
    for receipt in RECEIPTS:
        allocated = sum((Decimal(value) for value in receipt["allocations"].values()), Decimal("0"))
        assert allocated == receipt["value"]


def test_unit_054_raizen_receipt_confirms_only_its_declared_exact_competence():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "054",
                BonusRule.kind == "bank_deposit",
            )
        )
        # R$ 0.05/L x 169,000 L = R$ 8,450.00 expected for Feb/2026.
        db.add(
            Purchase(
                erp_entry_id=5402602,
                unit_code="054",
                supplier_name="RAIZEN S.A.",
                supplier_cnpj="033453598000123",
                mapped_company_code="SHELL",
                invoice_number="RAIZEN-02",
                purchase_date=date(2026, 2, 12),
                total_liters=Decimal("169000"),
                s10_liters=Decimal("0"),
                gross_value=Decimal("1000000"),
                net_value=Decimal("1000000"),
            )
        )
        db.add(
            PortalBonusEvent(
                unit_code="054",
                company_code="SHELL",
                category=RAIZEN_RECEIPT_CATEGORY,
                portal_date=date(2026, 3, 27),
                value=Decimal("8450.00"),
                description="TED Raízen - bonificação contratual",
                reference="901088379/2777175813",
                client_cnpj="12564276000262",
                business_classification="contractual_bonus",
                review_notes=json.dumps(
                    {
                        "bank_commitment": "901088379",
                        "client_commitment": "2777175813",
                        "payer_name": "Raízen S.A.",
                        "payer_cnpj": "033453598000123",
                        "beneficiary_name": "STILO COMERCIO DE COMBUSTIVEIS",
                        "beneficiary_cnpj": "12564276000262",
                        "allocations": {"2026-02-01": "8450.00"},
                    }
                ),
                event_key="raizen-receipt-2026-02",
            )
        )

        rebuild_reconciliations(db, today=date(2026, 3, 31), commit=False)
        row = db.scalar(
            select(Reconciliation).where(
                Reconciliation.rule_id == rule.id,
                Reconciliation.reference_month == date(2026, 2, 1),
            )
        )

        assert Decimal(row.expected_value) == Decimal("8450.00")
        assert Decimal(row.observed_value) == Decimal("8450.00")
        assert Decimal(row.difference_value) == Decimal("0.00")
        assert row.status == "confirmed"
        detail = build_reconciliation_detail(db, row, rule)
        assert detail["evidence"][0]["label"] == "Comprovante bancário da Raízen"
        assert detail["evidence"][0]["document"] == "2777175813"
