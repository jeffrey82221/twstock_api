"""institutional_source：非交易日回溯到最近交易日（I007）；上游失敗不當成非交易日（I010）。"""
import asyncio
from datetime import date, timedelta

import pytest

import app.institutional_source as inst

ROW = {"stock_name": "台積電", "foreign_investors_net_buy_sell": 100, "foreign_dealers_net_buy_sell": 0,
       "investment_trust_net_buy_sell": 5, "dealers_net_buy_sell": -3, "dealers_proprietary_net_buy_sell": -1,
       "dealers_hedge_net_buy_sell": -2, "total_institutional_net_buy_sell": 102}


@pytest.fixture
def market(monkeypatch):
    """Fake per-day fetch: trading days -> payload, holidays -> {}, anything else -> None (upstream failure)."""
    state = {"trading": {}, "holidays": set(), "calls": []}

    async def fake_basic():
        return {"2330": {"market": "上市", "short_name": "台積電"}}

    async def fake_fetch(mkt, d):
        state["calls"].append(d)
        if d in state["trading"]:
            return state["trading"][d]
        return {} if d in state["holidays"] else None

    monkeypatch.setattr(inst, "load_basic_table", fake_basic)
    monkeypatch.setattr(inst, "_fetch_per_day_market", fake_fetch)
    return state


def test_trading_day_is_returned_as_is(market):
    market["trading"][date(2026, 9, 4)] = {"2330": ROW}
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", date(2026, 9, 4)))
    assert out["row"]["trade_date"] == "2026-09-04"
    assert out["data_date"] == "2026-09-04"
    assert market["calls"] == [date(2026, 9, 4)]


def test_weekend_walks_back_to_friday(market):
    market["trading"][date(2026, 9, 4)] = {"2330": ROW}       # Fri
    market["holidays"].add(date(2026, 9, 5))              # Sat
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", date(2026, 9, 5)))
    assert out["trade_date"] == "2026-09-05"                  # requested date kept (seed key)
    assert out["row"]["trade_date"] == "2026-09-04"           # actual trading day
    assert out["data_date"] == "2026-09-04"
    assert out["row"]["total_institutional_net_buy_sell"] == 102


def test_long_holiday_within_cap(market):
    start = date(2026, 2, 20)
    market["trading"][start - timedelta(days=9)] = {"2330": ROW}
    market["holidays"].update(start - timedelta(days=i) for i in range(9))
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", start))
    assert out["row"]["trade_date"] == (start - timedelta(days=9)).isoformat()


def test_lookback_is_capped(market):
    start = date(2026, 2, 20)
    market["holidays"].update(start - timedelta(days=i) for i in range(30))
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", start))
    assert out["row"] is None
    assert len(market["calls"]) == inst.MAX_LOOKBACK_DAYS + 1


def test_download_failure_does_not_walk_back(market):
    market["trading"][date(2026, 9, 3)] = {"2330": ROW}
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", date(2026, 9, 4)))
    assert out["row"] is None
    assert out["reason"] == "upstream_error"
    assert market["calls"] == [date(2026, 9, 4)]


# --- I010: only an explicit "no data" answer may be cached as a non-trading day ---

def _serve(monkeypatch, tmp_path, payload):
    import httpx

    monkeypatch.setattr(inst, "CACHE_ROOT", tmp_path)

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url), json=payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


T86_ROW = ["2330", "台積電"] + ["1,000"] * 17


@pytest.mark.parametrize("payload", [
    {"stat": "查詢過於頻繁，請稍後再試"},            # throttle answer
    {"stat": None},
    "not a dict",
])
def test_t86_unexpected_payload_is_failure_and_not_cached(monkeypatch, tmp_path, payload):
    _serve(monkeypatch, tmp_path, payload)
    assert asyncio.run(inst._fetch_twse_t86_day(date(2017, 6, 5))) is None
    assert not list(tmp_path.rglob("*.json.gz"))


def test_t86_no_data_stat_is_cached_as_non_trading_day(monkeypatch, tmp_path):
    _serve(monkeypatch, tmp_path, {"stat": "很抱歉，沒有符合條件的資料!", "total": 0})
    assert asyncio.run(inst._fetch_twse_t86_day(date(2017, 5, 30))) == {}
    assert inst._cache_read("twse", date(2017, 5, 30)) == {}


def test_t86_ok_payload_is_parsed_and_cached(monkeypatch, tmp_path):
    _serve(monkeypatch, tmp_path, {"stat": "OK", "data": [T86_ROW]})
    out = asyncio.run(inst._fetch_twse_t86_day(date(2017, 6, 5)))
    assert out["2330"]["total_institutional_net_buy_sell"] == 1000
    assert inst._cache_read("twse", date(2017, 6, 5)) == out


def test_tpex_payload_for_another_date_is_failure(monkeypatch, tmp_path):
    _serve(monkeypatch, tmp_path, {"stat": "ok", "date": "20261125", "tables": [{"date": "115/11/25", "data": []}]})
    assert asyncio.run(inst._fetch_tpex_daily_trade(date(2024, 4, 3))) is None
    assert not list(tmp_path.rglob("*.json.gz"))


def test_tpex_holiday_is_cached_as_empty(monkeypatch, tmp_path):
    _serve(monkeypatch, tmp_path, {"stat": "ok", "date": "20240404", "tables": [{"date": "113/04/04", "data": []}]})
    assert asyncio.run(inst._fetch_tpex_daily_trade(date(2024, 4, 4))) == {}
    assert inst._cache_read("tpex", date(2024, 4, 4)) == {}


T86_16_FIELDS = ["證券代號", "證券名稱", "外資買進股數", "外資賣出股數", "外資買賣超股數", "投信買進股數", "投信賣出股數",
                 "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)",
                 "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)",
                 "三大法人買賣超股數"]


def test_t86_pre_2017_12_18_layout_is_parsed_by_field_name(monkeypatch, tmp_path):
    """I010 root cause: before 2017-12-18 T86 has 16 columns; the 19-column parser dropped
    every row and the day was cached as a non-trading day."""
    row = ["2330", "台積電", "19,279,050", "9,741,202", "9,537,848", "0", "728,000", "-728,000", "-740,010",
           "320,000", "839,000", "-519,000", "375,990", "597,000", "-221,010", "8,069,838"]
    _serve(monkeypatch, tmp_path, {"stat": "OK", "fields": T86_16_FIELDS, "data": [row]})
    out = asyncio.run(inst._fetch_twse_t86_day(date(2017, 6, 5)))["2330"]
    assert out["foreign_investors_net_buy_sell"] == 9537848
    assert out["foreign_dealers_net_buy_sell"] is None
    assert out["investment_trust_net_buy_sell"] == -728000
    assert out["dealers_net_buy_sell"] == -740010
    assert out["dealers_hedge_net_buy_sell"] == -221010
    assert out["total_institutional_net_buy_sell"] == 8069838


def test_t86_rows_that_cannot_be_parsed_are_failure_not_holiday(monkeypatch, tmp_path):
    _serve(monkeypatch, tmp_path, {"stat": "OK", "fields": ["代號", "名稱"], "data": [["2330", "台積電"]]})
    assert asyncio.run(inst._fetch_twse_t86_day(date(2017, 6, 5))) is None
    assert not list(tmp_path.rglob("*.json.gz"))


def test_tpex_old_layout_in_second_table(monkeypatch, tmp_path):
    row = ["6488", "環球晶", "1,000", "400", "600", "10", "0", "10", "-5", "0", "5", "-5", "0", "0", "0", "605"]
    _serve(monkeypatch, tmp_path, {"stat": "ok", "date": "20180102",
                                    "tables": [{}, {"date": "107/01/02", "data": [row]}]})
    out = asyncio.run(inst._fetch_tpex_daily_trade(date(2018, 1, 2)))["6488"]
    assert out["foreign_investors_net_buy_sell"] == 600
    assert out["foreign_dealers_net_buy_sell"] is None
    assert out["total_institutional_net_buy_sell"] == 605
