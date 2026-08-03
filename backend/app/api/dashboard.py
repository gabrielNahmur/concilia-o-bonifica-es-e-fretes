from datetime import date

from fastapi import APIRouter, Query

from app.dependencies import CurrentUser, DbSession
from app.services.analytics import get_bonus_expected_details, get_dashboard


router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/bonus-details")
def bonus_expected_details(
    db: DbSession,
    _: CurrentUser,
    reference_month: date | None = None,
    unit: list[str] | None = Query(None, description="Repita unit para filtrar múltiplas unidades."),
):
    """Open the expected-bonus card with contractual purchases and titles."""
    return get_bonus_expected_details(db, reference_month, unit)


@router.get("/dashboard")
def dashboard(
    db: DbSession,
    _: CurrentUser,
    reference_month: date | None = None,
    unit: str | None = Query(None, min_length=3, max_length=3),
    brand: str | None = None,
    status: str | None = None,
):
    result = get_dashboard(db, reference_month)
    if unit:
        result["units"] = [row for row in result["units"] if row["code"] == unit]
        result["contracts"] = [row for row in result["contracts"] if row["unit_code"] == unit]
        result["alerts"] = [row for row in result["alerts"] if row.get("unit_code") == unit]
    if brand:
        allowed = {row["code"] for row in result["units"] if (row.get("brand") or "").lower() == brand.lower()}
        result["units"] = [row for row in result["units"] if row["code"] in allowed]
        result["contracts"] = [row for row in result["contracts"] if row["unit_code"] in allowed]
    if status:
        result["contracts"] = [row for row in result["contracts"] if row["status"] == status]
    return result
