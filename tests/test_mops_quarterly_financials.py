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