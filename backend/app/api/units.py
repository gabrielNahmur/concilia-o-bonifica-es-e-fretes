from datetime import date

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.dependencies import CurrentUser, DbSession
from app.models import Unit
from app.services.analytics import get_contract_metrics, get_indefinite_contract_metrics, get_unit_detail


router = APIRouter(prefix="/units", tags=["unidades"])


@router.get("")
def units(db: DbSession, _: CurrentUser):
    contracts = {row["unit_code"]: row for row in get_contract_metrics(db)}
    contracts.update({row["unit_code"]: row for row in get_indefinite_contract_metrics(db)})
    rows = db.scalars(select(Unit).where(Unit.active.is_(True)).order_by(Unit.code)).all()
    return [
        {
            "code": row.code,
            "display_name": row.display_name,
            "cnpj": row.cnpj,
            "city": row.city,
            "state": row.state,
            "brand": row.brand,
            "contract": contracts.get(row.code),
        }
        for row in rows
    ]


@router.get("/{code}")
def unit_detail(code: str, db: DbSession, _: CurrentUser, as_of: date | None = None):
    result = get_unit_detail(db, code.zfill(3), as_of)
    if not result:
        raise HTTPException(status_code=404, detail="Unidade não encontrada")
    return result
