"""Validate the audited Ipiranga unit 001 ledger after an import."""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import BonusRule, PortalBonusEvent, PortalBonusMatch
from app.services.ipiranga_portal import build_ipiranga_cycle_ledger, build_upfront_ledger
from app.services.rules import money


EXPECTED_PORTAL_TOTAL = Decimal("208365.02")
EXPECTED_CONTRACT_TOTAL = Decimal("226896.00")


def main() -> None:
    with SessionLocal() as db:
        rule = db.scalar(
            select(BonusRule).where(
                BonusRule.unit_code == "001",
                BonusRule.company_code == "IPIRANGA",
                BonusRule.kind == "distributor_credit",
                BonusRule.active.is_(True),
            )
        )
        if not rule:
            raise SystemExit("Regra ativa da unidade 001 não encontrada")

        ledger = build_ipiranga_cycle_ledger(db, rule, date.today())
        portal_total = money(db.scalar(select(func.sum(PortalBonusEvent.value))) or 0)
        exact_matches = db.scalar(
            select(func.count()).select_from(PortalBonusMatch).where(PortalBonusMatch.status == "exact")
        ) or 0
        expected = money(sum((row["expected"] for row in ledger["cycles"]), Decimal("0")))
        observed = money(sum((row["observed"] for row in ledger["cycles"]), Decimal("0")))
        upfront = build_upfront_ledger(db, "001")
        result = {
            "events": len(ledger["events"]),
            "exact_matches": exact_matches,
            "cycles": len(ledger["cycles"]),
            "expected": str(expected),
            "portal_total": str(portal_total),
            "allocated": str(observed),
            "missing": str(money(expected - observed)),
            "upfront_contracted": str(upfront["contracted"]),
            "upfront_probable_used": str(upfront["probable_used"]),
            "upfront_estimated_balance": str(upfront["estimated_balance"]),
        }
        print(result)
        if len(ledger["events"]) != 15 or exact_matches != 15:
            raise SystemExit("Quantidade de eventos ou cadeias exatas divergente")
        if portal_total != EXPECTED_PORTAL_TOTAL or observed != EXPECTED_PORTAL_TOTAL:
            raise SystemExit("Total do portal ou apropriação do livro divergente")
        if expected != EXPECTED_CONTRACT_TOTAL:
            raise SystemExit("Total contratual esperado dos ciclos fechados divergente")


if __name__ == "__main__":
    main()
