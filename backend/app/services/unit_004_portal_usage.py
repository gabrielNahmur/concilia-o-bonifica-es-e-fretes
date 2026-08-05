"""Primary Ipiranga portal evidence for the unit 004 credit reconciliation."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BonusRule, PortalBonusEvent, PortalBonusMatch, Purchase


DIRECT_PORTAL_USAGE_STATUS = "portal_usage_confirmed"


@dataclass(frozen=True)
class Unit004PortalUsage:
    """A portal credit and the NF explicitly named as its use destination."""

    event: PortalBonusEvent
    match: PortalBonusMatch
    purchase: Purchase
    details: dict


def is_unit_004_portal_credit_rule(rule: BonusRule) -> bool:
    return (
        rule.unit_code == "004"
        and rule.company_code == "IPIRANGA"
        and rule.kind == "distributor_credit"
    )


def unit_004_portal_usage_events(db: Session, rule: BonusRule) -> list[Unit004PortalUsage]:
    """Load only explicit, validated portal usage details for unit 004.

    A portal "Nota Fiscal Utilizada" is evidence of credit use.  It does not
    claim that this NF generated the credit, so callers must not allocate it
    back to purchase cycles.
    """
    if not is_unit_004_portal_credit_rule(rule):
        return []
    rows = db.execute(
        select(PortalBonusEvent, PortalBonusMatch, Purchase)
        .join(PortalBonusMatch, PortalBonusMatch.event_id == PortalBonusEvent.id)
        .join(Purchase, Purchase.erp_entry_id == PortalBonusMatch.purchase_entry_id)
        .where(
            PortalBonusEvent.unit_code == rule.unit_code,
            PortalBonusEvent.company_code == rule.company_code,
            PortalBonusEvent.category == "postpaid",
            PortalBonusMatch.status == DIRECT_PORTAL_USAGE_STATUS,
            Purchase.unit_code == rule.unit_code,
            Purchase.mapped_company_code == rule.company_code,
        )
        .order_by(PortalBonusEvent.portal_date, PortalBonusEvent.id)
    ).all()
    usage = []
    for event, match, purchase in rows:
        try:
            details = json.loads(match.details_json or "{}")
        except json.JSONDecodeError:
            details = {}
        usage.append(Unit004PortalUsage(event, match, purchase, details))
    return usage
