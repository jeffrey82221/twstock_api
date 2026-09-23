import asyncio
from datetime import date

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