"""Point-in-time (PIT) availability rules for periodic financial data.

`as_of` in this API means "what was public on that date". A period's numbers become
public on its *available date*; a period is selectable for `as_of` only when
`available_date <= as_of`. Without this, a backtest on `as_of = 2024-04-01` would
read March revenue (published by 2024-04-10) or Q1 financials (published by 05-15).

Available date = the statutory filing deadline (證交法 §36 and its implementing rules),
not the actual filing date, which these sources do not provide. Companies that file
early are therefore seen a few days late; nothing is ever seen early. Late filers
(past the deadline) are the one exception and are rare.

Rules:
  * Monthly revenue: the 10th of the following month.
  * Quarterly financial reports (IFRS, 2013 onward):
      Q1 / Q3: 45 days after quarter end (05-15, 11-14)
      Q2:      45 days (08-14); banks, insurers, securities firms and financial
               holding companies have two months (08-31)
      Q4:      the annual report, three months after year end (03-31 next year)
"""
from __future__ import annotations

from datetime import date, timedelta

REVENUE_DEADLINE_DAY = 10


def month_end(year: int, month: int) -> date:
    first_next = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return first_next - timedelta(days=1)


def quarter_end(year: int, quarter: int) -> date:
    return month_end(year, quarter * 3)


def revenue_available_date(year: int, month: int) -> date:
    """Monthly revenue for (year, month) is public by the 10th of the next month."""
    return date(year + (month == 12), 1 if month == 12 else month + 1, REVENUE_DEADLINE_DAY)


def latest_available_revenue_month(as_of: date) -> tuple[int, int]:
    """The most recent revenue month whose deadline is on or before `as_of`."""
    year, month = as_of.year, as_of.month - 1          # last month
    if as_of.day < REVENUE_DEADLINE_DAY:
        month -= 1                                     # last month is not due yet
    while month < 1:
        year, month = year - 1, month + 12
    return year, month


def quarterly_available_date(year: int, quarter: int, financial: bool = True) -> date:
    """Filing deadline of the (year, quarter) report.

    `financial=True` applies the longer Q2 deadline of financial companies. It is
    the default because it is the safe bound when the company type is unknown.
    """
    if quarter == 4:
        return date(year + 1, 3, 31)
    if quarter == 2:
        return date(year, 8, 31) if financial else date(year, 8, 14)
    return quarter_end(year, quarter) + timedelta(days=45)


def quarter_of(period_end: date) -> tuple[int, int]:
    return period_end.year, (period_end.month - 1) // 3 + 1


def quarter_available_on(period_end: date, as_of: date, financial: bool = True) -> bool:
    """True when the quarter ending `period_end` was public on `as_of`."""
    year, quarter = quarter_of(period_end)
    return quarterly_available_date(year, quarter, financial) <= as_of


def previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def latest_available_quarter(as_of: date, financial: bool = False) -> tuple[int, int]:
    """The most recent quarter whose filing deadline is on or before `as_of`."""
    year, quarter = quarter_of(as_of)
    while quarterly_available_date(year, quarter, financial) > as_of:
        year, quarter = previous_quarter(year, quarter)
    return year, quarter
