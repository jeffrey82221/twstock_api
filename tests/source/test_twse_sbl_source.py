import asyncio
from datetime import date, timedelta

import app.twse_sbl_source as sbl


def test_parse_sbl_payload_normalizes_roc_dates_and_numbers():
    payload = {
        "stat": "OK",
        "fields": ["借券成交日期", "證券代號", "證券名稱", "交易方式", "成交數量(交易單位)", "成交費率(%)", "完成還券日收盤價", "完成還券日期", "借券天數"],
        "data": [["112年10月11日", "2330", "台積電", "議借", "3,100", "0.53", "580.00", "113年01月16日", 97]],
    }
    assert sbl.parse_sbl_payload(payload) == [{
        "transaction_date": "2023-10-11", "stock_id": "2330", "stock_name": "台積電",
        "transaction_type": "議借", "quantity_lots": 3100.0, "fee_rate_pct": 0.53,
        "completion_close_price": 580.0, "completion_date": "2024-01-16", "lending_days": 97,
    }]


def test_parse_sbl_payload_skips_invalid_rows():
    payload = {"fields": ["借券成交日期", "證券代號", "證券名稱", "交易方式", "成交數量(交易單位)", "成交費率(%)", "完成還券日收盤價", "完成還券日期", "借券天數"], "data": [["bad"]]}
    assert sbl.parse_sbl_payload(payload) == []


def test_get_sbl_history_returns_latest_available_completion_date(monkeypatch):
    async def fake_basic():
        return {"2330": {"market": "上市"}}

    async def fake_fetch(stock_id, start, end):
        if end == date(2024, 1, 31):
            return [{"completion_date": "2024-01-26", "stock_id": "2330"}]
        return []

    monkeypatch.setattr(sbl, "load_basic_table", fake_basic)
    monkeypatch.setattr(sbl, "_fetch_window", fake_fetch)
    result = asyncio.run(sbl.get_sbl_history("2330", date(2024, 1, 31)))
    assert result["found"] is True
    assert result["data_date"] == "2024-01-26"


def test_get_sbl_history_unknown_stock(monkeypatch):
    async def fake_basic():
        return {}

    monkeypatch.setattr(sbl, "load_basic_table", fake_basic)
    result = asyncio.run(sbl.get_sbl_history("9999", date(2024, 1, 31)))
    assert result["found"] is False


def test_min_date_is_completion_date_boundary():
    assert sbl.SBL_MIN_DATE.isoformat() == "2005-01-28"

def _sbl_fixture(monkeypatch, responder):
    windows = []

    async def fake_basic():
        return {"2330": {"market": "上市"}}

    async def fake_fetch(stock_id, start, end):
        windows.append((start, end))
        return responder(start, end)

    monkeypatch.setattr(sbl, "load_basic_table", fake_basic)
    monkeypatch.setattr(sbl, "_fetch_window", fake_fetch)
    return windows


def test_default_lookback_is_a_single_window(monkeypatch):
    windows = _sbl_fixture(monkeypatch, lambda s, e: [])
    result = asyncio.run(sbl.get_sbl_history("2330", date(2024, 3, 5)))
    assert result["found"] is False
    assert windows == [(date(2024, 2, 4), date(2024, 3, 5))]  # 31 days inclusive, no walk back to 2005


def test_lookback_never_goes_below_min_date(monkeypatch):
    windows = _sbl_fixture(monkeypatch, lambda s, e: [])
    asyncio.run(sbl.get_sbl_history("2330", date(2005, 2, 5)))
    assert windows == [(sbl.SBL_MIN_DATE, date(2005, 2, 5))]


def test_wider_lookback_walks_multiple_windows(monkeypatch):
    hit = [{"completion_date": "2023-12-20", "stock_id": "2330"}]
    windows = _sbl_fixture(monkeypatch, lambda s, e: hit if s <= date(2023, 12, 20) <= e else [])
    assert asyncio.run(sbl.get_sbl_history("2330", date(2024, 3, 5)))["found"] is False
    windows.clear()
    result = asyncio.run(sbl.get_sbl_history("2330", date(2024, 3, 5), max_lookback_days=93))
    assert result["found"] is True and result["data_date"] == "2023-12-20"
    assert len(windows) == 3
    assert windows[-1][0] == date(2024, 3, 5) - timedelta(days=92)


def test_upstream_error_propagates_instead_of_not_found(monkeypatch):
    # A failed window must not become found=False (-> 404, which the DB http_cache keeps).
    import pytest

    from app.sources import UpstreamFetchError

    def responder(start, end):
        raise UpstreamFetchError("blocked")

    windows = _sbl_fixture(monkeypatch, responder)
    with pytest.raises(UpstreamFetchError):
        asyncio.run(sbl.get_sbl_history("2330", date(2024, 3, 5), max_lookback_days=93))
    assert len(windows) == 1


def test_non_ok_stat_is_upstream_error_and_not_cached(monkeypatch, tmp_path):
    """I010: only stat OK (with or without rows) may be cached; a throttle answer must not
    become a permanent "no returns in this window"."""
    import asyncio
    from datetime import date

    import httpx
    import pytest

    import app.twse_sbl_source as sbl
    from app.sources import UpstreamFetchError

    monkeypatch.setattr(sbl, "CACHE_ROOT", tmp_path)

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", str(url)), json={"stat": "查詢過於頻繁"})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(UpstreamFetchError):
        asyncio.run(sbl._fetch_window("2330", date(2024, 4, 1), date(2024, 4, 30)))
    assert not list(tmp_path.rglob("*.json.gz"))
