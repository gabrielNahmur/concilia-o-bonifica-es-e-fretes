from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import BonusRule, Contract, PayableDocument, Purchase, Reconciliation, SyncRun, Unit
from app.services.rules import (
    UmbrellaPurchase,
    add_months,
    allocate_umbrella,
    contract_pace_status,
    current_month_projection,
    liters,
    month_end,
    month_start,
    monthly_target,
    projected_completion_date,
)


ZERO = Decimal("0")
RECONCILIATION_STATUS_LABELS = {
    "partial": "parcial",
    "divergent": "divergente",
    "overdue": "vencida",
}
BONUS_RULE_LABELS = {
    "distributor_credit": "Crédito na distribuidora",
    "milestone_bonus": "Marco quadrimestral BR",
    "invoice_discount": "Desconto em boleto",
    "s10_excess_credit": "Crédito adicional S10",
    "bank_deposit": "Depósito em conta",
}


def _d(value: Decimal | None) -> Decimal:
    return value or ZERO


def _number(value: Decimal | None, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _regular_contract_metrics(db: Session, contract: Contract, as_of: date) -> dict:
    purchases = db.scalars(
        select(Purchase).where(
            Purchase.unit_code == contract.unit_code,
            Purchase.mapped_company_code == contract.company_code,
            Purchase.purchase_date >= contract.start_date,
            Purchase.purchase_date <= as_of,
        )
    ).all()
    return _metrics_from_rows(contract, purchases, as_of)


def _metrics_from_rows(contract: Contract, purchases: list, as_of: date) -> dict:
    current_start = month_start(as_of)
    previous_start = add_months(current_start, -3)
    accumulated = sum((Decimal(str(row.total_liters)) for row in purchases), ZERO)
    month_actual = sum((Decimal(str(row.total_liters)) for row in purchases if row.purchase_date >= current_start), ZERO)
    last_three = sum(
        (Decimal(str(row.total_liters)) for row in purchases if previous_start <= row.purchase_date < current_start), ZERO
    )
    avg_three = last_three / Decimal("3")
    target = monthly_target(Decimal(contract.total_liters), contract.term_months)
    remaining = max(ZERO, Decimal(contract.total_liters) - accumulated)
    completion = projected_completion_date(remaining, avg_three, as_of)
    status = contract_pace_status(
        completion,
        contract.end_date,
        completed=accumulated >= Decimal(contract.total_liters),
    )
    return {
        "unit_code": contract.unit_code,
        "company_code": contract.company_code,
        "contract_type": "fixed_term",
        "contract_label": "Contrato com prazo definido",
        "start_date": contract.start_date,
        "end_date": contract.end_date,
        "total_liters": _number(contract.total_liters, 3),
        "monthly_target": _number(target, 2),
        "month_actual": _number(month_actual, 3),
        "month_percent": _number((month_actual / target * 100) if target else ZERO, 2),
        "month_projection": _number(current_month_projection(month_actual, as_of), 3),
        "accumulated_liters": _number(accumulated, 3),
        "remaining_liters": _number(remaining, 3),
        "contract_percent": _number((accumulated / Decimal(contract.total_liters) * 100), 2),
        "rolling_average": _number(avg_three, 2),
        "projected_completion": completion,
        "status": status,
        "upfront_total": _number(contract.upfront_total, 2),
        "upfront_per_liter": _number(contract.upfront_per_liter, 6),
        "postpaid_per_liter": _number(contract.postpaid_per_liter, 6),
        "effective_per_liter": _number(Decimal(contract.upfront_per_liter) + Decimal(contract.postpaid_per_liter), 6),
    }


def _umbrella_metrics(db: Session, contracts: list[Contract], as_of: date) -> dict[str, dict]:
    start = min(contract.start_date for contract in contracts)
    purchases = db.scalars(
        select(Purchase).where(
            Purchase.unit_code.in_([contract.unit_code for contract in contracts]),
            Purchase.mapped_company_code == "BR",
            Purchase.purchase_date >= start,
            Purchase.purchase_date <= as_of,
        )
    ).all()
    allocations, totals, beyond = allocate_umbrella(
        [
            UmbrellaPurchase(row.erp_entry_id, row.unit_code, row.purchase_date, Decimal(row.total_liters))
            for row in purchases
        ]
    )
    rows_by_contract: dict[str, list] = defaultdict(list)
    for allocation in allocations:
        rows_by_contract[allocation.contract_unit].append(allocation)

    result: dict[str, dict] = {}
    for contract in contracts:
        synthetic_rows = [
            type("Allocated", (), {"total_liters": row.liters, "purchase_date": row.purchase_date})
            for row in rows_by_contract[contract.unit_code]
        ]
        metrics = _metrics_from_rows(contract, synthetic_rows, as_of)
        metrics["umbrella_group"] = contract.umbrella_group
        metrics["umbrella_beyond_liters"] = _number(beyond, 3)
        metrics["accumulated_liters"] = _number(totals[contract.unit_code], 3)
        result[contract.unit_code] = metrics
    return result


def get_contract_metrics(db: Session, as_of: date | None = None) -> list[dict]:
    as_of = as_of or date.today()
    contracts = db.scalars(select(Contract).where(Contract.status == "active").order_by(Contract.unit_code)).all()
    umbrella_contracts = [row for row in contracts if row.umbrella_group == "BR_002_006"]
    umbrella = _umbrella_metrics(db, umbrella_contracts, as_of) if umbrella_contracts else {}
    metrics = []
    for contract in contracts:
        metrics.append(umbrella.get(contract.unit_code) or _regular_contract_metrics(db, contract, as_of))
    return metrics


def _indefinite_metric(
    db: Session,
    unit: Unit,
    rules: list[BonusRule],
    as_of: date,
    reference_month: date,
) -> dict:
    """Build the operational view for a commercial rule without a deadline.

    These units do have an active commercial agreement (normally a bonus
    rule), but no contracted total or expiry date. They must never be mixed
    into the fixed-term total/remaining-liter KPIs.
    """
    company_code = rules[0].company_code
    start_date = min(rule.effective_from for rule in rules)
    purchases = db.scalars(
        select(Purchase).where(
            Purchase.unit_code == unit.code,
            Purchase.mapped_company_code == company_code,
            Purchase.purchase_date >= start_date,
            Purchase.purchase_date <= as_of,
        )
    ).all()
    current_start = month_start(as_of)
    previous_start = add_months(current_start, -3)
    month_actual = sum(
        (Decimal(str(row.total_liters)) for row in purchases if row.purchase_date >= current_start),
        ZERO,
    )
    last_three = sum(
        (Decimal(str(row.total_liters)) for row in purchases if previous_start <= row.purchase_date < current_start),
        ZERO,
    )
    bonus_rows = db.scalars(
        select(Reconciliation)
        .join(BonusRule, Reconciliation.rule_id == BonusRule.id)
        .where(
            Reconciliation.unit_code == unit.code,
            Reconciliation.reference_month == reference_month,
            Reconciliation.status != "superseded",
            BonusRule.active.is_(True),
        )
    ).all()
    expected_bonus = sum((Decimal(row.expected_value) for row in bonus_rows), ZERO)
    observed_bonus = sum(
        (Decimal(row.observed_value) + Decimal(row.manual_adjustment or 0) for row in bonus_rows),
        ZERO,
    )
    return {
        "unit_code": unit.code,
        "company_code": company_code,
        "contract_type": "indefinite",
        "contract_label": "Contrato por tempo indeterminado",
        "start_date": start_date,
        "end_date": None,
        "term_months": None,
        "total_liters": None,
        "monthly_target": None,
        "month_actual": _number(month_actual, 3),
        "month_percent": None,
        "month_projection": _number(current_month_projection(month_actual, as_of), 3),
        "accumulated_liters": _number(sum((Decimal(str(row.total_liters)) for row in purchases), ZERO), 3),
        "remaining_liters": None,
        "contract_percent": None,
        "rolling_average": _number(last_three / Decimal("3"), 2),
        "projected_completion": None,
        "status": "indefinite",
        "bonus_expected": _number(expected_bonus),
        "bonus_identified": _number(observed_bonus),
        "bonus_rules": [
            {
                "id": rule.id,
                "kind": rule.kind,
                "label": BONUS_RULE_LABELS.get(rule.kind, rule.kind),
                "rate_per_liter": _number(rule.rate_per_liter, 6),
                "effective_from": rule.effective_from,
                "effective_to": rule.effective_to,
            }
            for rule in rules
        ],
    }


def get_indefinite_contract_metrics(
    db: Session,
    as_of: date | None = None,
    reference_month: date | None = None,
) -> list[dict]:
    """Return active commercial agreements that do not have an expiry date."""
    as_of = as_of or date.today()
    reference_month = month_start(reference_month or as_of)
    fixed_units = set(
        db.scalars(select(Contract.unit_code).where(Contract.status == "active")).all()
    )
    rules = db.scalars(
        select(BonusRule)
        .where(BonusRule.active.is_(True))
        .order_by(BonusRule.unit_code, BonusRule.company_code, BonusRule.effective_from, BonusRule.id)
    ).all()
    by_unit: dict[str, list[BonusRule]] = defaultdict(list)
    for rule in rules:
        if rule.unit_code not in fixed_units:
            by_unit[rule.unit_code].append(rule)
    if not by_unit:
        return []
    units = {
        row.code: row
        for row in db.scalars(
            select(Unit).where(Unit.code.in_(sorted(by_unit)), Unit.active.is_(True))
        ).all()
    }
    result = []
    for code, unit_rules in by_unit.items():
        unit = units.get(code)
        if not unit:
            continue
        # The existing commercial data has one current supplier per
        # deadline-free unit. Keep separate groups if that assumption changes.
        company_groups: dict[str, list[BonusRule]] = defaultdict(list)
        for rule in unit_rules:
            company_groups[rule.company_code].append(rule)
        for company_rules in company_groups.values():
            result.append(_indefinite_metric(db, unit, company_rules, as_of, reference_month))
    return sorted(result, key=lambda row: (row["unit_code"], row["company_code"]))


def _unit_purchase_totals(db: Session, reference_month: date) -> dict[str, Decimal]:
    month_end = add_months(reference_month, 1)
    rows = db.execute(
        select(Purchase.unit_code, func.coalesce(func.sum(Purchase.total_liters), 0))
        .where(
            Purchase.purchase_date >= reference_month,
            Purchase.purchase_date < month_end,
            Purchase.mapped_company_code.is_not(None),
        )
        .group_by(Purchase.unit_code)
    ).all()
    return {code: Decimal(total) for code, total in rows}


def _json_evidence(row: Reconciliation) -> list[dict]:
    try:
        payload = json.loads(row.evidence_json or "[]")
    except (TypeError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def _reconciliation_period(row: Reconciliation, rule: BonusRule) -> tuple[date, date]:
    """Use the materialized contractual period whenever reconciliation has it."""
    for evidence in _json_evidence(row):
        if evidence.get("source") != "calculation":
            continue
        try:
            return (
                date.fromisoformat(str(evidence["period_start"])[:10]),
                date.fromisoformat(str(evidence["period_end"])[:10]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    start = max(month_start(row.reference_month), rule.effective_from)
    end = min(month_end(row.reference_month), rule.effective_to or month_end(row.reference_month))
    return start, end


def _eligible_bonus_liters(purchase: Purchase, rule: BonusRule) -> Decimal:
    applies_to = (rule.applies_to or "all_fuel").strip().lower()
    if not applies_to.startswith("fuel_codes:"):
        return Decimal(purchase.total_liters or 0)
    item_codes = {item.strip() for item in applies_to.split(":", 1)[1].split(",") if item.strip()}
    return sum(
        (Decimal(item.quantity or 0) for item in purchase.items if str(item.item_code).strip() in item_codes),
        ZERO,
    )


def _bonus_purchases_for_period(
    db: Session,
    row: Reconciliation,
    rule: BonusRule,
    period_start: date,
    period_end: date,
) -> tuple[list[Purchase], dict[int, Decimal]]:
    """Return orders supporting a bonus item without leaking unrelated units.

    BR's 002/006 agreement is the sole cross-unit exception. Its allocations
    are rebuilt chronologically so a dashboard user sees only the liters that
    belong to the selected contract, even when the physical invoice was issued
    by the other umbrella unit.
    """
    if rule.kind == "milestone_bonus" and row.unit_code in {"002", "006"}:
        purchases = db.scalars(
            select(Purchase)
            .options(selectinload(Purchase.items))
            .where(
                Purchase.unit_code.in_(("002", "006")),
                Purchase.mapped_company_code == "BR",
                Purchase.purchase_date >= rule.effective_from,
                Purchase.purchase_date <= period_end,
            )
            .order_by(Purchase.purchase_date, Purchase.erp_entry_id)
        ).all()
        allocations, _, _ = allocate_umbrella(
            [
                UmbrellaPurchase(item.erp_entry_id, item.unit_code, item.purchase_date, Decimal(item.total_liters))
                for item in purchases
            ]
        )
        allocated_liters: dict[int, Decimal] = defaultdict(Decimal)
        for allocation in allocations:
            if allocation.contract_unit == row.unit_code and period_start <= allocation.purchase_date <= period_end:
                allocated_liters[allocation.purchase_key] += allocation.liters
        return [item for item in purchases if item.erp_entry_id in allocated_liters], allocated_liters

    contractual_date = (
        func.coalesce(Purchase.invoice_issue_date, Purchase.purchase_date)
        if rule.kind == "distributor_credit" and rule.company_code == "IPIRANGA"
        else Purchase.purchase_date
    )
    purchases = db.scalars(
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(
            Purchase.unit_code == row.unit_code,
            Purchase.mapped_company_code == rule.company_code,
            contractual_date >= period_start,
            contractual_date <= period_end,
        )
        .order_by(contractual_date, Purchase.erp_entry_id)
    ).all()
    return purchases, {item.erp_entry_id: Decimal(item.total_liters or 0) for item in purchases}


def _dashboard_evidence(evidence: list[dict]) -> list[dict]:
    """A readable evidence list for the bonus card, without raw JSON blobs."""
    result = []
    for item in evidence:
        if item.get("source") == "calculation":
            continue
        value = item.get("allocated", item.get("value", item.get("source_value")))
        result.append(
            {
                "source": item.get("source") or "ERP",
                "date": item.get("date"),
                "document": item.get("document") or item.get("bank_document"),
                "value": _number(Decimal(str(value or 0))),
                "description": item.get("history") or item.get("match_basis") or item.get("source_reason"),
                "counted": item.get("counted") is not False,
            }
        )
    return result


def get_bonus_expected_details(
    db: Session,
    reference_month: date | None = None,
    unit_codes: list[str] | None = None,
) -> dict:
    """Detailed, card-ready explanation of the expected bonus for a month.

    Every row is one reconciliation rule/competence. It carries the actual
    contractual orders, their eligible liters and linked payable titles where
    those titles exist, so the dashboard can open a useful detail without
    making an executive work through the technical reconciliation drawer.
    """
    reference_month = month_start(reference_month or date.today())
    filters = [
        Reconciliation.reference_month == reference_month,
        Reconciliation.status != "superseded",
        Reconciliation.expected_value > ZERO,
    ]
    if unit_codes:
        filters.append(Reconciliation.unit_code.in_({code.zfill(3) for code in unit_codes}))
    rows = db.execute(
        select(Reconciliation, BonusRule, Unit)
        .join(BonusRule, Reconciliation.rule_id == BonusRule.id)
        .join(Unit, Unit.code == Reconciliation.unit_code)
        .where(*filters)
        .order_by(Reconciliation.unit_code, BonusRule.kind, Reconciliation.id)
    ).all()
    details = []
    total_expected = total_identified = ZERO
    for reconciliation, rule, unit in rows:
        period_start, period_end = _reconciliation_period(reconciliation, rule)
        purchases, allocated_liters = _bonus_purchases_for_period(
            db, reconciliation, rule, period_start, period_end
        )
        entry_ids = [item.erp_entry_id for item in purchases]
        documents_by_entry: dict[int, list[PayableDocument]] = defaultdict(list)
        if entry_ids:
            for document in db.scalars(
                select(PayableDocument)
                .where(PayableDocument.erp_entry_id.in_(entry_ids))
                .order_by(PayableDocument.erp_entry_id, PayableDocument.due_date, PayableDocument.id)
            ).all():
                if document.erp_entry_id is not None:
                    documents_by_entry[document.erp_entry_id].append(document)
        purchase_payload = []
        eligible_liters = gross_value = net_value = ZERO
        document_value = document_discount = ZERO
        for purchase in purchases:
            eligible = _eligible_bonus_liters(purchase, rule)
            eligible_liters += eligible
            gross_value += Decimal(purchase.gross_value or 0)
            net_value += Decimal(purchase.net_value or 0)
            titles = []
            for document in documents_by_entry.get(purchase.erp_entry_id, []):
                document_value += Decimal(document.document_value or 0)
                document_discount += Decimal(document.other_discount or 0)
                titles.append(
                    {
                        "document_id": document.document_id,
                        "invoice_number": document.invoice_number,
                        "due_date": document.due_date,
                        "payment_date": document.payment_date,
                        "document_value": _number(document.document_value),
                        "other_discount": _number(document.other_discount),
                        "balance": _number(document.balance),
                    }
                )
            purchase_payload.append(
                {
                    "erp_entry_id": purchase.erp_entry_id,
                    "physical_unit_code": purchase.unit_code,
                    "purchase_date": purchase.purchase_date,
                    "invoice_issue_date": purchase.invoice_issue_date,
                    "invoice_number": purchase.invoice_number,
                    "access_key": purchase.access_key,
                    "supplier_name": purchase.supplier_name,
                    "total_liters": _number(purchase.total_liters, 3),
                    "eligible_liters": _number(eligible, 3),
                    "contract_allocated_liters": _number(allocated_liters.get(purchase.erp_entry_id), 3),
                    "gross_value": _number(purchase.gross_value),
                    "net_value": _number(purchase.net_value),
                    "titles": titles,
                }
            )
        expected = Decimal(reconciliation.expected_value or 0)
        identified = Decimal(reconciliation.observed_value or 0) + Decimal(reconciliation.manual_adjustment or 0)
        total_expected += expected
        total_identified += identified
        details.append(
            {
                "reconciliation_id": reconciliation.id,
                "unit": {
                    "code": unit.code,
                    "display_name": unit.display_name,
                    "brand": unit.brand,
                },
                "company_code": rule.company_code,
                "rule": {
                    "id": rule.id,
                    "kind": rule.kind,
                    "label": BONUS_RULE_LABELS.get(rule.kind, rule.kind),
                    "rate_per_liter": _number(rule.rate_per_liter, 6),
                    "milestone_liters": _number(rule.milestone_liters, 3) if rule.milestone_liters is not None else None,
                    "milestone_amount": _number(rule.milestone_amount) if rule.milestone_amount is not None else None,
                },
                "competence": {
                    "reference_month": reconciliation.reference_month,
                    "period_start": period_start,
                    "period_end": period_end,
                    "due_date": reconciliation.due_date,
                },
                "expected_value": _number(expected),
                "identified_value": _number(identified),
                "difference_value": _number(expected - identified),
                "status": reconciliation.status,
                "purchase_summary": {
                    "count": len(purchase_payload),
                    "eligible_liters": _number(eligible_liters, 3),
                    "gross_value": _number(gross_value),
                    "net_value": _number(net_value),
                    "title_value": _number(document_value),
                    "title_other_discount": _number(document_discount),
                },
                "purchases": purchase_payload,
                "evidence": _dashboard_evidence(_json_evidence(reconciliation)),
            }
        )
    return {
        "reference_month": reference_month,
        "totals": {
            "expected_value": _number(total_expected),
            "identified_value": _number(total_identified),
            "difference_value": _number(total_expected - total_identified),
            "items": len(details),
        },
        "items": details,
    }


def get_dashboard(db: Session, reference_month: date | None = None, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    reference_month = month_start(reference_month or as_of)
    units = db.scalars(select(Unit).where(Unit.active.is_(True)).order_by(Unit.code)).all()
    contracts = get_contract_metrics(db, as_of)
    indefinite_contracts = get_indefinite_contract_metrics(db, as_of, reference_month)
    purchases = _unit_purchase_totals(db, reference_month)
    reconciliations = db.scalars(
        select(Reconciliation).where(Reconciliation.reference_month == reference_month)
    ).all()
    reconciliation_totals = {
        "expected": sum((Decimal(row.expected_value) for row in reconciliations), ZERO),
        "observed": sum((Decimal(row.observed_value) + Decimal(row.manual_adjustment) for row in reconciliations), ZERO),
    }
    latest_sync = db.scalar(
        select(SyncRun).where(SyncRun.status == "success").order_by(SyncRun.finished_at.desc()).limit(1)
    )
    series_start = add_months(reference_month, -11)
    series_purchases = db.scalars(
        select(Purchase).where(
            Purchase.purchase_date >= series_start,
            Purchase.purchase_date < add_months(reference_month, 1),
            Purchase.mapped_company_code.is_not(None),
        )
    ).all()
    monthly_series = defaultdict(Decimal)
    for purchase in series_purchases:
        monthly_series[month_start(purchase.purchase_date)] += Decimal(purchase.total_liters)
    series = [
        {"month": month, "liters": _number(monthly_series.get(month, ZERO), 3)}
        for month in (add_months(series_start, offset) for offset in range(12))
    ]
    sync_stale = True
    if latest_sync and latest_sync.finished_at:
        finished = latest_sync.finished_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        sync_stale = datetime.now(timezone.utc) - finished > timedelta(hours=24)
    unit_rows = []
    contract_map = {row["unit_code"]: row for row in contracts}
    indefinite_map = {row["unit_code"]: row for row in indefinite_contracts}
    for unit in units:
        unit_rows.append(
            {
                "code": unit.code,
                "display_name": unit.display_name,
                "city": unit.city,
                "brand": unit.brand,
                "month_liters": _number(purchases.get(unit.code, ZERO), 3),
                "contract": contract_map.get(unit.code),
                "indefinite_contract": indefinite_map.get(unit.code),
            }
        )
    alerts = [row for row in contracts if row["status"] == "late"]
    alerts.extend(
        {
            "unit_code": row.unit_code,
            "status": row.status,
            "message": f"Bonificação {RECONCILIATION_STATUS_LABELS.get(row.status, row.status)}: diferença de R$ {float(row.difference_value):,.2f}",
        }
        for row in reconciliations
        if row.status in {"partial", "divergent", "overdue"}
    )
    return {
        "reference_month": reference_month,
        "as_of": as_of,
        "totals": {
            "month_liters": _number(sum(purchases.values(), ZERO), 3),
            "contract_liters": _number(sum(Decimal(str(row["accumulated_liters"])) for row in contracts), 3),
            "remaining_liters": _number(sum(Decimal(str(row["remaining_liters"])) for row in contracts), 3),
            "bonus_expected": _number(reconciliation_totals["expected"], 2),
            "bonus_observed": _number(reconciliation_totals["observed"], 2),
        },
        "contracts": contracts,
        "indefinite_contracts": indefinite_contracts,
        "units": unit_rows,
        "alerts": alerts,
        "series": series,
        "sync": {
            "last_success": latest_sync.finished_at if latest_sync else None,
            "rows_processed": latest_sync.rows_processed if latest_sync else 0,
            "stale": sync_stale,
        },
    }


def get_unit_detail(db: Session, unit_code: str, as_of: date | None = None) -> dict | None:
    as_of = as_of or date.today()
    unit = db.scalar(
        select(Unit)
        .options(selectinload(Unit.contracts), selectinload(Unit.bonus_rules))
        .where(Unit.code == unit_code)
    )
    if not unit:
        return None
    metrics = next((row for row in get_contract_metrics(db, as_of) if row["unit_code"] == unit_code), None)
    if metrics is None:
        metrics = next(
            (row for row in get_indefinite_contract_metrics(db, as_of) if row["unit_code"] == unit_code),
            None,
        )
    start = add_months(month_start(as_of), -11)
    rows = db.execute(
        select(
            func.date_trunc("month", Purchase.purchase_date).label("month")
            if db.bind and db.bind.dialect.name == "postgresql"
            else func.strftime("%Y-%m-01", Purchase.purchase_date).label("month"),
            func.sum(Purchase.total_liters),
        )
        .where(Purchase.unit_code == unit_code, Purchase.purchase_date >= start, Purchase.mapped_company_code.is_not(None))
        .group_by("month")
        .order_by("month")
    ).all()
    series = [{"month": str(month)[:10], "liters": _number(total, 3)} for month, total in rows]
    reconciliations = db.scalars(
        select(Reconciliation).where(Reconciliation.unit_code == unit_code).order_by(Reconciliation.reference_month.desc())
    ).all()
    return {
        "unit": {"code": unit.code, "display_name": unit.display_name, "city": unit.city, "brand": unit.brand},
        "contract": metrics,
        "series": series,
        "bonus_rules": [
            {
                "id": row.id,
                "kind": row.kind,
                "rate_per_liter": _number(row.rate_per_liter, 6),
                "threshold_liters": _number(row.threshold_liters, 3) if row.threshold_liters is not None else None,
                "effective_from": row.effective_from,
            }
            for row in unit.bonus_rules
        ],
        "reconciliations": [
            {
                "id": row.id,
                "reference_month": row.reference_month,
                "due_date": row.due_date,
                "expected_value": _number(row.expected_value),
                "observed_value": _number(row.observed_value),
                "difference_value": _number(row.difference_value),
                "status": row.status,
                "confidence": row.confidence,
            }
            for row in reconciliations
        ],
    }
