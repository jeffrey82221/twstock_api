"""Opt-in live contract checks for every upstream family used by app/main.py.

Run with::

    RUN_UPSTREAM_CONTRACT_TESTS=1 pytest -m upstream_contract -q

The default test run skips these network checks so ordinary CI is not coupled
to upstream rate limits or temporary outages.
"""
from __future__ import annotations

import csv
import io
import os

import httpx
import pytest

from app.foreign_ownership_source import MI_QFIIS_URL
from app.institutional_source import TPEX_DAILY_TRADE_URL, TWSE_T86_URL
from app.mops_financial_analysis import MOPS_T51SB02_URL
from app.ohlcv_source import (
    TPEX_DAILY_QUOTES_URL,
    TPEX_TRADING_STOCK_URL,
    TWSE_MI_INDEX_URL,
    TWSE_STOCK_DAY_URL,
)
from app.sources import (
    FINMIND_URL,
    GCIS_BUSINESS_URL,
    MOPS_T05ST08_URL,
    TPEX_BASIC_URL,
    TPEX_REVENUE_O_CSV_URL,
    TWSE_BASIC_URL,
    TWSE_REVENUE_L_URL,
)
from app.valuation_source import TWSE_BWIBBU_URL

pytestmark = pytest.mark.upstream_contract

LIVE = os.getenv("RUN_UPSTREAM_CONTRACT_TESTS") == "1"
pytestmark = [
    pytest.mark.upstream_contract,
    pytest.mark.skipif(not LIVE, reason="set RUN_UPSTREAM_CONTRACT_TESTS=1"),
]


def _get(url: str, **params) -> httpx.Response:
    response = httpx.get(url, params=params, timeout=45.0, follow_redirects=True, verify=False)
    response.raise_for_status()
    return response


def _json(response: httpx.Response) -> dict | list:
    payload = response.json()
    assert isinstance(payload, (dict, list)), "upstream stopped returning JSON"
    return payload


def test_twse_and_tpex_basic_contracts():
    twse = _json(_get(TWSE_BASIC_URL))
    tpex = _json(_get(TPEX_BASIC_URL))
    assert isinstance(twse, list) and twse
    assert isinstance(tpex, list) and tpex
    assert {"公司代號", "公司名稱"}.issubset(twse[0])
    assert {"SecuritiesCompanyCode", "CompanyName"}.issubset(tpex[0])


@pytest.mark.parametrize(
    ("dataset", "required_keys"),
    [
        ("TaiwanStockFinancialStatements", {"date", "stock_id", "type", "value"}),
        ("TaiwanStockMonthRevenue", {"date", "stock_id", "revenue"}),
        ("TaiwanStockDividend", {"stock_id"}),
    ],
)
def test_finmind_dataset_contracts(dataset: str, required_keys: set[str]):
    payload = _json(
        _get(
            FINMIND_URL,
            dataset=dataset,
            data_id="2330",
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
    )
    assert payload.get("status") == 200
    rows = payload.get("data")
    assert isinstance(rows, list) and rows
    assert required_keys.issubset(rows[0])


def test_gcis_business_contract():
    payload = _json(
        _get(
            GCIS_BUSINESS_URL,
            **{"$format": "json", "$filter": "Business_Accounting_NO eq 22099131"},
        )
    )
    assert isinstance(payload, list) and payload
    assert {"Business_Accounting_NO", "Company_Name", "Cmp_Business"}.issubset(payload[0])
    assert isinstance(payload[0]["Cmp_Business"], list)


def test_twse_revenue_contract():
    payload = _json(_get(TWSE_REVENUE_L_URL))
    assert isinstance(payload, list) and payload
    assert all(
        {"公司代號", "資料年月", "營業收入-當月營收"}.issubset(row)
        for row in payload[:5]
    )


def test_tpex_revenue_contract():
    response = _get(TPEX_REVENUE_O_CSV_URL)
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows and any("公司代號" in cell for cell in rows[0])


def test_twse_ohlcv_contracts():
    stock_day = _json(_get(TWSE_STOCK_DAY_URL, response="json", date="20240101", stockNo="2330"))
    market_index = _get(TWSE_MI_INDEX_URL, response="csv", date="20240105", type="ALLBUT0999")
    assert stock_day.get("stat") in {"OK", "很抱歉，沒有符合條件的資料"}
    assert isinstance(stock_day.get("fields"), list)
    assert market_index.content


def test_tpex_ohlcv_contracts():
    trading = _json(_get(TPEX_TRADING_STOCK_URL, code="6488", date="2024/01/01", id="", response="json"))
    quotes = _json(_get(TPEX_DAILY_QUOTES_URL, date="2024/01/05", id="", response="json", type="EW"))
    assert isinstance(trading, dict) and isinstance(quotes, dict)
    assert "tables" in trading or "aaData" in trading or "stat" in trading
    assert "tables" in quotes or "aaData" in quotes or "stat" in quotes


def test_institutional_contracts():
    twse = _json(_get(TWSE_T86_URL, date="20240105", selectType="ALL", response="json"))
    tpex = _json(_get(TPEX_DAILY_TRADE_URL, date="2024/01/05", type="Daily", sect="EW", response="json"))
    assert isinstance(twse, dict) and "stat" in twse
    assert isinstance(tpex, dict) and ("tables" in tpex or "stat" in tpex)


def test_foreign_ownership_contract():
    response = _get(MI_QFIIS_URL, response="csv", date="20240105", selectType="ALLBUT0999")
    rows = list(csv.reader(io.StringIO(response.content.decode("big5", errors="replace"))))
    assert rows and any("證券代號" in cell for cell in rows[0] + (rows[1] if len(rows) > 1 else []))


def test_valuation_contract():
    payload = _json(_get(TWSE_BWIBBU_URL, response="json", date="20240105", selectType="ALL"))
    assert payload.get("stat") == "OK"
    assert len(payload.get("fields", [])) >= 5
    assert payload.get("data")


def test_mops_contracts():
    financial = httpx.post(
        MOPS_T51SB02_URL,
        data={"TYPEK": "sii", "year": "113", "season": "1"},
        timeout=45.0,
        follow_redirects=True,
        verify=False,
    )
    product = httpx.post(
        MOPS_T05ST08_URL,
        data={"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "TYPEK": "sii", "co_id": "2330"},
        timeout=45.0,
        follow_redirects=True,
        verify=False,
    )
    assert financial.status_code == 200 and len(financial.content) > 1000
    assert product.status_code == 200 and len(product.content) > 1000


def test_ic_chain_contract():
    response = _get("https://ic.tpex.org.tw/introduce.php", ic="6000")
    text = response.text
    assert response.status_code == 200
    assert "main_ic_panel" in text or "產業" in text


def test_yfinance_contract():
    yfinance = pytest.importorskip("yfinance")
    ticker = yfinance.Ticker("2330.TW")
    frame = ticker.quarterly_financials
    assert frame is not None
    assert not frame.empty
    assert any(str(index) in {"Basic EPS", "Net Income", "Operating Income", "Total Revenue"} for index in frame.index)