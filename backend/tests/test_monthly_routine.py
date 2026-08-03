from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.monthly_routine import _parse_raizen_receipt_pages, build_monthly_routine
from app.database import Base
from app.models import BonusRule, PortalStatementImport, Reconciliation
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


def test_monthly_routine_treats_covered_external_file_as_analysis_not_missing_source():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_reference_data(db)
        rule = _rule(db, "004", "distributor_credit")
        db.add(_reconciliation(rule, "004", expected="700.00", observed="0.00", status="pending"))
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

        payload = build_monthly_routine(db, reference_months=[date(2026, 7, 1)])
        card = next(row for row in payload["cards"] if row["unit_code"] == "004")
        assert card["situation"] == "analysis"
        assert len(card["imports"]) == 1


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
