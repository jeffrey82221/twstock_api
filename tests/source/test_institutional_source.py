"""institutional_source：非交易日回溯到最近交易日（I007）。"""
import asyncio
from datetime import date, timedelta

import pytest

import app.institutional_source as inst

ROW = {"stock_name": "台積電", "foreign_investors_net_buy_sell": 100, "foreign_dealers_net_buy_sell": 0,
       "investment_trust_net_buy_sell": 5, "dealers_net_buy_sell": -3, "dealers_proprietary_net_buy_sell": -1,
       "dealers_hedge_net_buy_sell": -2, "total_institutional_net_buy_sell": 102}


@pytest.fixture
def market(monkeypatch):
    """Fake TWSE: trading days -> payload; cached_empty -> non-trading days already in the disk cache."""
    state = {"trading": {}, "cached_empty": set(), "calls": []}

    async def fake_basic():
        return {"2330": {"market": "上市", "short_name": "台積電"}}

    async def fake_fetch(mkt, d):
        state["calls"].append(d)
        return state["trading"].get(d, {})

    def fake_cache_read(key, d):
        if d in state["trading"]:
            return state["trading"][d]
        return {} if d in state["cached_empty"] else None

    monkeypatch.setattr(inst, "load_basic_table", fake_basic)
    monkeypatch.setattr(inst, "_fetch_per_day_market", fake_fetch)
    monkeypatch.setattr(inst, "_cache_read", fake_cache_read)
    return state


def test_trading_day_is_returned_as_is(market):
    market["trading"][date(2026, 9, 4)] = {"2330": ROW}
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", date(2026, 9, 4)))
    assert out["row"]["trade_date"] == "2026-09-04"
    assert out["data_date"] == "2026-09-04"
    assert market["calls"] == [date(2026, 9, 4)]


def test_weekend_walks_back_to_friday(market):
    market["trading"][date(2026, 9, 4)] = {"2330": ROW}       # Fri
    market["cached_empty"].add(date(2026, 9, 5))              # Sat
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", date(2026, 9, 5)))
    assert out["trade_date"] == "2026-09-05"                  # requested date kept (seed key)
    assert out["row"]["trade_date"] == "2026-09-04"           # actual trading day
    assert out["data_date"] == "2026-09-04"
    assert out["row"]["total_institutional_net_buy_sell"] == 102


def test_long_holiday_within_cap(market):
    start = date(2026, 2, 20)
    market["trading"][start - timedelta(days=9)] = {"2330": ROW}
    market["cached_empty"].update(start - timedelta(days=i) for i in range(9))
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", start))
    assert out["row"]["trade_date"] == (start - timedelta(days=9)).isoformat()


def test_lookback_is_capped(market):
    start = date(2026, 2, 20)
    market["cached_empty"].update(start - timedelta(days=i) for i in range(30))
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", start))
    assert out["row"] is None
    assert len(market["calls"]) == inst.MAX_LOOKBACK_DAYS + 1


def test_download_failure_does_not_walk_back(market):
    # Empty payload that is not in the cache = upstream failure, not a holiday.
    market["trading"][date(2026, 9, 3)] = {"2330": ROW}
    out = asyncio.run(inst.get_institutional_net_buy_sell("2330", date(2026, 9, 4)))
    assert out["row"] is None
    assert market["calls"] == [date(2026, 9, 4)]
