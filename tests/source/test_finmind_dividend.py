"""I012: FinMind TaiwanStockDividend is filtered upstream by `date` (the record date, ~5 days
after the ex-dividend date). query_dividend must fetch past as_of so an event whose ex-date is
on/just before as_of is not dropped, while still never returning an event after as_of."""
import asyncio
from datetime import date

from app import service

ROWS = [
    {"date": "2021-07-28", "year": "109年", "CashExDividendTradingDate": "2021-07-22",
     "CashEarningsDistribution": 4.0, "CashStatutorySurplus": 0, "StockEarningsDistribution": 0,
     "StockStatutorySurplus": 0, "AnnouncementDate": "2021-07-07"},
    {"date": "2022-07-10", "year": "110年", "CashExDividendTradingDate": "2022-07-04",
     "CashEarningsDistribution": 5.2, "CashStatutorySurplus": 0, "StockEarningsDistribution": 0,
     "StockStatutorySurplus": 0, "AnnouncementDate": "2022-06-08"},
]


def _fake_finmind(calls):
    async def fake(stock_id, start, end):
        calls.append((start, end))
        return [r for r in ROWS if start <= r["date"] <= end]   # FinMind filters on `date`
    return fake


def test_event_on_its_ex_date_is_returned(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "get_dividend", _fake_finmind(calls))
    out = asyncio.run(service.query_dividend("2317", "2022-07-04"))
    assert out["dividend"]["reference_date"] == "2022-07-04"
    assert out["dividend"]["cash_dividend"] == 5.2
    assert calls[0][1] > "2022-07-10"   # window reaches the record date


def test_event_after_as_of_is_not_returned(monkeypatch):
    monkeypatch.setattr(service, "get_dividend", _fake_finmind([]))
    out = asyncio.run(service.query_dividend("2317", "2022-07-03"))
    assert out["dividend"]["reference_date"] == "2021-07-22"


def test_lag_constant_covers_record_date():
    assert service.FINMIND_DIVIDEND_RECORD_LAG_DAYS >= (date(2022, 7, 10) - date(2022, 7, 4)).days
