"""Import Raízen bank receipts for unit 054 into the application database.

The original PDF is retained in ``portal_statement_imports`` and its seven
Santander TEDs are normalized as reusable evidence.  It writes only to the
Contracts GBI PostgreSQL database; it never connects to or writes to the ERP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import PortalBonusEvent, PortalBonusEventSource, PortalStatementImport, User
from app.services.audit import audit
from app.services.reconciliation import RAIZEN_RECEIPT_CATEGORY, rebuild_reconciliations


UNIT_CODE = "054"
COMPANY_CODE = "SHELL"
CLIENT_CNPJ = "12564276000262"
PAYER_NAME = "Raízen S.A."
PAYER_CNPJ = "033453598000123"
BENEFICIARY_NAME = "STILO COMERCIO DE COMBUSTIVEIS"
BANK = "237 - Banco Bradesco S.A."
AGENCY = "0439"
ACCOUNT = "000000024515-1"

# Each allocation is transcribed from the seven pages of the Santander receipt
# provided by Raízen on 30/07/2026. The R$86,250.00 TED is intentionally split
# only among the twelve competences whose exact expected sum it closes.
RECEIPTS = (
    {
        "page": 1,
        "credit_date": date(2026, 3, 27),
        "value": Decimal("8450.00"),
        "bank_commitment": "901088379",
        "client_commitment": "2777175813",
        "allocations": {"2026-02-01": "8450.00"},
    },
    {
        "page": 2,
        "credit_date": date(2026, 4, 28),
        "value": Decimal("11100.00"),
        "bank_commitment": "901098121",
        "client_commitment": "2782264102",
        "allocations": {"2026-03-01": "11100.00"},
    },
    {
        "page": 3,
        "credit_date": date(2026, 1, 28),
        "value": Decimal("86250.00"),
        "bank_commitment": "901068709",
        "client_commitment": "2776587506",
        "allocations": {
            "2024-12-01": "5750.00",
            "2025-01-01": "6500.00",
            "2025-03-01": "4500.00",
            "2025-04-01": "5000.00",
            "2025-05-01": "6000.00",
            "2025-06-01": "4500.00",
            "2025-07-01": "6000.00",
            "2025-08-01": "9500.00",
            "2025-09-01": "8400.00",
            "2025-10-01": "10500.00",
            "2025-11-01": "9100.00",
            "2025-12-01": "10500.00",
        },
    },
    {
        "page": 4,
        "credit_date": date(2026, 2, 26),
        "value": Decimal("8500.00"),
        "bank_commitment": "901077838",
        "client_commitment": "2776864902",
        "allocations": {"2026-01-01": "8500.00"},
    },
    {
        "page": 5,
        "credit_date": date(2026, 7, 28),
        "value": Decimal("10250.00"),
        "bank_commitment": "901126078",
        "client_commitment": "2783151347",
        "allocations": {"2026-06-01": "10250.00"},
    },
    {
        "page": 6,
        "credit_date": date(2026, 5, 28),
        "value": Decimal("8850.00"),
        "bank_commitment": "901107787",
        "client_commitment": "2782563570",
        "allocations": {"2026-04-01": "8850.00"},
    },
    {
        "page": 7,
        "credit_date": date(2026, 6, 26),
        "value": Decimal("7800.00"),
        "bank_commitment": "901117022",
        "client_commitment": "2782849811",
        "allocations": {"2026-05-01": "7800.00"},
    },
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa os comprovantes bancários da Raízen da unidade 054")
    parser.add_argument("file", type=Path)
    parser.add_argument("--actor-email")
    return parser.parse_args()


def _event_key(receipt: dict) -> str:
    identity = {
        "unit": UNIT_CODE,
        "category": RAIZEN_RECEIPT_CATEGORY,
        "bank_commitment": receipt["bank_commitment"],
        "client_commitment": receipt["client_commitment"],
        "date": receipt["credit_date"].isoformat(),
        "value": str(receipt["value"]),
        "allocations": receipt["allocations"],
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def main() -> None:
    args = _arguments()
    content = args.file.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    with SessionLocal() as db:
        actor_query = select(User).where(User.active.is_(True), User.role == "admin")
        if args.actor_email:
            actor_query = actor_query.where(User.email == args.actor_email.strip().lower())
        actor = db.scalar(actor_query.order_by(User.created_at))
        if not actor:
            raise SystemExit("Nenhum administrador ativo foi encontrado para registrar a auditoria")

        import_row = db.scalar(
            select(PortalStatementImport).where(PortalStatementImport.content_sha256 == digest)
        )
        repeated_file = import_row is not None
        if not import_row:
            import_row = PortalStatementImport(
                unit_code=UNIT_CODE,
                company_code=COMPANY_CODE,
                category=RAIZEN_RECEIPT_CATEGORY,
                client_cnpj=CLIENT_CNPJ,
                period_start=date(2024, 12, 1),
                period_end=date(2026, 6, 1),
                original_filename=args.file.name,
                content_type="application/pdf",
                content_sha256=digest,
                source_file=content,
                row_count=len(RECEIPTS),
                uploaded_by=actor.id,
            )
            db.add(import_row)
            db.flush()

        created_events = 0
        linked_sources = 0
        for receipt in RECEIPTS:
            event_key = _event_key(receipt)
            event = db.scalar(select(PortalBonusEvent).where(PortalBonusEvent.event_key == event_key))
            metadata = {
                "import_id": import_row.id,
                "page": receipt["page"],
                "bank_commitment": receipt["bank_commitment"],
                "client_commitment": receipt["client_commitment"],
                "payer_name": PAYER_NAME,
                "payer_cnpj": PAYER_CNPJ,
                "beneficiary_name": BENEFICIARY_NAME,
                "beneficiary_cnpj": CLIENT_CNPJ,
                "bank": BANK,
                "agency": AGENCY,
                "account": ACCOUNT,
                "allocations": receipt["allocations"],
            }
            if not event:
                event = PortalBonusEvent(
                    unit_code=UNIT_CODE,
                    company_code=COMPANY_CODE,
                    category=RAIZEN_RECEIPT_CATEGORY,
                    portal_date=receipt["credit_date"],
                    value=receipt["value"],
                    description="TED Raízen - bonificação contratual",
                    reference=f"{receipt['bank_commitment']}/{receipt['client_commitment']}",
                    client_cnpj=CLIENT_CNPJ,
                    business_classification="contractual_bonus",
                    event_key=event_key,
                )
                db.add(event)
                created_events += 1
            event.review_notes = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            event.reviewed_by = actor.id
            event.reviewed_at = datetime.now(timezone.utc)
            db.flush()

            source = db.scalar(
                select(PortalBonusEventSource).where(
                    PortalBonusEventSource.import_id == import_row.id,
                    PortalBonusEventSource.event_id == event.id,
                )
            )
            if not source:
                db.add(
                    PortalBonusEventSource(
                        import_id=import_row.id,
                        event_id=event.id,
                        row_number=receipt["page"],
                        raw_json=json.dumps(
                            {
                                "source": "Santander - Comprovante de Emissão DOC/TED",
                                "credit_date": receipt["credit_date"].isoformat(),
                                "value": str(receipt["value"]),
                                **metadata,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                )
                linked_sources += 1

        import_row.imported_count = created_events
        import_row.duplicate_count = len(RECEIPTS) - created_events
        audit(
            db,
            actor,
            "raizen_bank_receipt_import",
            "portal_statement_import",
            import_row.id,
            {
                "filename": import_row.original_filename,
                "sha256": digest,
                "unit_code": UNIT_CODE,
                "payer_cnpj": PAYER_CNPJ,
                "beneficiary_cnpj": CLIENT_CNPJ,
                "receipts": len(RECEIPTS),
                "created_events": created_events,
                "already_imported": repeated_file,
            },
        )
        db.flush()
        rebuilt = rebuild_reconciliations(db)
        print(
            {
                "import_id": import_row.id,
                "already_imported": repeated_file,
                "created_events": created_events,
                "linked_sources": linked_sources,
                "reconciliations_rebuilt": rebuilt,
            }
        )


if __name__ == "__main__":
    main()
