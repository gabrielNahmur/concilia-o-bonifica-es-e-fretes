"""Import audited Ipiranga portal statements from the command line.

This operational helper writes only to the application's PostgreSQL database.
It never opens a connection to, or writes to, the ERP SQL Server.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import PortalBonusEvent, PortalBonusMatch, User
from app.services.audit import audit
from app.services.ipiranga_portal import import_ipiranga_statement
from app.services.reconciliation import rebuild_reconciliations


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa extratos postecipados Ipiranga/Texaco de uma unidade")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--unit", default="001")
    parser.add_argument("--actor-email")
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    with SessionLocal() as db:
        actor_query = select(User).where(User.active.is_(True), User.role == "admin")
        if args.actor_email:
            actor_query = actor_query.where(User.email == args.actor_email.strip().lower())
        actor = db.scalar(actor_query.order_by(User.created_at))
        if not actor:
            raise SystemExit("Nenhum administrador ativo foi encontrado para registrar a auditoria")

        results = []
        for path in args.files:
            content = path.read_bytes()
            content_type = (
                "application/pdf"
                if path.suffix.lower() == ".pdf"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if path.suffix.lower() == ".xlsx"
                else "application/vnd.ms-excel"
            )
            imported, repeated = import_ipiranga_statement(
                db,
                unit_code=args.unit,
                filename=path.name,
                content_type=content_type,
                content=content,
                uploaded_by=actor.id,
            )
            audit(
                db,
                actor,
                "portal_statement_import",
                "portal_statement_import",
                imported.id,
                {
                    "filename": imported.original_filename,
                    "sha256": imported.content_sha256,
                    "already_imported": repeated,
                    "imported_count": imported.imported_count,
                    "duplicate_count": imported.duplicate_count,
                    "source": "operational_cli",
                },
            )
            results.append(
                {
                    "file": path.name,
                    "already_imported": repeated,
                    "new_events": imported.imported_count,
                    "duplicates": imported.duplicate_count,
                }
            )

        rebuild_reconciliations(db)
        event_count = len(db.scalars(select(PortalBonusEvent)).all())
        matches = db.scalars(select(PortalBonusMatch)).all()
        exact_count = sum(row.status in {"exact", "portal_exact", "portal_group_exact"} for row in matches)
        print({"imports": results, "events": event_count, "exact_matches": exact_count})


if __name__ == "__main__":
    main()
