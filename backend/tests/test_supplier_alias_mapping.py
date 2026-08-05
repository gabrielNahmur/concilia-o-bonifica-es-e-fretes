from datetime import date

import pytest
from sqlalchemy import delete

from app.database import Base, SessionLocal, engine
from app.models import Company, SupplierAlias, Unit
from app.services.seed import map_supplier


GLOBAL_CNPJ = "55111111000101"
PRECEDENCE_CNPJ = "55222222000102"


@pytest.fixture
def db():
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        session.execute(
            delete(SupplierAlias).where(
                SupplierAlias.cnpj.in_([GLOBAL_CNPJ, PRECEDENCE_CNPJ])
            )
        )
        if not session.get(Company, "QAGLOBAL"):
            session.add(Company(code="QAGLOBAL", display_name="Global QA"))
        if not session.get(Company, "QALOCAL"):
            session.add(Company(code="QALOCAL", display_name="Local QA"))
        if not session.get(Unit, "991"):
            session.add(
                Unit(
                    code="991",
                    display_name="Unidade Alias QA",
                    state="RS",
                    active=True,
                )
            )
        session.commit()
        yield session
    finally:
        session.close()


def test_map_supplier_uses_global_alias_when_unit_has_no_specific_alias(db):
    db.add(
        SupplierAlias(
            company_code="QAGLOBAL",
            unit_code=None,
            cnpj=GLOBAL_CNPJ,
            effective_from=date(2026, 1, 1),
            active=True,
        )
    )
    db.commit()

    mapped = map_supplier(
        db,
        "991",
        "55.111.111/0001-01",
        None,
        date(2026, 8, 1),
    )

    assert mapped == "QAGLOBAL"


def test_map_supplier_prefers_unit_alias_over_newer_global_alias(db):
    db.add_all(
        [
            SupplierAlias(
                company_code="QAGLOBAL",
                unit_code=None,
                cnpj=PRECEDENCE_CNPJ,
                effective_from=date(2026, 7, 1),
                active=True,
            ),
            SupplierAlias(
                company_code="QALOCAL",
                unit_code="991",
                cnpj=PRECEDENCE_CNPJ,
                effective_from=date(2026, 1, 1),
                active=True,
            ),
        ]
    )
    db.commit()

    mapped = map_supplier(
        db,
        "991",
        PRECEDENCE_CNPJ,
        None,
        date(2026, 8, 1),
    )

    assert mapped == "QALOCAL"
