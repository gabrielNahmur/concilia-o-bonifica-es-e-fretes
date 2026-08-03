from datetime import date
from decimal import Decimal
from io import BytesIO

from reportlab.pdfgen import canvas
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import BonusRule, PayableDocument, Purchase, PurchaseItem, Reconciliation
from app.services.boleto_evidence import import_boleto_evidence, parse_boleto_pdf
from app.services.seed import seed_reference_data


def _boleto_pdf(discount: str = "1.080,00") -> bytes:
    stream = BytesIO()
    pdf = canvas.Canvas(stream)
    lines = [
        "655-6",
        "IPIRANGA PRODUTOS DE PETROLEO SA 0001/737731-2 08/07/2026 13/07/2026",
        "90.589.698/0005-49 DM 55003003085390 - 1 006133708-3 196.894,20",
        f"CONCEDER DESCONTO: R${discount}",
        "GBI COMBUSTIVEIS LTDA.",
    ]
    y = 800
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 24
    pdf.save()
    return stream.getvalue()


def _context(db: Session):
    seed_reference_data(db)
    rule = db.scalar(
        select(BonusRule).where(BonusRule.unit_code == "005", BonusRule.kind == "invoice_discount")
    )
    purchase = Purchase(
        erp_entry_id=900096793,
        unit_code="005",
        supplier_person_id=99,
        supplier_name="IPIRANGA PRODUTOS DE PETROLEO SA",
        supplier_cnpj="33337122015906",
        mapped_company_code="TEXACO",
        invoice_number="3085390",
        invoice_series="3",
        purchase_date=date(2026, 7, 8),
        invoice_issue_date=date(2026, 7, 8),
        total_liters=Decimal("37000"),
        gross_value=Decimal("196894.20"),
        net_value=Decimal("196894.20"),
    )
    purchase.items.extend(
        [
            PurchaseItem(
                item_code="1", sequence=1, description="GASOLINA COMUM", unit="L",
                quantity=Decimal("27000"), unit_value=Decimal("5.27"), total_value=Decimal("142279.20"),
            ),
            PurchaseItem(
                item_code="4", sequence=2, description="GASOLINA ADITIVADA", unit="L",
                quantity=Decimal("10000"), unit_value=Decimal("5.4615"), total_value=Decimal("54615"),
            ),
        ]
    )
    db.add(purchase)
    db.add(
        PayableDocument(
            unit_code="005", person_id=99, title_type="NF", document_id="085390", sequence="01",
            erp_entry_id=purchase.erp_entry_id, invoice_number="3085390",
            document_value=Decimal("196894.20"), other_discount=Decimal("0"),
            issue_date=date(2026, 7, 8), due_date=date(2026, 7, 13),
            balance=Decimal("196894.20"),
        )
    )
    reconciliation = Reconciliation(
        unit_code="005", rule_id=rule.id, reference_month=date(2026, 7, 1),
        due_date=date(2026, 7, 31), expected_value=Decimal("1080"), observed_value=Decimal("0"),
        manual_adjustment=Decimal("0"), difference_value=Decimal("1080"), status="pending",
        confidence="none", evidence_json="[]",
    )
    db.add(reconciliation)
    db.flush()
    return rule, reconciliation


def test_parser_extracts_explicit_discount_and_document_identity():
    parsed = parse_boleto_pdf(_boleto_pdf())
    assert parsed.invoice_number == "3085390"
    assert parsed.title_document_id == "085390"
    assert parsed.payer_cnpj == "90589698000549"
    assert parsed.gross_value == Decimal("196894.20")
    assert parsed.discount_value == Decimal("1080.00")
    assert parsed.due_date == date(2026, 7, 13)


def test_import_links_boleto_to_exact_note_title_and_expected_discount():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        rule, reconciliation = _context(db)
        evidence, duplicate = import_boleto_evidence(
            db,
            reconciliation,
            rule,
            filename="geracaoBoleto.pdf",
            content_type="application/pdf",
            content=_boleto_pdf(),
            uploaded_by="admin-test",
        )
        assert duplicate is False
        assert evidence.match_status == "exact"
        assert evidence.purchase_entry_id == 900096793
        assert evidence.payable_document_id is not None
        assert evidence.expected_discount_value == Decimal("1080.00")
        assert evidence.net_value == Decimal("195814.20")
