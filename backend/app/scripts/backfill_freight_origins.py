from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.erp_sync import (
    pending_resolved_freight_origin_cnpjs,
    refresh_selected_freight_origins,
    unresolved_resolved_freight_origin_cnpjs,
)


def run(db: Session) -> int:
    cnpjs = pending_resolved_freight_origin_cnpjs(db, limit=3)
    checked, enriched = refresh_selected_freight_origins(db, cnpjs)
    db.flush()
    pending_total = len(unresolved_resolved_freight_origin_cnpjs(db))
    eligible_now = len(pending_resolved_freight_origin_cnpjs(db))
    db.commit()
    print(
        f"{checked} origem(ns) consultada(s); {enriched} origem(ns) enriquecida(s); "
        f"{pending_total} pendente(s) total; {eligible_now} elegível(is) agora."
    )
    return enriched


def main() -> None:
    with SessionLocal() as db:
        run(db)


if __name__ == "__main__":
    main()
