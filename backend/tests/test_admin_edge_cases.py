from sqlalchemy import select
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Unit, User
from app.security import hash_password
from app.services.seed import seed_reference_data


PASSWORD = "SenhaSegura123!"


def _user(email: str, name: str, role: str = "viewer") -> User:
    return User(
        email=email,
        full_name=name,
        role=role,
        active=True,
        must_change_password=False,
        password_hash=hash_password(PASSWORD),
    )


def test_admin_duplicates_are_conflicts_and_self_lockout_is_blocked():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == "edge.admin@gbi.com"))
        if not admin:
            admin = _user("edge.admin@gbi.com", "Admin de Borda", "admin")
            db.add(admin)
        viewer = db.scalar(select(User).where(User.email == "edge.viewer@gbi.com"))
        if not viewer:
            viewer = _user("edge.viewer@gbi.com", "Viewer de Borda")
            db.add(viewer)
        db.commit()
        admin_id = admin.id
        viewer_id = viewer.id

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login",
            json={"email": "edge.admin@gbi.com", "password": PASSWORD},
        ).status_code == 200

        first = client.post(
            "/api/admin/recipients",
            json={"name": "Diretoria Borda", "email": "edge.report@gbi.com", "active": True},
        )
        assert first.status_code == 200
        duplicate = client.post(
            "/api/admin/recipients",
            json={"name": "Diretoria Duplicada", "email": "EDGE.REPORT@gbi.com", "active": True},
        )
        assert duplicate.status_code == 409

        duplicate_user_email = client.patch(
            f"/api/admin/users/{viewer_id}",
            json={
                "email": "EDGE.ADMIN@gbi.com",
                "full_name": "Viewer de Borda",
                "role": "viewer",
                "active": True,
            },
        )
        assert duplicate_user_email.status_code == 409

        self_deactivate = client.patch(
            f"/api/admin/users/{admin_id}",
            json={
                "email": "edge.admin@gbi.com",
                "full_name": "Admin de Borda",
                "role": "admin",
                "active": False,
            },
        )
        assert self_deactivate.status_code == 400
        self_demote = client.patch(
            f"/api/admin/users/{admin_id}",
            json={
                "email": "edge.admin@gbi.com",
                "full_name": "Admin de Borda",
                "role": "viewer",
                "active": True,
            },
        )
        assert self_demote.status_code == 400
        assert client.get("/api/admin/users").status_code == 200


def test_configuration_rejects_unknown_references_and_duplicate_unit_cnpj():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        admin = db.scalar(select(User).where(User.email == "config.edge.admin@gbi.com"))
        if not admin:
            db.add(_user("config.edge.admin@gbi.com", "Admin Configuração", "admin"))
        db.commit()

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login",
            json={"email": "config.edge.admin@gbi.com", "password": PASSWORD},
        ).status_code == 200

        unknown_unit = client.post(
            "/api/admin/contracts",
            json={
                "unit_code": "999",
                "company_code": "IPIRANGA",
                "start_date": "2026-01-01",
                "term_months": 12,
                "total_liters": 1000,
                "status": "active",
            },
        )
        assert unknown_unit.status_code == 400
        assert unknown_unit.json()["detail"] == "Unidade não encontrada"

        unknown_company = client.post(
            "/api/admin/rules",
            json={
                "unit_code": "001",
                "company_code": "INEXISTENTE",
                "kind": "distributor_credit",
                "effective_from": "2026-01-01",
                "rate_per_liter": 0.08,
            },
        )
        assert unknown_company.status_code == 400
        assert unknown_company.json()["detail"] == "Companhia não encontrada"

        invalid_alias = client.post(
            "/api/admin/aliases",
            json={
                "unit_code": "001",
                "company_code": "IPIRANGA",
                "cnpj": "123",
                "effective_from": "2026-01-01",
                "active": True,
            },
        )
        assert invalid_alias.status_code == 400
        assert invalid_alias.json()["detail"] == "CNPJ do fornecedor deve conter 14 dígitos"

        invalid_period = client.post(
            "/api/admin/rules",
            json={
                "unit_code": "001",
                "company_code": "IPIRANGA",
                "kind": "distributor_credit",
                "effective_from": "2026-03-01",
                "effective_to": "2026-02-01",
            },
        )
        assert invalid_period.status_code == 422

        with SessionLocal() as db:
            units = db.scalars(select(Unit).where(Unit.cnpj.is_not(None)).order_by(Unit.code)).all()
            assert len(units) >= 2
            source_cnpj = units[0].cnpj
            target_code = units[1].code
        duplicate_cnpj = client.patch(
            f"/api/admin/units/{target_code}",
            json={"cnpj": source_cnpj},
        )
        assert duplicate_cnpj.status_code == 409
        assert duplicate_cnpj.json()["detail"] == "CNPJ já cadastrado em outra unidade"

        null_display_name = client.patch(
            "/api/admin/units/001",
            json={"display_name": None},
        )
        assert null_display_name.status_code == 422

        empty_normalized_alias = client.post(
            "/api/admin/aliases",
            json={
                "unit_code": "001",
                "company_code": "IPIRANGA",
                "cnpj": "---",
                "effective_from": "2026-01-01",
                "active": True,
            },
        )
        assert empty_normalized_alias.status_code == 400
        assert empty_normalized_alias.json()["detail"] == "Informe um CNPJ ou trecho do nome do fornecedor"


def test_viewer_can_read_monthly_routine_without_import_permissions():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        viewer = db.scalar(select(User).where(User.email == "routine.edge.viewer@gbi.com"))
        if not viewer:
            db.add(_user("routine.edge.viewer@gbi.com", "Viewer Rotina"))
        db.commit()

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login",
            json={"email": "routine.edge.viewer@gbi.com", "password": PASSWORD},
        ).status_code == 200
        assert client.get("/api/monthly-routine").status_code == 200
        assert client.post(
            "/api/monthly-routine/imports/ipiranga",
            data={"unit_code": "001"},
        ).status_code == 403
