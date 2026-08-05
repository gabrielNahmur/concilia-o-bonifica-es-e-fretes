"""Apply the approved August/2026 start to the unit 050 S10 bonus rule.

Only the Contracts application database is changed.  The ERP remains strictly
read-only, and the prior rule effective date is retained in the audit trail.
"""

from datetime import date

from sqlalchemy import select

from app.database import SessionLocal
from app.models import BonusRule
from app.services.audit import audit
from app.services.reconciliation import rebuild_reconciliations


APPROVED_START = date(2026, 8, 1)


def main() -> None:
    with SessionLocal() as db:
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "050",
                BonusRule.company_code == "TEXACO",
                BonusRule.kind == "s10_excess_credit",
            )
        )
        if not rule:
            raise SystemExit("Regra adicional S10 da unidade 050 não encontrada")

        previous_start = rule.effective_from
        if previous_start != APPROVED_START:
            rule.effective_from = APPROVED_START
            audit(
                db,
                None,
                "bonus_rule_effective_date_changed",
                "bonus_rule",
                rule.id,
                {
                    "unit_code": "050",
                    "rule_kind": "s10_excess_credit",
                    "previous_effective_from": previous_start.isoformat() if previous_start else None,
                    "effective_from": APPROVED_START.isoformat(),
                    "reason": "Início aprovado do adicional S10: agosto de 2026, sem retroatividade.",
                },
            )
        rebuilt = rebuild_reconciliations(db)
        db.commit()
        print(
            {
                "unit": "050",
                "rule": "s10_excess_credit",
                "previous_effective_from": previous_start.isoformat() if previous_start else None,
                "effective_from": APPROVED_START.isoformat(),
                "rebuilt": rebuilt,
            }
        )


if __name__ == "__main__":
    main()
