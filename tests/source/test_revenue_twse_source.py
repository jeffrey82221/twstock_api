from __future__ import annotations

import asyncio
import functools

import app.service as service


def test_query_revenue_twse_times_out_within_budget(monkeypatch):
    """Cold MOPS t21sc03 fan-out (up to 26 pages) must not exceed the budget
    that keeps the API below Postgres's 45s http.timeout_msec (PR #80 CI)."""

    async def fake_basic(stock_id):
        return {"market": "上市"}

    async def slow_t187ap05(market_code):
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(service, "_get_basic", fake_basic)
    monkeypatch.setattr(service, "get_twse_monthly_revenue_all", slow_t187ap05)
    monkeypatch.setattr(
        service, "run_within_budget", functools.partial(service.run_within_budget, budget_s=0.05)
    )

    result = asyncio.run(service.query_revenue_twse("2330", "2010-01-01"))
    assert result["found"] is False
    assert result["reason"] == "upstream_timeout"
    assert result["as_of"] == "2010-01-01"
