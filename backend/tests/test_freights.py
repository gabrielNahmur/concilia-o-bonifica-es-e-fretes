import json
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import (
    FreightCte,
    FreightCteInvoice,
    FreightRate,
    FreightReconciliation,
    FreightReview,
    PayableDocument,
    Purchase,
    PurchaseItem,
    User,
)
from app.security import hash_password
from app.api.freights import _statement, freight_card_details, freight_summary, list_freights
from app.services.freight_reconciliation import rebuild_freight_reconciliations
from app.services.seed import seed_reference_data


RATE = Decimal("0.1525")


def test_freight_list_uses_database_pagination_and_summary_totals_all_rows():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        for index in range(55):
            erp_id = 90_000 + index
            db.add(_cte(erp_id, 10_000 + index, "002", "10", "100", issue=date(2026, 6, 1)))
            db.add(
                FreightReconciliation(
                    erp_cte_id=erp_id,
                    reference_date=date(2026, 6, 1),
                    matched_liters=Decimal("100"),
                    expected_value=Decimal("10"),
                    charged_value=Decimal("10"),
                    difference_value=Decimal("0"),
                    primary_status="correct",
                    algorithm_version="test",
                    fingerprint=str(index).zfill(64),
                )
            )
        db.commit()

        page = list_freights(db, None, competence="2026-06", page=2, page_size=50)
        summary = freight_summary(db, None, competence="2026-06")

        assert page["total"] == 55
        assert page["pages"] == 2
        assert len(page["items"]) == 5
        assert summary["total_ctes"] == 55
        assert summary["liters"] == 5_500


def _purchase(
    entry_id,
    unit,
    number,
    key,
    liters,
    issue=date(2026, 6, 12),
    supplier_cnpj="11111111000111",
):
    return Purchase(
        erp_entry_id=entry_id,
        unit_code=unit,
        supplier_name="DISTRIBUIDORA TESTE",
        supplier_cnpj=supplier_cnpj,
        invoice_number=str(number),
        invoice_series="1",
        access_key=key,
        purchase_date=issue,
        invoice_issue_date=issue,
        total_liters=Decimal(str(liters)),
        s10_liters=Decimal("0"),
        gross_value=Decimal("60000"),
        net_value=Decimal("60000"),
    )


def _cte(erp_id, number, unit, charged, cargo, *, canceled=False, issue=date(2026, 6, 12), carrier="40080594000102"):
    cnpjs = {
        "002": "90589698000204",
        "005": "90589698000549",
        "012": "90589698000972",
        "013": "90589698001197",
        "054": "12564276000262",
    }
    return FreightCte(
        erp_cte_id=erp_id,
        cte_number=number,
        series="1",
        access_key=str(erp_id).zfill(44),
        issue_date=issue,
        unit_code=unit,
        destination_cnpj=cnpjs[unit],
        carrier_person_id=77,
        carrier_cnpj=carrier,
        carrier_name="TRR PAMPA DIESEL LTDA",
        sender_cnpj="11111111000111",
        sender_name="DISTRIBUIDORA TESTE",
        purpose=0,
        source_status="C" if canceled else "N",
        is_canceled=canceled,
        source_active=True,
        charged_service_value=Decimal(str(charged)),
        charged_receivable_value=Decimal(str(charged)),
        charged_net_value=Decimal(str(charged)),
        cargo_value=Decimal("60000"),
        cargo_liters=Decimal(str(cargo)),
    )


def _supplemental(erp_id, number, unit, charged, issue, carrier):
    cte = _cte(erp_id, number, unit, charged, "0", issue=issue, carrier=carrier)
    cte.source_kind = "purchase_entry"
    cte.source_entry_id = abs(erp_id)
    cte.sender_cnpj = None
    cte.sender_name = None
    cte.cargo_liters = Decimal("0")
    cte.cargo_value = Decimal("0")
    return cte


def _payable(erp_cte_id, unit, value, sequence="01"):
    return PayableDocument(
        unit_code=unit,
        person_id=77,
        title_type="CT",
        document_id=str(erp_cte_id)[-6:],
        sequence=sequence,
        erp_cte_id=erp_cte_id,
        invoice_number=str(erp_cte_id),
        document_value=Decimal(str(value)),
        other_discount=Decimal("0"),
        issue_date=date(2026, 6, 12),
        due_date=date(2026, 6, 20),
        payment_date=date(2026, 6, 20),
        balance=Decimal("0"),
    )


def test_freight_v2_uses_invoice_liters_and_classifies_cent_differences():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        seeded_rate = db.scalar(select(FreightRate).where(FreightRate.rate_per_liter == RATE))
        assert seeded_rate.effective_from == date(2026, 3, 1)
        cases = [
            (8801, 2376, "2" * 44, "10000", "13000", "1525.00", "correct"),
            (8802, 2382, "3" * 44, "13000", "10000", "1982.51", "overcharged"),
            (8803, 2453, "4" * 44, "5000", "13000", "762.49", "undercharged"),
        ]
        for erp_id, number, key, liters, cargo, charged, _ in cases:
            purchase = _purchase(erp_id + 10000, "002", number + 100000, key, liters)
            db.add(purchase)
            db.flush()
            db.add(PurchaseItem(erp_entry_id=purchase.erp_entry_id, item_code="1", sequence=1, description="COMBUSTÍVEL", unit="L", quantity=Decimal(liters), unit_value=Decimal("6"), total_value=Decimal("60000")))
            db.add(_cte(erp_id, number, "002", charged, cargo))
            db.add(FreightCteInvoice(erp_cte_id=erp_id, sequence=1, reference_access_key=key))
            db.add(_payable(erp_id, "002", charged))
        db.commit()
        assert rebuild_freight_reconciliations(db) == 3
        db.commit()
        for erp_id, _, _, liters, _, _, expected_status in cases:
            row = db.scalar(select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == erp_id))
            assert row.matched_liters == Decimal(liters).quantize(Decimal("0.001"))
            assert row.primary_status == expected_status
            assert any(issue["code"] == "cargo_volume_mismatch" for issue in json.loads(row.issues_json))


def test_seed_bootstraps_rates_once_and_never_overwrites_manager_changes():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        rates = db.scalars(
            select(FreightRate)
            .where(FreightRate.carrier_cnpj == "40080594000102")
            .order_by(FreightRate.effective_from)
        ).all()
        assert [(row.effective_from, row.effective_to, row.rate_per_liter) for row in rates] == [
            (date(2026, 1, 1), date(2026, 2, 28), Decimal("0.142500")),
            (date(2026, 3, 1), None, Decimal("0.152500")),
        ]
        current = rates[1]
        current.effective_from = date(2026, 7, 1)
        current.rate_per_liter = Decimal("0.160000")
        db.commit()

        seed_reference_data(db)
        refreshed = db.scalars(
            select(FreightRate)
            .where(FreightRate.carrier_cnpj == "40080594000102")
            .order_by(FreightRate.effective_from)
        ).all()
        assert len(refreshed) == 2
        assert refreshed[1].effective_from == date(2026, 7, 1)
        assert refreshed[1].rate_per_liter == Decimal("0.160000")


def test_invoice_reference_date_controls_competence_and_rate():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        key = "1" * 44
        db.add(_purchase(28048, "002", 4200716, key, "10000", date(2026, 3, 30)))
        db.add(_cte(8048, 2148, "002", "1525", "10000", issue=date(2026, 4, 10)))
        db.add(
            FreightCteInvoice(
                erp_cte_id=8048,
                sequence=1,
                reference_access_key=key,
                reference_invoice_number="4200716",
                reference_issue_date=date(2026, 3, 30),
            )
        )
        db.add(_payable(8048, "002", "1525"))
        db.commit()

        rebuild_freight_reconciliations(db)
        db.commit()

        row = db.scalar(select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == 8048))
        assert row.reference_date == date(2026, 3, 30)
        assert row.expected_value == Decimal("1525.00")
        assert row.primary_status == "correct"
        assert row.algorithm_version == "freight-v5"
        assert len(db.scalars(_statement("2026-03", None, None, None, None)).all()) == 1
        assert len(db.scalars(_statement("2026-04", None, None, None, None)).all()) == 0


def test_access_key_month_controls_competence_when_document_date_is_missing():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        db.add(_cte(8098, 2098, "002", "762.50", "5000", issue=date(2026, 4, 3)))
        db.add(
            FreightCteInvoice(
                erp_cte_id=8098,
                sequence=1,
                reference_access_key="432603" + "1" * 38,
            )
        )
        db.commit()

        rebuild_freight_reconciliations(db)
        db.commit()

        row = db.scalar(select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == 8098))
        assert row.reference_date == date(2026, 3, 1)
        assert row.primary_status == "document_mismatch"
        assert any(issue["code"] == "reference_month_from_access_key" for issue in json.loads(row.issues_json))
        assert len(db.scalars(_statement("2026-03", None, None, None, None)).all()) == 1
        assert len(db.scalars(_statement("2026-04", None, None, None, None)).all()) == 0


def test_dashboard_statement_hides_canceled_ctes_but_keeps_audit_record():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        active = _cte(8101, 1901, "002", "883", "5000")
        canceled = _cte(8102, 1888, "002", "2565", "10000", canceled=True)
        db.add_all((active, canceled))
        db.commit()
        rebuild_freight_reconciliations(db)
        db.commit()

        rows = db.scalars(_statement("2026-06", None, None, None, None)).all()
        assert [row.erp_cte_id for row in rows] == [8101]
        canceled_row = db.scalar(
            select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == 8102)
        )
        assert canceled_row is not None
        assert canceled_row.primary_status == "canceled"


def test_duplicate_invoice_is_never_consumed_by_two_ctes_and_unpriced_is_explicit():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        key = "5" * 44
        db.add(_purchase(28900, "002", 500100, key, "10000", date(2026, 5, 12)))
        for erp_id, number in ((8901, 2501), (8902, 2502)):
            db.add(_cte(erp_id, number, "002", "1525", "10000", issue=date(2026, 5, 12)))
            db.add(FreightCteInvoice(erp_cte_id=erp_id, sequence=1, reference_access_key=key))
        db.add(_purchase(28903, "012", 500103, "6" * 44, "10000", date(2025, 8, 2)))
        db.add(_cte(8903, 2503, "012", "1525", "10000", issue=date(2025, 8, 31)))
        db.add(FreightCteInvoice(erp_cte_id=8903, sequence=1, reference_access_key="6" * 44))
        db.commit()
        rebuild_freight_reconciliations(db)
        db.commit()
        duplicates = db.scalars(select(FreightReconciliation).where(FreightReconciliation.erp_cte_id.in_([8901, 8902]))).all()
        assert all(row.primary_status == "document_mismatch" for row in duplicates)
        assert all(row.matched_liters == Decimal("0.000") for row in duplicates)
        unpriced = db.scalar(
            select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == 8903)
        )
        assert unpriced.primary_status == "unpriced"
        assert unpriced.expected_value == Decimal("0.00")
        assert unpriced.difference_value == Decimal("0.00")


def test_zero_verified_liters_never_becomes_a_false_freight_difference():
    """A tariff is not an expected value until a purchase NF-e proves litres."""
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        # The CT-e has a valid tariff for unit 002, but its NF-e key points to
        # a purchase recorded in another unit. This is the real-world shape of
        # the confusing "0 L expected / R$ difference" case.
        foreign_key = "9" * 44
        db.add(_purchase(301001, "012", 700001, foreign_key, "10000", date(2026, 6, 12)))
        db.add(_cte(301002, 2426, "002", "1525", "10000", issue=date(2026, 6, 12)))
        db.add(FreightCteInvoice(erp_cte_id=301002, sequence=1, reference_access_key=foreign_key))
        db.commit()

        rebuild_freight_reconciliations(db)
        db.commit()

        row = db.scalar(select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == 301002))
        assert row.matched_liters == Decimal("0.000")
        assert row.expected_value == Decimal("0.00")
        assert row.difference_value == Decimal("0.00")
        assert row.primary_status == "document_mismatch"
        issues = json.loads(row.issues_json)
        assert any(issue["code"] == "missing_verified_liters" for issue in issues)
        listed = list_freights(db, None, competence=["2026-06"], unit=["002"], page=1, page_size=50)
        item = next(item for item in listed["items"] if item["erp_cte_id"] == 301002)
        assert item["rate_available"] is True
        assert item["comparison_available"] is False
        assert item["has_rate"] is False
        assert item["difference_value"] == 0.0
        summary = freight_summary(db, None, competence=["2026-06"], unit=["002"])
        assert summary["difference"] == 0.0
        assert summary["uncomparable_ctes"] >= 1


def test_freight_filters_accept_multi_select_and_card_details_use_same_scope():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        for erp_id, unit, reference_date, status in (
            (302001, "002", date(2026, 6, 1), "correct"),
            (302002, "005", date(2026, 7, 1), "overcharged"),
            (302003, "012", date(2026, 7, 1), "correct"),
        ):
            db.add(_cte(erp_id, erp_id, unit, "10", "100", issue=reference_date))
            db.add(
                FreightReconciliation(
                    erp_cte_id=erp_id,
                    reference_date=reference_date,
                    matched_liters=Decimal("100"),
                    expected_value=Decimal("10"),
                    charged_value=Decimal("10"),
                    difference_value=Decimal("0"),
                    rate_id=1,
                    primary_status=status,
                    algorithm_version="test",
                    fingerprint=str(erp_id).zfill(64),
                )
            )
        db.commit()
        scoped = _statement(["2026-06,2026-07"], ["002", "005"], None, None, None)
        assert {row.erp_cte_id for row in db.scalars(scoped).all()} == {302001, 302002}
        detail = freight_card_details(
            db,
            None,
            metric="pending",
            competence=["2026-06", "2026-07"],
            unit=["002", "005"],
            carrier=None,
            status=None,
            page=1,
            page_size=50,
        )
        assert detail["total"] == 1
        assert detail["items"][0]["erp_cte_id"] == 302002

def test_vkl_monthly_batch_maps_all_remaining_invoices_and_is_idempotent():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        db.add_all(
            (
                _purchase(50101, "005", 101, "1" * 44, "37000", date(2026, 5, 2)),
                _purchase(50102, "005", 102, "2" * 44, "107000", date(2026, 5, 19)),
                _supplemental(-5100, 500, "005", "18576", date(2026, 5, 31), "26313088000195"),
                FreightCteInvoice(erp_cte_id=-5100, sequence=1),
            )
        )
        db.commit()

        rebuild_freight_reconciliations(db)
        db.commit()
        rebuild_freight_reconciliations(db)
        db.commit()

        row = db.scalar(select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == -5100))
        references = db.scalars(
            select(FreightCteInvoice)
            .where(FreightCteInvoice.erp_cte_id == -5100)
            .order_by(FreightCteInvoice.sequence)
        ).all()
        assert row.reference_date == date(2026, 5, 2)
        assert row.matched_liters == Decimal("144000.000")
        assert row.primary_status == "unpriced"
        assert [reference.resolved_purchase_entry_id for reference in references] == [50101, 50102]
        assert all(reference.resolution_source == "automatic" for reference in references)


def test_brondani_dispatch_calendar_keeps_consecutive_supplier_routes_separate():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        old_origin = "33453598011400"
        new_origin = "33453598013705"
        db.add_all(
            (
                _purchase(54001, "054", 57172, "3" * 44, "20000", date(2026, 6, 2), old_origin),
                _purchase(54002, "054", 1415529, "4" * 44, "20000", date(2026, 6, 5), new_origin),
                _supplemental(-5458, 1458, "054", "2595", date(2026, 6, 5), "52935802000197"),
                _supplemental(-5465, 1465, "054", "460", date(2026, 6, 6), "52935802000197"),
                FreightCteInvoice(erp_cte_id=-5458, sequence=1),
                FreightCteInvoice(erp_cte_id=-5465, sequence=1),
                FreightRate(
                    carrier_cnpj="52935802000197",
                    carrier_name="PAULO BRONDANI TRANSPORTES",
                    origin_cnpj=old_origin,
                    unit_code="054",
                    effective_from=date(2026, 4, 1),
                    rate_per_liter=Decimal("0.023"),
                    active=True,
                ),
            )
        )
        db.commit()

        rebuild_freight_reconciliations(db)
        db.commit()

        first = db.scalar(select(FreightCteInvoice).where(FreightCteInvoice.erp_cte_id == -5458))
        second = db.scalar(select(FreightCteInvoice).where(FreightCteInvoice.erp_cte_id == -5465))
        assert first.resolved_purchase_entry_id == 54002
        assert second.resolved_purchase_entry_id == 54001
        assert first.resolution_source == second.resolution_source == "automatic"
        first_reconciliation = db.scalar(
            select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == -5458)
        )
        second_reconciliation = db.scalar(
            select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == -5465)
        )
        assert first_reconciliation.rate_id is None
        assert first_reconciliation.primary_status == "unpriced"
        assert second_reconciliation.rate_id is not None
        assert second_reconciliation.expected_value == Decimal("460.00")
        assert second_reconciliation.difference_value == Decimal("0.00")


def test_talisma_batch_matches_nominal_volume_and_leaves_surcharge_unresolved():
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        seed_reference_data(db)
        talisma_origin = "33337122009698"
        rows = [
            _purchase(13001, "013", 307700, "5" * 44, "9990", date(2026, 3, 30), talisma_origin),
            _purchase(13002, "013", 307829, "6" * 44, "9990", date(2026, 4, 18), talisma_origin),
            _purchase(13003, "013", 308217, "7" * 44, "8500", date(2026, 5, 19), talisma_origin),
            _supplemental(-13009, 9, "013", "17640", date(2026, 4, 14), "08825400000148"),
            _supplemental(-13021, 21, "013", "400", date(2026, 6, 23), "08825400000148"),
            _supplemental(-13027, 27, "013", "60", date(2026, 6, 23), "08825400000148"),
            _supplemental(-13028, 28, "013", "340", date(2026, 6, 23), "08825400000148"),
        ]
        rows.extend(
            FreightCteInvoice(erp_cte_id=erp_id, sequence=1)
            for erp_id in (-13009, -13021, -13027, -13028)
        )
        db.add_all(rows)
        db.commit()

        rebuild_freight_reconciliations(db)
        db.commit()

        matches = {
            reference.erp_cte_id: reference
            for reference in db.scalars(
                select(FreightCteInvoice).where(
                    FreightCteInvoice.erp_cte_id.in_((-13009, -13021, -13027, -13028))
                )
            ).all()
        }
        assert matches[-13009].resolved_purchase_entry_id == 13001
        assert matches[-13021].resolved_purchase_entry_id == 13002
        assert matches[-13028].resolved_purchase_entry_id == 13003
        assert matches[-13027].resolved_purchase_entry_id is None
        assert matches[-13027].match_status == "missing_reference"


def test_supplemental_without_fuel_is_removed_from_freight_reconciliation():
    """An avulsa freight expense must not enter fuel freight dashboards."""
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(local_engine)
    with Session(local_engine) as db:
        supplemental = _supplemental(
            -13027,
            27,
            "013",
            "60",
            date(2026, 6, 23),
            "08825400000148",
        )
        db.add_all(
            (
                supplemental,
                FreightCteInvoice(erp_cte_id=supplemental.erp_cte_id, sequence=1),
                FreightReconciliation(
                    erp_cte_id=supplemental.erp_cte_id,
                    reference_date=date(2026, 6, 23),
                    matched_liters=Decimal("0"),
                    expected_value=Decimal("0"),
                    charged_value=Decimal("60"),
                    difference_value=Decimal("0"),
                    primary_status="document_mismatch",
                    algorithm_version="freight-v5",
                    fingerprint="legacy".zfill(64),
                ),
            )
        )
        db.commit()

        rebuild_freight_reconciliations(db)
        db.commit()

        assert db.get(FreightCte, supplemental.erp_cte_id) is not None
        assert db.scalar(
            select(FreightCteInvoice).where(FreightCteInvoice.erp_cte_id == supplemental.erp_cte_id)
        ) is not None
        assert db.scalar(
            select(FreightReconciliation).where(
                FreightReconciliation.erp_cte_id == supplemental.erp_cte_id
            )
        ) is None


def test_cte_2426_suggests_only_nf_4200716_and_admin_confirmation_is_audited():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        for email, role in (("freight.admin@gbi.com", "admin"), ("freight.viewer@gbi.com", "viewer")):
            if not db.scalar(select(User).where(User.email == email)):
                db.add(User(email=email, full_name=f"Freight {role}", role=role, active=True, must_change_password=False, password_hash=hash_password("SenhaSegura123!")))
        direct_key = "7" * 44
        candidate_key = "8" * 44
        if not db.get(Purchase, 324250):
            direct = _purchase(324250, "012", 3075300, direct_key, "8000")
            candidate = _purchase(324260, "002", 4200716, candidate_key, "10000")
            db.add_all((direct, candidate))
            db.flush()
            db.add_all((
                PurchaseItem(erp_entry_id=direct.erp_entry_id, item_code="1", sequence=1, description="GASOLINA", unit="L", quantity=Decimal("8000"), unit_value=Decimal("6"), total_value=Decimal("48000")),
                PurchaseItem(erp_entry_id=candidate.erp_entry_id, item_code="1", sequence=1, description="GASOLINA", unit="L", quantity=Decimal("10000"), unit_value=Decimal("6"), total_value=Decimal("60000")),
            ))
            db.add_all((
                _cte(32425, 2425, "012", "1220", "8000"),
                _cte(32426, 2426, "002", "1525", "10000"),
                FreightCteInvoice(erp_cte_id=32425, sequence=1, reference_access_key=direct_key),
                FreightCteInvoice(erp_cte_id=32426, sequence=1, reference_access_key=direct_key),
                _payable(32425, "012", "1220"),
                _payable(32426, "002", "1525"),
            ))
        db.commit()
        rebuild_freight_reconciliations(db)
        db.commit()
        mismatch = db.scalar(select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == 32426))
        assert mismatch.primary_status == "document_mismatch"
        reference = db.scalar(select(FreightCteInvoice).where(FreightCteInvoice.erp_cte_id == 32426))
        assert reference.resolved_purchase_entry_id is None
        assert reference.candidate_purchase_entry_id == 324260
        reconciliation_id, fingerprint = mismatch.id, mismatch.fingerprint

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"email": "freight.viewer@gbi.com", "password": "SenhaSegura123!"}).status_code == 200
        assert client.get(f"/api/freights/{reconciliation_id}").status_code == 200
        search = client.get("/api/freights", params={"search": "2426", "page_size": 200})
        assert search.status_code == 200
        assert search.json()["total"] == 1
        assert search.json()["items"][0]["cte_number"] == 2426
        multi = client.get(
            "/api/freights",
            params=[("competence", "2026-06,2026-07"), ("unit", "002,012"), ("page_size", "200")],
        )
        assert multi.status_code == 200, multi.text
        assert any(item["cte_number"] == 2426 for item in multi.json()["items"])
        june_only = client.get(
            "/api/freights",
            params=[("competence", "2026-06"), ("page_size", "200")],
        )
        assert june_only.status_code == 200, june_only.text
        assert june_only.json()["items"]
        assert all(str(item["reference_date"]).startswith("2026-06") for item in june_only.json()["items"])
        june_summary = client.get("/api/freights/summary", params=[("competence", "2026-06")])
        assert june_summary.status_code == 200, june_summary.text
        assert june_summary.json()["total_ctes"] == june_only.json()["total"]
        drill_down = client.get(
            "/api/freights/card-details",
            params=[("metric", "pending"), ("competence", "2026-06"), ("unit", "002")],
        )
        assert drill_down.status_code == 200, drill_down.text
        assert any(item["cte_number"] == 2426 for item in drill_down.json()["items"])
        assert client.post(f"/api/freights/{reconciliation_id}/review", json={"action": "confirm", "notes": "NF correta confirmada", "candidate_purchase_entry_id": 324260, "fingerprint": fingerprint}).status_code == 403
        assert client.get("/api/admin/freight-rates").status_code == 403
        assert client.get("/api/admin/freight-carriers").status_code == 403
        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={"email": "freight.admin@gbi.com", "password": "SenhaSegura123!"}).status_code == 200
        carriers = client.get("/api/admin/freight-carriers")
        assert carriers.status_code == 200
        assert any(row["carrier_cnpj"] == "40080594000102" for row in carriers.json())
        response = client.post(f"/api/freights/{reconciliation_id}/review", json={"action": "confirm", "notes": "NF 4200716 confere com unidade, data e 10.000 litros.", "candidate_purchase_entry_id": 324260, "fingerprint": fingerprint})
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["primary_status"] == "correct"
        assert detail["matched_liters"] == 10000.0
        assert detail["expected_value"] == 1525.0
        assert detail["difference_value"] == 0.0
        assert detail["invoices"][0]["resolved"]["invoice_number"] == "4200716"
        overlap = client.post("/api/admin/freight-rates", json={
            "carrier_cnpj": "40080594000102", "carrier_name": "TRR PAMPA DIESEL LTDA",
            "origin_cnpj": None, "unit_code": None, "effective_from": "2026-07-01",
            "effective_to": None, "rate_per_liter": 0.16, "active": True,
        })
        assert overlap.status_code == 409
        stale = client.post(f"/api/freights/{reconciliation_id}/review", json={"action": "justify", "notes": "Tentativa em fotografia antiga", "candidate_purchase_entry_id": None, "fingerprint": fingerprint})
        assert stale.status_code == 409
    with SessionLocal() as db:
        reviews = db.scalars(select(FreightReview).where(FreightReview.reconciliation_id == reconciliation_id)).all()
        assert len(reviews) == 1
        assert reviews[0].action == "confirm"
