from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


MONEY = Decimal("0.01")
LITERS = Decimal("0.001")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def liters(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(LITERS, rounding=ROUND_HALF_UP)


def add_months(value: date, months: int) -> date:
    target_month = value.month - 1 + months
    year = value.year + target_month // 12
    month = target_month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_start(value: date) -> date:
    return value.replace(day=1)


def month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def next_month(value: date) -> date:
    return add_months(month_start(value), 1)


def due_date_for_month(reference_month: date, month_offset: int = 1, due_day: int | None = None) -> date:
    due_month = add_months(month_start(reference_month), month_offset)
    if due_day is None:
        return month_end(due_month)
    return due_month.replace(day=min(due_day, calendar.monthrange(due_month.year, due_month.month)[1]))


def monthly_target(total_liters: Decimal, term_months: int) -> Decimal:
    if term_months <= 0:
        return Decimal("0")
    return liters(total_liters / Decimal(term_months))


def current_month_projection(actual: Decimal, as_of: date) -> Decimal:
    days = calendar.monthrange(as_of.year, as_of.month)[1]
    if as_of.day <= 0:
        return Decimal("0")
    return liters(actual / Decimal(as_of.day) * Decimal(days))


def projected_completion_date(remaining_liters: Decimal, monthly_average: Decimal, as_of: date) -> date | None:
    if monthly_average <= 0 or remaining_liters <= 0:
        return as_of if remaining_liters <= 0 else None
    months = int((remaining_liters / monthly_average).to_integral_value(rounding="ROUND_CEILING"))
    return add_months(as_of, months)


def contract_pace_status(
    projected_completion: date | None,
    contract_end: date,
    *,
    completed: bool = False,
) -> str:
    """Classify contractual pace against the agreed end date.

    The management rule is deliberately calendar based: a projection within
    one month before or after the contractual end is still ``on_track``.  Two
    or more calendar months early is ``ahead`` and two or more months late is
    ``late``.  A missing projection means there is no recent purchase pace to
    support the deadline, therefore it is treated as late rather than as a
    neutral state.
    """
    if completed:
        return "completed"
    if projected_completion is None:
        return "late"
    if projected_completion < add_months(contract_end, -1):
        return "ahead"
    if projected_completion > add_months(contract_end, 1):
        return "late"
    return "on_track"


def br_milestones(total_liters: Decimal, step: Decimal = Decimal("440000"), maximum: int = 6) -> int:
    if total_liters <= 0:
        return 0
    return min(maximum, int(total_liters // step))


def br_bonus_value(total_liters: Decimal) -> Decimal:
    return money(br_milestones(total_liters) * Decimal("35000"))


def s10_excess_bonus(s10_liters: Decimal, threshold: Decimal = Decimal("150000")) -> Decimal:
    return money(max(Decimal("0"), s10_liters - threshold) * Decimal("0.10"))


@dataclass(frozen=True)
class UmbrellaPurchase:
    key: int
    unit_code: str
    purchase_date: date
    liters: Decimal


@dataclass(frozen=True)
class UmbrellaAllocation:
    purchase_key: int
    physical_unit: str
    contract_unit: str
    purchase_date: date
    liters: Decimal


def allocate_umbrella(
    purchases: list[UmbrellaPurchase],
    contract_units: tuple[str, str] = ("002", "006"),
    target: Decimal = Decimal("2640000"),
) -> tuple[list[UmbrellaAllocation], dict[str, Decimal], Decimal]:
    totals = {contract_units[0]: Decimal("0"), contract_units[1]: Decimal("0")}
    allocations: list[UmbrellaAllocation] = []
    beyond = Decimal("0")

    for purchase in sorted(purchases, key=lambda row: (row.purchase_date, row.key)):
        own = purchase.unit_code
        other = contract_units[1] if own == contract_units[0] else contract_units[0]
        remaining = Decimal(purchase.liters)

        for destination in (own, other):
            capacity = max(Decimal("0"), target - totals[destination])
            allocated = min(remaining, capacity)
            if allocated > 0:
                totals[destination] += allocated
                allocations.append(
                    UmbrellaAllocation(purchase.key, own, destination, purchase.purchase_date, liters(allocated))
                )
                remaining -= allocated
            if remaining <= 0:
                break
        if remaining > 0:
            beyond += remaining

    return allocations, {key: liters(value) for key, value in totals.items()}, liters(beyond)


def reconciliation_status(expected: Decimal, observed: Decimal, due_date: date, today: date, tolerance: Decimal = Decimal("1.00")) -> str:
    difference = expected - observed
    if abs(difference) <= tolerance:
        return "confirmed"
    if today <= due_date and observed <= 0:
        return "pending"
    if observed > 0 and observed < expected - tolerance:
        return "partial" if today <= due_date else "overdue"
    if today > due_date and observed <= 0:
        return "overdue"
    return "divergent"
