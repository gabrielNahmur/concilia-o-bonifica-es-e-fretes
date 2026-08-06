from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import FreightOrigin
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
