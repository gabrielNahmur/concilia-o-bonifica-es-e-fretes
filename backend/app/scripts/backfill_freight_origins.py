from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.erp_sync import (
    pending_resolved_freight_origin_cnpjs,
    refresh_selected_freight_origins,
)


def run(db: Session) -> int:
    cnpjs = pending_resolved_freight_origin_cnpjs(db, limit=3)
    checked = len(cnpjs)
    enriched = refresh_selected_freight_origins(db, cnpjs)
    db.flush()
    pending = len(pending_resolved_freight_origin_cnpjs(db))
    db.commit()
    print(f"{checked} origem(ns) consultada(s); {enriched} origem(ns) enriquecida(s); {pending} pendente(s).")
    return enriched


def main() -> None:
    with SessionLocal() as db:
        run(db)


if __name__ == "__main__":
    main()
