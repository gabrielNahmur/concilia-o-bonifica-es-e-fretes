from datetime import date, datetime, timezone
from decimal import Decimal
from importlib import import_module

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import Base
from app.api.admin import list_freight_rates
from app.api.freights import _detail_payload
from app.models import FreightCte, FreightCteInvoice, FreightOrigin, FreightRate, FreightReconciliation, Purchase
from app.services import cnpj_registry
from app.services.cnpj_registry import CnpjLocation, CnpjLookupError, refresh_freight_origins
from app.services import erp_sync


def test_refresh_caches_normalized_registered_origin_without_repeat_lookup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls = []

    def fetch(cnpj: str) -> CnpjLocation:
        calls.append(cnpj)
        return CnpjLocation("33453598013705", "RAIZEN S.A.", "Esteio", "RS", "cnpj_ws")

    with Session(engine) as db:
        assert refresh_freight_origins(db, ["33.453.598/0137-05"], fetch) == 1
        db.commit()

        origin = db.get(FreightOrigin, "33453598013705")
        assert origin is not None
        assert origin.cnpj == "33453598013705"
        assert origin.legal_name == "RAIZEN S.A."
        assert origin.city == "Esteio"
        assert origin.state == "RS"
        assert origin.source == "cnpj_ws"

        assert refresh_freight_origins(db, ["33453598013705"], fetch) == 0
        assert calls == ["33453598013705"]


def test_refresh_records_lookup_error_for_retry():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    def fetch(_cnpj: str) -> CnpjLocation:
        raise CnpjLookupError("serviço indisponível")

    with Session(engine) as db:
        assert refresh_freight_origins(db, ["33.453.598/0137-05"], fetch) == 0
        db.commit()

        origin = db.get(FreightOrigin, "33453598013705")
        assert origin is not None
        assert origin.city is None
        assert origin.last_error == "serviço indisponível"
        assert origin.last_lookup_at is not None
        assert origin.next_retry_at is not None


def test_refresh_skips_failed_lookup_until_retry_window_expires():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls = []

    def fetch(cnpj: str) -> CnpjLocation:
        calls.append(cnpj)
        raise CnpjLookupError("serviço indisponível")

    with Session(engine) as db:
        assert refresh_freight_origins(db, ["33.453.598/0137-05"], fetch) == 0
        db.commit()
        origin = db.get(FreightOrigin, "33453598013705")
        first_lookup_at = origin.last_lookup_at
        first_retry_at = origin.next_retry_at

        assert refresh_freight_origins(db, ["33453598013705"], fetch) == 0
        db.commit()
        db.refresh(origin)

        assert calls == ["33453598013705"]
        assert origin.last_lookup_at == first_lookup_at
        assert origin.next_retry_at == first_retry_at
        assert origin.last_error == "serviço indisponível"


def test_refresh_compares_aware_retry_time_as_utc_instant(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    calls = []
    now = datetime(2026, 8, 6, 18, 48, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(cnpj_registry, "utcnow", lambda: now)

    def fetch(cnpj: str) -> CnpjLocation:
        calls.append(cnpj)
        return CnpjLocation(cnpj, "RAIZEN S.A.", "Esteio", "RS", "cnpj_ws")

    with Session(engine) as db:
        db.add(FreightOrigin(cnpj="33453598013705"))
        db.commit()
        origin = db.get(FreightOrigin, "33453598013705")
        origin.next_retry_at = datetime.fromisoformat("2026-08-06T17:48:30-03:00")

        assert refresh_freight_origins(db, ["33453598013705"], fetch) == 0
        assert calls == []


def test_freight_origin_table_rejects_non_numeric_cnpj_outside_refresh_flow():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        with pytest.raises(IntegrityError):
            db.execute(insert(FreightOrigin).values(cnpj="3345359801370X"))
            db.commit()


def _purchase(entry_id: int, cnpj: str) -> Purchase:
    return Purchase(
        erp_entry_id=entry_id,
        unit_code="002",
        supplier_cnpj=cnpj,
        purchase_date=date(2026, 8, 1),
        total_liters=Decimal("0"),
        s10_liters=Decimal("0"),
        gross_value=Decimal("0"),
        net_value=Decimal("0"),
    )


def _cte(erp_cte_id: int) -> FreightCte:
    return FreightCte(
        erp_cte_id=erp_cte_id,
        cte_number=erp_cte_id,
        series="1",
        access_key=str(erp_cte_id).zfill(44),
        issue_date=date(2026, 8, 1),
        unit_code="002",
        destination_cnpj="90589698000204",
        carrier_cnpj="99999999000199",
        source_active=True,
        is_canceled=False,
        charged_service_value=Decimal("0"),
        charged_receivable_value=Decimal("0"),
        charged_net_value=Decimal("0"),
        cargo_value=Decimal("0"),
        cargo_liters=Decimal("0"),
    )


def test_refresh_from_resolved_invoices_limits_pending_supplier_cnpjs_in_order(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    selected = []

    def refresh(_db, cnpjs):
        selected.extend(cnpjs)
        return len(cnpjs)

    monkeypatch.setattr(erp_sync, "refresh_freight_origins", refresh)
    with Session(engine) as db:
        db.add_all(_purchase(index, cnpj) for index, cnpj in enumerate((
            "55555555000155", "11111111000111", "44444444000144", "22222222000122", "33333333000133",
        ), 1))
        db.add_all(_cte(index) for index in range(1, 7))
        db.add_all([
            FreightCteInvoice(erp_cte_id=index, sequence=1, resolved_purchase_entry_id=index)
            for index in range(1, 6)
        ])
        db.add(FreightCteInvoice(erp_cte_id=6, sequence=1, candidate_purchase_entry_id=1))
        db.add(FreightOrigin(cnpj="44444444000144", city="Esteio", state="RS"))
        db.commit()

        assert erp_sync.refresh_origins_from_resolved_freight_invoices(db) == 3

    assert selected == ["11111111000111", "22222222000122", "33333333000133"]


def test_pending_resolved_freight_origin_cnpjs_returns_all_unregistered_cnpjs_in_order(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    selected = []

    def refresh(_db, cnpjs):
        selected.extend(cnpjs)
        return len(cnpjs)

    monkeypatch.setattr(erp_sync, "refresh_freight_origins", refresh)
    with Session(engine) as db:
        db.add_all(_purchase(index, cnpj) for index, cnpj in enumerate((
            "55555555000155", "11111111000111", "44444444000144", "22222222000122", "33333333000133", "0000000000000X",
        ), 1))
        db.add_all(_cte(index) for index in range(1, 7))
        db.add_all([
            FreightCteInvoice(erp_cte_id=index, sequence=1, resolved_purchase_entry_id=index)
            for index in range(1, 7)
        ])
        db.add(FreightOrigin(cnpj="44444444000144", city="Esteio", state="RS"))
        db.commit()

        assert erp_sync.pending_resolved_freight_origin_cnpjs(db) == [
            "11111111000111", "22222222000122", "33333333000133", "55555555000155",
        ]
        assert erp_sync.refresh_origins_from_resolved_freight_invoices(db) == 3

    assert selected == ["11111111000111", "22222222000122", "33333333000133"]


def test_backfill_freight_origins_reports_enriched_and_pending(monkeypatch, capsys):
    script = import_module("app.scripts.backfill_freight_origins")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    refreshed = []

    def refresh(db, cnpjs):
        refreshed.extend(cnpjs)
        for cnpj in cnpjs:
            db.add(FreightOrigin(cnpj=cnpj, city="Esteio", state="RS"))
        return len(cnpjs)

    monkeypatch.setattr(erp_sync, "refresh_freight_origins", refresh)
    with Session(engine, autoflush=False) as db:
        db.add_all(_purchase(index, cnpj) for index, cnpj in enumerate((
            "0000000000000X", "44444444000144", "11111111000111", "33333333000133", "22222222000122",
        ), 1))
        db.add_all(_cte(index) for index in range(1, 6))
        db.add_all(
            FreightCteInvoice(erp_cte_id=index, sequence=1, resolved_purchase_entry_id=index)
            for index in range(1, 6)
        )
        db.commit()
        assert script.run(db) == 3

    output = capsys.readouterr().out
    assert refreshed == ["11111111000111", "22222222000122", "33333333000133"]
    assert "3 origem(ns) consultada(s)" in output
    assert "3 origem(ns) enriquecida(s)" in output
    assert "1 pendente(s)" in output


def test_origin_refresh_failure_does_not_change_freight_reconciliation_snapshot(monkeypatch, caplog):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    def unavailable(_db, _cnpjs):
        raise CnpjLookupError("serviço indisponível")

    monkeypatch.setattr(erp_sync, "refresh_freight_origins", unavailable)
    with Session(engine) as db:
        db.add(_purchase(1, "11111111000111"))
        db.add(_cte(1))
        db.add(FreightCteInvoice(erp_cte_id=1, sequence=1, resolved_purchase_entry_id=1))
        db.add(FreightRate(
            carrier_cnpj="99999999000199", effective_from=date(2026, 1, 1), rate_per_liter=Decimal("0.1"),
        ))
        db.flush()
        db.add(FreightReconciliation(
            erp_cte_id=1, rate_id=1, reference_date=date(2026, 8, 1), matched_liters=Decimal("10"),
            expected_value=Decimal("1"), charged_value=Decimal("2"), difference_value=Decimal("1"),
            primary_status="overcharged", algorithm_version="test", fingerprint="a" * 64,
        ))
        db.commit()
        before = db.scalar(select(FreightReconciliation).where(FreightReconciliation.erp_cte_id == 1))
        snapshot = (before.rate_id, before.expected_value, before.difference_value, before.primary_status)

        assert erp_sync.refresh_origins_from_resolved_freight_invoices(db) == 0
        db.refresh(before)

        assert (before.rate_id, before.expected_value, before.difference_value, before.primary_status) == snapshot
    assert "serviço indisponível" in caplog.text


def test_rate_and_detail_payloads_show_only_resolved_registered_origins():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all((_purchase(1, "11111111000111"), _purchase(2, "22222222000122"), _cte(1)))
        db.add_all((
            FreightCteInvoice(erp_cte_id=1, sequence=1, resolved_purchase_entry_id=1),
            FreightCteInvoice(erp_cte_id=1, sequence=2, resolved_purchase_entry_id=1),
            FreightCteInvoice(erp_cte_id=1, sequence=3, resolved_purchase_entry_id=2),
            FreightCteInvoice(erp_cte_id=1, sequence=4, candidate_purchase_entry_id=2),
        ))
        db.add(FreightOrigin(
            cnpj="11111111000111", legal_name="Fornecedor", city="Esteio", state="RS", source="cnpj_ws",
        ))
        db.add(FreightRate(
            carrier_cnpj="99999999000199", origin_cnpj="11111111000111", effective_from=date(2026, 1, 1), rate_per_liter=Decimal("0.1"),
        ))
        db.add(FreightReconciliation(
            erp_cte_id=1, reference_date=date(2026, 8, 1), matched_liters=Decimal("0"), expected_value=Decimal("0"),
            charged_value=Decimal("0"), difference_value=Decimal("0"), primary_status="pending", algorithm_version="test", fingerprint="b" * 64,
        ))
        db.commit()

        rates = list_freight_rates(db, None)
        row = db.scalar(
            select(FreightReconciliation)
            .where(FreightReconciliation.erp_cte_id == 1)
            .options(selectinload(FreightReconciliation.cte).selectinload(FreightCte.invoices))
        )
        detail = _detail_payload(db, row)

    assert rates[0]["origin"] == {
        "cnpj": "11111111000111", "legal_name": "Fornecedor", "city": "Esteio", "state": "RS", "source": "cnpj_ws",
    }
    assert detail["origins"] == [{
        "cnpj": "11111111000111", "legal_name": "Fornecedor", "city": "Esteio", "state": "RS", "source": "cnpj_ws",
    }, {
        "cnpj": "22222222000122", "legal_name": None, "city": None, "state": None, "source": None,
    }]
