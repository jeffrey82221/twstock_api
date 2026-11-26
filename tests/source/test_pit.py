"""app/pit.py: statutory filing deadlines used as point-in-time availability (I002)."""
from datetime import date

import pytest

from app import pit


@pytest.mark.parametrize("year, month, expected", [
    (2024, 3, date(2024, 4, 10)),
    (2024, 12, date(2025, 1, 10)),
])
def test_revenue_available_on_10th_of_next_month(year, month, expected):
    assert pit.revenue_available_date(year, month) == expected


@pytest.mark.parametrize("as_of, expected", [
    (date(2026, 11, 1), (2026, 9)),     # October revenue not due until 11-10
    (date(2026, 11, 9), (2026, 9)),
    (date(2026, 11, 10), (2026, 10)),   # due date itself counts
    (date(2026, 1, 9), (2025, 11)),     # crosses the year boundary twice
    (date(2026, 1, 10), (2025, 12)),
    (date(2026, 2, 1), (2025, 12)),
])
def test_latest_available_revenue_month(as_of, expected):
    assert pit.latest_available_revenue_month(as_of) == expected
    y, m = expected
    assert pit.revenue_available_date(y, m) <= as_of


@pytest.mark.parametrize("year, quarter, financial, expected", [
    (2024, 1, False, date(2024, 5, 15)),
    (2024, 2, False, date(2024, 8, 14)),
    (2024, 2, True, date(2024, 8, 31)),
    (2024, 3, False, date(2024, 11, 14)),
    (2024, 4, False, date(2025, 3, 31)),
])
def test_quarterly_deadlines(year, quarter, financial, expected):
    assert pit.quarterly_available_date(year, quarter, financial) == expected


def test_unknown_company_type_uses_the_later_q2_deadline():
    assert pit.quarterly_available_date(2024, 2) == date(2024, 8, 31)


@pytest.mark.parametrize("as_of, financial, expected", [
    (date(2024, 3, 30), False, (2023, 3)),   # 2023 annual report not due until 03-31
    (date(2024, 3, 31), False, (2023, 4)),
    (date(2024, 5, 14), False, (2023, 4)),
    (date(2024, 5, 15), False, (2024, 1)),
    (date(2024, 8, 20), False, (2024, 2)),
    (date(2024, 8, 20), True, (2024, 1)),
    (date(2024, 12, 31), False, (2024, 3)),
])
def test_latest_available_quarter(as_of, financial, expected):
    assert pit.latest_available_quarter(as_of, financial) == expected


def test_quarter_available_on_uses_period_end():
    assert not pit.quarter_available_on(date(2024, 3, 31), date(2024, 5, 14))
    assert pit.quarter_available_on(date(2024, 3, 31), date(2024, 5, 15))
    assert not pit.quarter_available_on(date(2024, 6, 30), date(2024, 8, 20))   # financial bound
