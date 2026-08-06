from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import FreightOrigin
from app.services import cnpj_registry
from app.services.cnpj_registry import CnpjLocation, CnpjLookupError, refresh_freight_origins


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
