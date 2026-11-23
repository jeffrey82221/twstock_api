import asyncio
from datetime import date

import pytest

import app.mops_quarterly_financials as mqf


def _table(headers, values):
    head = "".join(f"<th>{value}</th>" for value in headers)
    cells = "".join(f"<td>{value}</td>" for value in values)
    return f'<table class="hasBorder"><tr>{head}</tr><tr>{cells}</tr></table>'


def test_parse_operating_analysis_html():
    headers = ["公司代號", "公司名稱", "營業收入", "毛利率", "營業利益率", "稅前純益率", "稅後純益率"]
    html = _table(headers, ["2330", "台積電", "592,644.20", "53.07", "42.02", "44.98", "38.00"])
    assert mqf.parse_operating_analysis_html(html)["2330"]["gross_margin_pct"] == 53.07


def test_parse_income_statement_html_extracts_eps():
    headers = ["公司代號", "公司名稱"] + [f"欄位{i}" for i in range(27)] + ["基本每股盈餘（元）"]
    values = ["2330", "台積電"] + ["--"] * 27 + ["12.34"]
    assert mqf.parse_income_statement_html(_table(headers, values))["2330"]["eps"] == 12.34


def test_period_uses_previous_completed_quarter():
    assert mqf._period_for(date(2024, 5, 10)) == (113, 1, date(2024, 3, 31))


def test_period_rejects_before_verified_source_boundary():
    with pytest.raises(ValueError):
        mqf._period_for(date(2013, 3, 30))


def test_previous_period_crosses_year_boundary():
    assert mqf._previous_period(113, 1) == (112, 4, date(2023, 12, 31))


def test_get_quarterly_financials_uses_market_and_combines_sources(monkeypatch):
    async def fake_basic():
        return {"2330": {"market": "上市"}}

    async def fake_fetch(kind, market, year_tw, quarter):
        if kind == "operating":
            return {"2330": {"stock_id": "2330", "company_name": "台積電", "gross_margin_pct": 53.0}}
        return {"2330": {"eps": 12.34}}

    monkeypatch.setattr(mqf, "load_basic_table", fake_basic)
    monkeypatch.setattr(mqf, "_fetch", fake_fetch)
    result = asyncio.run(mqf.get_quarterly_financials(" 2330 ", date(2024, 5, 10)))
    assert result["found"] is True
    assert result["data_date"] == "2024-03-31"
    assert result["data"]["eps"] == 12.34


def test_get_quarterly_financials_falls_back_to_previous_available_quarter(monkeypatch):
    async def fake_basic():
        return {"2330": {"market": "上市"}}

    async def fake_fetch(kind, market, year_tw, quarter):
        if quarter == 2:
            return {}
        if kind == "operating":
            return {"2330": {"stock_id": "2330", "company_name": "台積電", "gross_margin_pct": 50.0}}
        return {"2330": {"eps": 10.0}}

    monkeypatch.setattr(mqf, "load_basic_table", fake_basic)
    monkeypatch.setattr(mqf, "_fetch", fake_fetch)
    result = asyncio.run(mqf.get_quarterly_financials("2330", date(2024, 8, 10)))
    assert result["data_date"] == "2024-03-31"

def _patch_basic(monkeypatch, market="上市"):
    async def fake_basic():
        return {"2330": {"market": market}}

    monkeypatch.setattr(mqf, "load_basic_table", fake_basic)


def test_get_quarterly_financials_caps_lookback(monkeypatch):
    """An unfiled / absent stock must not walk back to 2013 (two full-market
    MOPS downloads per quarter)."""
    _patch_basic(monkeypatch)
    calls = []

    async def fake_fetch(kind, market, year_tw, quarter):
        calls.append((kind, year_tw, quarter))
        return {}

    monkeypatch.setattr(mqf, "_fetch", fake_fetch)
    result = asyncio.run(mqf.get_quarterly_financials("2330", date(2024, 5, 10)))
    assert result["found"] is False
    assert result["reason"] == "not_found"
    assert len({(y, q) for _, y, q in calls}) == mqf.MAX_LOOKBACK_QUARTERS


def test_get_quarterly_financials_stops_on_upstream_failure(monkeypatch):
    """A failed download (`None`) is not "stock absent": stop instead of
    scanning further back."""
    _patch_basic(monkeypatch)
    calls = []

    async def fake_fetch(kind, market, year_tw, quarter):
        calls.append((year_tw, quarter))
        return None

    monkeypatch.setattr(mqf, "_fetch", fake_fetch)
    result = asyncio.run(mqf.get_quarterly_financials("2330", date(2024, 5, 10)))
    assert result["found"] is False
    assert result["reason"] == "upstream_error"
    assert set(calls) == {(113, 1)}


def test_get_quarterly_financials_times_out_within_budget(monkeypatch):
    import functools

    _patch_basic(monkeypatch)

    async def slow_fetch(kind, market, year_tw, quarter):
        await asyncio.sleep(5)
        return {}

    monkeypatch.setattr(mqf, "_fetch", slow_fetch)
    monkeypatch.setattr(mqf, "run_within_budget", functools.partial(mqf.run_within_budget, budget_s=0.05))
    result = asyncio.run(mqf.get_quarterly_financials("2330", date(2024, 5, 10)))
    assert result["found"] is False
    assert result["reason"] == "upstream_timeout"
    assert result["data_date"] == "2024-03-31"


def test_fetch_failure_returns_none_and_is_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(mqf, "CACHE_ROOT", tmp_path)

    class _FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            raise mqf.httpx.ConnectTimeout("boom")

    monkeypatch.setattr(mqf.httpx, "AsyncClient", _FailingClient)
    assert asyncio.run(mqf._fetch("operating", "sii", 113, 1)) is None
    assert not any(tmp_path.iterdir())
