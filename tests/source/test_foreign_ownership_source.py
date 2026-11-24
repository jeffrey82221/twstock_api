from __future__ import annotations

import asyncio
from datetime import date, timedelta

import app.foreign_ownership_source as fos


def _csv(*rows: str) -> bytes:
    return ("\n".join(rows) + "\n").encode("big5")


class TestParseMiQfiisCsv:
    def test_parses_rows_and_roc_date(self):
        content = _csv(
            '"115年09月18日 外資及陸資投資持股統計"',
            '"證券代號","證券名稱","國際證券編碼","發行股數","外資及陸資尚可投資股數","全體外資及陸資持有股數","外資及陸資尚可投資比率","全體外資及陸資持股比率","外資及陸資共用法令投資上限比率","陸資法令投資上限比率","與前日異動原因(註)","最近一次上市公司申報外資及陸資持股異動日期"',
            '="2330","台積電","TW0002330008","10,000","7,000","3,000","70.00","30.00","40.00","40.00","","115/09/01"',
            '"說明：本資料僅供參考"',
        )
        result = fos.parse_mi_qfiis_csv(content, date(2026, 9, 18))

        assert set(result) == {"2330"}
        assert result["2330"] == {
            "trade_date": "2026-09-18",
            "stock_id": "2330",
            "stock_name": "台積電",
            "issued_shares": 10000.0,
            "foreign_investment_available_shares": 7000.0,
            "foreign_investment_shares": 3000.0,
            "foreign_investment_available_ratio_pct": 70.0,
            "foreign_investment_ratio_pct": 30.0,
            "foreign_investment_limit_ratio_pct": 40.0,
            "china_investment_limit_ratio_pct": 40.0,
            "change_reason": "",
            "last_change_date": "2026-09-01",
        }

    def test_invalid_payload_returns_empty(self):
        assert fos.parse_mi_qfiis_csv(b"not a csv", date(2026, 9, 18)) == {}


def test_get_foreign_ownership_returns_single_day_row(monkeypatch, tmp_path):
    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)
    payload = {"2330": {"trade_date": "2026-09-18", "stock_id": "2330"}, "1101": {"trade_date": "2026-09-18", "stock_id": "1101"}}

    async def fake_fetch(report_date):
        assert report_date == date(2026, 9, 18)
        return payload

    monkeypatch.setattr(fos, "_fetch_day", fake_fetch)
    row = asyncio.run(fos.get_foreign_ownership("2330", date(2026, 9, 18)))

    assert row == payload["2330"]


def test_get_foreign_ownership_falls_back_to_previous_available_day(monkeypatch, tmp_path):
    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)
    requested_dates = []
    previous_day = date(2026, 9, 18)

    async def fake_fetch(report_date):
        requested_dates.append(report_date)
        if report_date == previous_day:
            return {"2330": {"trade_date": previous_day.isoformat(), "stock_id": "2330"}}
        return {}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(fos, "_fetch_day", fake_fetch)
    monkeypatch.setattr(fos.asyncio, "sleep", no_sleep)
    row = asyncio.run(fos.get_foreign_ownership("2330", date(2026, 9, 20)))

    assert requested_dates == [date(2026, 9, 20), date(2026, 9, 19), previous_day]
    assert row["trade_date"] == "2026-09-18"

def _no_sleep_fetch(monkeypatch, tmp_path, responder):
    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)
    requested = []

    async def fake_fetch(report_date):
        requested.append(report_date)
        return responder(report_date)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(fos, "_fetch_day", fake_fetch)
    monkeypatch.setattr(fos.asyncio, "sleep", no_sleep)
    return requested


def test_lookback_is_bounded_by_max_lookback_days(monkeypatch, tmp_path):
    # every day is a non-trading day → must stop after max_lookback_days, not walk to MIN_DATE
    requested = _no_sleep_fetch(monkeypatch, tmp_path, lambda d: {})
    row = asyncio.run(fos.get_foreign_ownership("2330", date(2008, 1, 5)))
    assert row is None
    assert len(requested) == fos.MAX_LOOKBACK_DAYS + 1
    assert min(requested) == date(2008, 1, 5) - timedelta(days=fos.MAX_LOOKBACK_DAYS)


def test_lookback_never_goes_below_min_date(monkeypatch, tmp_path):
    requested = _no_sleep_fetch(monkeypatch, tmp_path, lambda d: {})
    asyncio.run(fos.get_foreign_ownership("2330", fos.MIN_DATE + timedelta(days=3)))
    assert min(requested) == fos.MIN_DATE


def test_trading_day_without_the_stock_stops_immediately(monkeypatch, tmp_path):
    # MI_QFIIS lists every listed stock on a trading day; absence means "not covered" (e.g. 上櫃)
    requested = _no_sleep_fetch(monkeypatch, tmp_path, lambda d: {"1101": {"trade_date": d.isoformat()}})
    row = asyncio.run(fos.get_foreign_ownership("5483", date(2024, 3, 5)))
    assert row is None
    assert requested == [date(2024, 3, 5)]


def test_custom_lookback_can_be_widened(monkeypatch, tmp_path):
    target = date(2024, 1, 1)
    requested = _no_sleep_fetch(
        monkeypatch, tmp_path,
        lambda d: {"2330": {"trade_date": d.isoformat()}} if d == target else {},
    )
    assert asyncio.run(fos.get_foreign_ownership("2330", date(2024, 3, 5))) is None
    requested.clear()
    row = asyncio.run(fos.get_foreign_ownership("2330", date(2024, 3, 5), max_lookback_days=90))
    assert row["trade_date"] == "2024-01-01"


def _blocked_response(url: str):
    import httpx

    request = httpx.Request("GET", url)
    return httpx.Response(307, request=request, text="<html>TWSE block page</html>")


def test_fetch_day_upstream_block_raises_and_is_rate_limited(monkeypatch, tmp_path):
    # TWSE's WAF answers blocked IPs with a 307 + HTML block page (no followable Location).
    # That must surface as an upstream error, not as an empty "non-trading day" payload.
    import httpx
    import pytest

    from app.sources import UpstreamFetchError, _request_source_errors

    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)

    async def fake_get(self, url, **kwargs):
        return _blocked_response(url)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def run():
        _request_source_errors.set([])
        with pytest.raises(UpstreamFetchError):
            await fos._fetch_day(date(2026, 8, 28))
        return list(_request_source_errors.get())

    errors = asyncio.run(run())
    assert len(errors) == 1 and errors[0].status_code == 307 and errors[0].is_rate_limited
    assert not list(tmp_path.iterdir())  # failure is not cached


def test_upstream_error_stops_lookback(monkeypatch, tmp_path):
    import pytest

    from app.sources import UpstreamFetchError

    def responder(d):
        raise UpstreamFetchError("blocked")

    requested = _no_sleep_fetch(monkeypatch, tmp_path, responder)
    with pytest.raises(UpstreamFetchError):
        asyncio.run(fos.get_foreign_ownership("2330", date(2026, 8, 31)))
    assert requested == [date(2026, 8, 31)]  # no day-by-day hammering of a blocking upstream
