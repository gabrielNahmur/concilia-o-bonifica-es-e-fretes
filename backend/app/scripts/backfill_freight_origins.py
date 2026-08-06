from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.erp_sync import (
    pending_resolved_freight_origin_cnpjs,
    refresh_origins_from_resolved_freight_invoices,
)


def run(db: Session) -> int:
    checked = len(pending_resolved_freight_origin_cnpjs(db))
    enriched = refresh_origins_from_resolved_freight_invoices(db)
    pending = len(pending_resolved_freight_origin_cnpjs(db))
    db.commit()
    print(f"{checked} origem(ns) consultada(s); {enriched} origem(ns) enriquecida(s); {pending} pendente(s).")
    return enriched


def main() -> None:
    with SessionLocal() as db:
        run(db)


if __name__ == "__main__":
    main()
