"""Per-stock-per-month disk cache in app/ohlcv_source.py.

raw_ohlcv_monthly and ohlcv_daily (two IMMVs) each call /api/ohlcv once per
seed row, and uvicorn workers do not share memory, so without a disk cache
every row hit TWSE STOCK_DAY / TPEx tradingStock twice. Only completed months
with an OK upstream payload may be cached.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

import app.ohlcv_source as ohlcv

TWSE_OK = {
    "stat": "OK",
    "data": [["113/01/02", "1,000", "50,000", "50.0", "51.0", "49.0", "50.5", "+0.5", "10", ""]],
}
TPEX_OK = {
    "stat": "ok",
    "tables": [{"data": [["113/01/02", "1", "50", "50.0", "51.0", "49.0", "50.5", "0.5", "10"]]}],
}


@pytest.fixture()
def fake_http(monkeypatch, tmp_path):
    monkeypatch.setattr(ohlcv, "CACHE_ROOT", tmp_path)
    calls: list[str] = []
    responses: dict[str, object] = {}

    async def _fake_get(url, **kwargs):
        calls.append(url)
        for key, payload in responses.items():
            if key in url:
                return payload
        return {"stat": "OK", "data": []}

    monkeypatch.setattr(ohlcv, "_http_get", _fake_get)
    return calls, responses


def _run(coro):
    return asyncio.run(coro)


def test_completed_month_detection():
    today = date(2026, 11, 23)
    assert ohlcv._is_completed_month(date(2026, 10, 1), today)
    assert ohlcv._is_completed_month(date(2025, 12, 1), today)
    assert not ohlcv._is_completed_month(date(2026, 11, 1), today)
    assert not ohlcv._is_completed_month(date(2026, 12, 1), today)


def test_twse_completed_month_hits_upstream_once(fake_http):
    calls, responses = fake_http
    responses["STOCK_DAY"] = TWSE_OK
    first = _run(ohlcv._fetch_twse_stock_day("2330", date(2024, 1, 1)))
    second = _run(ohlcv._fetch_twse_stock_day("2330", date(2024, 1, 1)))
    assert first == second and len(first) == 1
    assert first[0]["trade_date"] == "2024-01-02"
    assert len(calls) == 1


def test_tpex_completed_month_hits_upstream_once(fake_http):
    calls, responses = fake_http
    responses["tradingStock"] = TPEX_OK
    first = _run(ohlcv._fetch_tpex_trading_stock("5483", date(2024, 1, 1)))
    second = _run(ohlcv._fetch_tpex_trading_stock("5483", date(2024, 1, 1)))
    assert first == second and first[0]["volume"] == 1000
    assert len(calls) == 1


def test_current_month_is_not_cached(fake_http):
    calls, responses = fake_http
    responses["STOCK_DAY"] = TWSE_OK
    this_month = date.today().replace(day=1)
    _run(ohlcv._fetch_twse_stock_day("2330", this_month))
    _run(ohlcv._fetch_twse_stock_day("2330", this_month))
    assert len(calls) == 2


def test_non_ok_payload_is_not_cached(fake_http):
    calls, responses = fake_http
    responses["STOCK_DAY"] = {"stat": "查詢日期小於99年1月4日，請重新查詢!"}
    assert _run(ohlcv._fetch_twse_stock_day("2330", date(2009, 12, 1))) == []
    responses["STOCK_DAY"] = TWSE_OK
    assert len(_run(ohlcv._fetch_twse_stock_day("2330", date(2009, 12, 1)))) == 1
    assert len(calls) == 2


def test_cache_is_per_stock(fake_http):
    calls, responses = fake_http
    responses["STOCK_DAY"] = TWSE_OK
    _run(ohlcv._fetch_twse_stock_day("2330", date(2024, 1, 1)))
    _run(ohlcv._fetch_twse_stock_day("2317", date(2024, 1, 1)))
    assert len(calls) == 2


def test_throttle_answer_raises_instead_of_empty_month(fake_http):
    """2026-11-26 rebuild: TWSE failures returned [] -> /api/ohlcv 200 with rows=[] -> 454 months
    frozen empty in pop. A failure must surface (route answers 502)."""
    from app.sources import UpstreamFetchError

    calls, responses = fake_http
    responses["STOCK_DAY"] = {"stat": "查詢過於頻繁"}
    with pytest.raises(UpstreamFetchError):
        _run(ohlcv._fetch_twse_stock_day("2330", date(2020, 3, 1)))
    responses["tradingStock"] = {"stat": "error"}
    with pytest.raises(UpstreamFetchError):
        _run(ohlcv._fetch_tpex_trading_stock("5483", date(2020, 3, 1)))


def test_get_ohlcv_reports_upstream_error(fake_http, monkeypatch):
    calls, responses = fake_http
    responses["STOCK_DAY"] = {"stat": "查詢過於頻繁"}

    async def fake_market(code):
        return "上市"

    monkeypatch.setattr(ohlcv, "_resolve_market", fake_market)
    out = _run(ohlcv.get_ohlcv("2330", date(2020, 3, 1), date(2020, 3, 31)))
    assert out["found"] is False and out["reason"] == "upstream_error" and out["rows"] == []


# ---- per-day-market path (short ranges / TWSE before 2010): failure ≠ market closed ----
class _FakeResp:
    def __init__(self, body: bytes):
        self.content = body

    def raise_for_status(self):
        pass


def _fake_httpx(monkeypatch, body=None, exc=None):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            if exc is not None:
                raise exc
            return _FakeResp(body)

    monkeypatch.setattr(ohlcv.httpx, "AsyncClient", _Client)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(ohlcv.asyncio, "sleep", lambda *_: real_sleep(0))


def test_mi_index_download_failure_raises(fake_http, monkeypatch):
    from app.sources import UpstreamFetchError

    _fake_httpx(monkeypatch, exc=RuntimeError("boom"))
    with pytest.raises(UpstreamFetchError):
        _run(ohlcv._fetch_twse_mi_index_day(date(2008, 3, 3)))


def test_mi_index_error_page_raises_and_is_not_cached(fake_http, monkeypatch):
    from app.sources import UpstreamFetchError

    _fake_httpx(monkeypatch, body="<html>請稍後再試</html>".encode("ms950"))
    with pytest.raises(UpstreamFetchError):
        _run(ohlcv._fetch_twse_mi_index_day(date(2008, 3, 3)))
    assert not ohlcv._cache_path("twse", date(2008, 3, 3)).exists()


def test_mi_index_empty_body_is_market_closed(fake_http, monkeypatch):
    _fake_httpx(monkeypatch, body=b"")
    assert _run(ohlcv._fetch_twse_mi_index_day(date(2008, 3, 2))) == {}


def test_tpex_daily_quotes_failure_raises(fake_http):
    from app.sources import UpstreamFetchError

    calls, responses = fake_http
    responses["dailyQuotes"] = {"stat": "error"}
    with pytest.raises(UpstreamFetchError):
        _run(ohlcv._fetch_tpex_daily_quotes(date(2020, 3, 2)))


def test_get_ohlcv_per_day_failure_reports_upstream_error(fake_http, monkeypatch):
    calls, responses = fake_http
    responses["dailyQuotes"] = {"stat": "error"}

    async def fake_market(code):
        return "上櫃"

    monkeypatch.setattr(ohlcv, "_resolve_market", fake_market)
    out = _run(ohlcv.get_ohlcv("5483", date(2020, 3, 2), date(2020, 3, 4)))
    assert out["found"] is False and out["reason"] == "upstream_error"
    assert out["strategy"] == "per_day_market"


# ---- stat OK but rows unparseable (layout change) ≠ no trading: raise, never cache ----
def test_twse_stock_day_unparsed_rows_raise_and_are_not_cached(fake_http):
    from app.sources import UpstreamFetchError

    calls, responses = fake_http
    responses["STOCK_DAY"] = {"stat": "OK", "data": [["2024-01-02", "1,000"]]}   # 欄位不足
    with pytest.raises(UpstreamFetchError):
        _run(ohlcv._fetch_twse_stock_day("2330", date(2020, 3, 1)))
    assert not ohlcv._cache_path("twse_stock_day/2330", date(2020, 3, 1)).exists()


def test_tpex_trading_stock_unparsed_rows_raise_and_are_not_cached(fake_http):
    from app.sources import UpstreamFetchError

    calls, responses = fake_http
    responses["tradingStock"] = {"stat": "ok", "tables": [{"data": [["bad-date", "1", "50", "50", "51", "49", "50", "0", "10"]]}]}
    with pytest.raises(UpstreamFetchError):
        _run(ohlcv._fetch_tpex_trading_stock("5483", date(2020, 3, 1)))
    assert not ohlcv._cache_path("tpex_trading_stock/5483", date(2020, 3, 1)).exists()


def test_tpex_daily_quotes_unparsed_table_is_failure_not_holiday(fake_http):
    calls, responses = fake_http
    d = date(2020, 3, 2)
    responses["dailyQuotes"] = {"stat": "ok", "date": "20200302", "tables": [{"data": [["5483", "中美晶", "50"]]}]}
    assert _run(ohlcv.fetch_tpex_daily_quotes_checked(d)) is None
    assert not ohlcv._cache_path("tpex", d).exists()


def test_tpex_daily_quotes_empty_table_is_holiday(fake_http):
    calls, responses = fake_http
    d = date(2020, 3, 1)
    responses["dailyQuotes"] = {"stat": "ok", "date": "20200301", "tables": [{"data": []}]}
    assert _run(ohlcv.fetch_tpex_daily_quotes_checked(d)) == {}
