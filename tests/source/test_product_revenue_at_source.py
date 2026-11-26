"""get_product_revenue_at: an upstream failure must not be reported (or cached) as "never filed"."""
import asyncio

import pytest

import app.sources as src


@pytest.fixture(autouse=True)
def clear_caches():
    src._last_filed_cache.clear()
    src._product_revenue_at_cache.clear()
    yield
    src._last_filed_cache.clear()
    src._product_revenue_at_cache.clear()


def test_last_filed_lookup_failure_is_error_not_never_filed(monkeypatch):
    calls = []

    async def failing(stock_id):
        calls.append(stock_id)
        return {"found": False, "error": "MOPS 連線錯誤：ConnectTimeout"}

    monkeypatch.setattr(src, "get_product_revenue", failing)
    out = asyncio.run(src.get_product_revenue_at("4527", 2024, 4))
    assert out["found"] is False
    assert out["error"].startswith("MOPS 連線錯誤")
    assert out["notes"] is None                       # not "查無該公司…任何申報記錄"
    assert "4527" not in src._last_filed_cache        # retried on the next call
    asyncio.run(src.get_product_revenue_at("4527", 2024, 4))
    assert calls == ["4527", "4527"]


def test_genuinely_never_filed_is_cached(monkeypatch):
    async def never(stock_id):
        return {"found": False, "error": None, "notes": "該公司未申報各項產品業務營收（採 IFRSs 後採自願申報）。"}

    monkeypatch.setattr(src, "get_product_revenue", never)
    out = asyncio.run(src.get_product_revenue_at("2330", 2024, 4))
    assert out["found"] is False and "任何申報記錄" in out["notes"]
    assert src._last_filed_cache["2330"] is None
