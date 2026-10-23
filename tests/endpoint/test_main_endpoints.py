from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main


def _financials(stock_id: str = "2330", as_of: str = "2024-01-05") -> dict:
    return {"found": True, "stock_id": stock_id, "as_of": as_of, "eps": {"ttm_quarters": []}, "net_income": {"ttm_quarters": []}, "source": "test-source"}


def _company(stock_id: str = "2330") -> dict:
    return {"found": True, "stock_id": stock_id, "as_of": "2024-01-05", "basic": {"business_items": {"narrative": [], "categories": []}}, "eps": {"ttm_quarters": []}, "revenue": {}, "net_income": {"ttm_quarters": []}, "sources": ["test-source"], "value_chain": {"status": "ok", "memberships": [], "neighbors_by_chain": {}}}


@pytest.fixture
def client(monkeypatch):
    async def no_startup(*args, **kwargs):
        return None

    monkeypatch.setattr(main.icchain, "ensure_loaded", no_startup)
    with TestClient(main.app) as test_client:
        yield test_client


def test_catalog_and_chain_endpoints(client, monkeypatch):
    monkeypatch.setattr(main.icchain, "status", lambda: {"loaded": True, "loading": False, "chain_count": 1, "indexed_companies": 1})
    monkeypatch.setattr(main.icchain, "is_loaded", lambda: True)
    monkeypatch.setattr(main.icchain, "get_chain_response_bytes", lambda code: None)
    monkeypatch.setattr(main.icchain, "_chain_company_count", lambda chain: 1)
    monkeypatch.setattr(main.icchain, "list_chains", lambda: [{"ic_code": "D000", "ic_name": "test"}])
    monkeypatch.setattr(main.icchain, "get_chain", lambda code: {"ic_code": code, "ic_name": "test", "segments": {}})
    monkeypatch.setattr(main.icchain, "refresh_cache", lambda: None, raising=False)

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/chains").json()["chains"]
    assert client.get("/api/chain/D000").json()["ic_code"] == "D000"
    assert client.post("/api/chain/refresh").status_code == 200


def test_search_endpoint(client, monkeypatch):
    async def fake_search(keyword, limit=20):
        assert keyword == "2330"
        return [{"stock_id": "2330", "company_name": "台積電"}]

    monkeypatch.setattr(main, "search_companies", fake_search)
    response = client.get("/api/search", params={"q": "2330", "limit": 5})
    assert response.status_code == 200
    assert response.json()["results"][0]["stock_id"] == "2330"


def test_company_endpoint_family(client, monkeypatch):
    async def fake_query(stock_id, as_of=None):
        return _company(stock_id)

    async def fake_basic(stock_id):
        return {"found": True, "stock_id": stock_id}

    async def fake_business(stock_id):
        return {"found": True, "stock_id": stock_id, "narrative": [], "categories": []}

    async def fake_financials(stock_id, as_of=None):
        return _financials(stock_id, as_of or "2024-01-05")

    async def fake_revenue(stock_id, as_of=None):
        return {"found": True, "stock_id": stock_id, "as_of": as_of or "2024-01-05", "source": "test-source"}

    async def fake_dividend(stock_id, as_of=None):
        return {"found": True, "stock_id": stock_id, "as_of": as_of or "2024-01-05", "dividend": None, "source": "test-source"}

    async def fake_history(stock_id):
        return {"found": True, "stock_id": stock_id, "events": [], "source": "test-source"}

    async def fake_value_chain(stock_id):
        return {"found": True, "stock_id": stock_id, "status": "ok", "memberships": [], "neighbors_by_chain": {}}

    async def fake_product_revenue(stock_id, as_of=None):
        return {"found": True, "stock_id": stock_id, "as_of": as_of or "2024-01-05", "items": [], "source": "test-source"}

    for name, value in {
        "query": fake_query,
        "query_basic": fake_basic,
        "query_business_items": fake_business,
        "query_financials": fake_financials,
        "query_financials_yfinance": fake_financials,
        "query_revenue": fake_revenue,
        "query_revenue_twse": fake_revenue,
        "query_dividend": fake_dividend,
        "query_dividend_yfinance": fake_dividend,
        "query_dividend_history": fake_history,
        "query_dividend_history_yfinance": fake_history,
        "query_value_chain": fake_value_chain,
        "query_product_revenue": fake_product_revenue,
    }.items():
        monkeypatch.setattr(main, name, value)

    cases = [("2330", "2018-01-05"), ("2317", "2020-01-06"), ("2454", "2022-01-05"), ("1101", "2024-01-05"), ("2882", "2025-01-06")]
    for stock_id, as_of in cases:
        paths = [
            f"/api/company/{stock_id}?as_of={as_of}",
            f"/api/company/{stock_id}/basic",
            f"/api/company/{stock_id}/business-items",
            f"/api/company/{stock_id}/financials?as_of={as_of}",
            f"/api/company/{stock_id}/financials/yfinance?as_of={as_of}",
            f"/api/company/{stock_id}/revenue?as_of={as_of}",
            f"/api/company/{stock_id}/revenue/twse?as_of={as_of}",
            f"/api/company/{stock_id}/dividend?as_of={as_of}",
            f"/api/company/{stock_id}/dividend/yfinance?as_of={as_of}",
            f"/api/company/{stock_id}/dividend/history",
            f"/api/company/{stock_id}/dividend/history/yfinance",
            f"/api/company/{stock_id}/value-chain",
            f"/api/company/{stock_id}/product-revenue?as_of={as_of}",
        ]
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)


def test_market_data_endpoint_family(client, monkeypatch):
    async def fake_ohlcv(*args, **kwargs):
        return {"found": True, "stk_code": "2330", "from_date": "2024-01-05", "to_date": "2024-01-05", "strategy": "test", "data": []}

    async def fake_institutional(*args, **kwargs):
        return {"found": True, "stk_code": "2330", "trade_date": "2024-01-05", "source": "test-source"}

    async def fake_foreign(*args, **kwargs):
        return {"trade_date": "2024-01-05", "stock_id": "2330", "stock_name": "台積電"}

    async def fake_market_valuation(*args, **kwargs):
        return {"found": True, "date": "2024-01-05", "market_scope": "TWSE", "calculation_method": "test", "source": "test-source"}

    async def fake_company_valuation(*args, **kwargs):
        return {"found": True, "date": "2024-01-05", "stock_id": "2330", "calculation_method": "test", "source": "test-source", "constituent": {"stk_code": "2330", "included_in_per": True, "included_in_pbr": True, "included_in_yield": True}}

    async def fake_analysis(*args, **kwargs):
        return {"found": True, "year": 2024, "stock_id": "2330", "source": "test-source"}

    monkeypatch.setattr(main.ohlcv_source, "get_ohlcv", fake_ohlcv)
    monkeypatch.setattr(main.institutional_source, "get_institutional_net_buy_sell", fake_institutional)
    monkeypatch.setattr(main.foreign_ownership_source, "get_foreign_ownership", fake_foreign)
    monkeypatch.setattr(main.valuation_source, "get_market_valuation_summary", fake_market_valuation)
    monkeypatch.setattr(main.valuation_source, "get_company_valuation", fake_company_valuation)
    monkeypatch.setattr(main.mops_financial_analysis, "get_annual_financial_analysis", fake_analysis)

    cases = [("2330", "2018-01-05"), ("2317", "2020-01-06"), ("2454", "2022-01-05"), ("1101", "2024-01-05"), ("2882", "2025-01-06")]
    for stock_id, day in cases:
        requests = [
            ("/api/ohlcv", {"stk_code": stock_id, "from": day, "to": day}),
            ("/api/institutional-net-buy-sell", {"stk_code": stock_id, "date": day}),
            (f"/api/company/{stock_id}/foreign-ownership", {"as_of": day}),
            ("/api/market-valuation-summary", {"date": day}),
            ("/api/company-valuation", {"stock_id": stock_id, "date": day}),
            ("/api/v1/fundamentals/financial-analysis", {"stock_id": stock_id, "year": day[:4]}),
        ]
        for path, params in requests:
            response = client.get(path, params=params)
            assert response.status_code == 200, (path, response.text)


def test_product_filers_endpoint(client, monkeypatch):
    async def fake_filers(ym, market):
        return {"found": True, "ym": ym, "market": market, "filers": []}

    monkeypatch.setattr(main, "query_product_revenue_filers", fake_filers)
    response = client.get("/api/product-revenue/filers", params={"ym": "202401", "market": "sii"})
    assert response.status_code == 200
    assert response.json()["ym"] == "202401"


def test_endpoint_validation_and_not_found(client, monkeypatch):
    async def not_found(*args, **kwargs):
        return {"found": False, "stock_id": "9999", "error": "not found"}

    monkeypatch.setattr(main, "query", not_found)
    assert client.get("/api/company/9999").status_code == 404
    assert client.get("/api/ohlcv", params={"stk_code": "2330", "from": "bad", "to": "2024-01-05"}).status_code == 400
    assert client.get("/api/institutional-net-buy-sell", params={"stk_code": "2330", "date": "bad"}).status_code == 400
    assert client.get("/api/company/2330/foreign-ownership", params={"as_of": "2000-01-01"}).status_code == 400
    assert client.get("/api/company-valuation", params={"stock_id": "2330", "date": "2000-01-01"}).status_code == 400
    assert client.get("/api/v1/fundamentals/financial-analysis", params={"stock_id": "2330", "year": "2999"}).status_code == 400