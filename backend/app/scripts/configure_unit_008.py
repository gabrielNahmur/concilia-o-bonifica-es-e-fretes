"""Apply the confirmed Unit 008 postpaid configuration to an existing app DB.

This updates only the application's PostgreSQL database. It never writes to
the ERP; the source of truth remains the read-only sync and the preserved
Ipiranga report uploaded afterwards.
"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import BonusRule, Contract
from app.services.seed import seed_reference_data


def main() -> None:
    with SessionLocal() as db:
        seed_reference_data(db)
        contract = db.scalar(
            select(Contract).where(
                Contract.unit_code == "008",
                Contract.company_code == "IPIRANGA",
            )
        )
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "008",
                BonusRule.company_code == "IPIRANGA",
                BonusRule.kind == "distributor_credit",
            )
        )
        if not contract or not rule:
            raise SystemExit("Nao foi possivel configurar a regra postecipada da unidade 008")
        print(
            {
                "unit": "008",
                "contract_postpaid_rate": str(contract.postpaid_per_liter),
                "rule_id": rule.id,
                "effective_from": rule.effective_from.isoformat(),
                "effective_to": rule.effective_to.isoformat(),
                "rate_per_liter": str(rule.rate_per_liter),
            }
        )


if __name__ == "__main__":
    main()
