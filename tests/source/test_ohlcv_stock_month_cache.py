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
