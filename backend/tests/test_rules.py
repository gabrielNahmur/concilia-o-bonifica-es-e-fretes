from datetime import date
from decimal import Decimal

from app.services.rules import (
    UmbrellaPurchase,
    allocate_umbrella,
    br_bonus_value,
    contract_pace_status,
    current_month_projection,
    due_date_for_month,
    money,
    monthly_target,
    projected_completion_date,
    s10_excess_bonus,
)


def test_monthly_target_and_partial_projection():
    assert monthly_target(Decimal("5220000"), 36) == Decimal("145000.000")
    assert current_month_projection(Decimal("50000"), date(2026, 7, 10)) == Decimal("155000.000")


def test_projected_completion_and_zero_average():
    assert projected_completion_date(Decimal("300000"), Decimal("100000"), date(2026, 7, 10)) == date(2026, 10, 10)
    assert projected_completion_date(Decimal("300000"), Decimal("0"), date(2026, 7, 10)) is None


def test_contract_pace_uses_one_calendar_month_tolerance():
    contract_end = date(2028, 6, 15)
    assert contract_pace_status(date(2028, 5, 15), contract_end) == "on_track"
    assert contract_pace_status(date(2028, 7, 15), contract_end) == "on_track"
    assert contract_pace_status(date(2028, 4, 15), contract_end) == "ahead"
    assert contract_pace_status(date(2028, 8, 15), contract_end) == "late"
    assert contract_pace_status(None, contract_end) == "late"
    assert contract_pace_status(date(2030, 1, 1), contract_end, completed=True) == "completed"


def test_br_three_milestones_equal_105k():
    assert br_bonus_value(Decimal("1320000")) == Decimal("105000.00")


def test_umbrella_splits_purchase_at_exact_contract_limit():
    purchases = [
        UmbrellaPurchase(1, "002", date(2026, 1, 1), Decimal("2600000")),
        UmbrellaPurchase(2, "002", date(2026, 1, 2), Decimal("100000")),
    ]
    allocations, totals, beyond = allocate_umbrella(purchases)
    split = [row for row in allocations if row.purchase_key == 2]
    assert [(row.contract_unit, row.liters) for row in split] == [
        ("002", Decimal("40000.000")),
        ("006", Decimal("60000.000")),
    ]
    assert totals["002"] == Decimal("2640000.000")
    assert totals["006"] == Decimal("60000.000")
    assert beyond == Decimal("0.000")


def test_invoice_discount_and_s10_bonus_cases():
    assert money(Decimal("15000") * Decimal("0.04")) == Decimal("600.00")
    assert s10_excess_bonus(Decimal("160000")) == Decimal("1000.00")


def test_shell_deposit_due_on_twentieth_next_month():
    assert due_date_for_month(date(2026, 6, 1), 1, 20) == date(2026, 7, 20)
