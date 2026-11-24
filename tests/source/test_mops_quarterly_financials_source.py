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


# --- I001: MOPS tables are year-to-date; `data` must be the single quarter ---

def _ytd(rev, gm, eps):
    return {"stock_id": "2330", "company_name": "台積電", "revenue_millions": rev, "gross_margin_pct": gm,
            "operating_margin_pct": gm, "pretax_margin_pct": gm, "net_margin_pct": gm, "eps": eps}


def test_single_quarter_differences_amounts_and_ratios():
    # 2330 2026: Q1 YTD 1,134,103.44 / 22.08; H1 YTD 2,404,483.69 / 49.33.
    q2 = mqf.single_quarter(_ytd(2404483.69, 67.03, 49.33), _ytd(1134103.44, 66.25, 22.08))
    assert q2["revenue_millions"] == 1270380.25  # = Apr-Jun monthly revenue sum
    assert q2["eps"] == 27.25
    # Gross profit H1 - Q1 over Q2 revenue, not the YTD ratio.
    expected = round((67.03 * 2404483.69 - 66.25 * 1134103.44) / 1270380.25, 2)
    assert q2["gross_margin_pct"] == expected
    assert q2["gross_margin_pct"] != 67.03


def test_single_quarter_is_null_when_previous_quarter_missing():
    q = mqf.single_quarter(_ytd(2404483.69, 67.03, 49.33), None)
    assert q["revenue_millions"] is None and q["eps"] is None and q["gross_margin_pct"] is None


def _fake_tables(tables):
    async def fake_fetch(kind, market, year_tw, quarter):
        return tables.get((kind, quarter), {})
    return fake_fetch


def test_q4_returns_single_quarter_and_full_year_ytd(monkeypatch):
    _patch_basic(monkeypatch)
    tables = {
        ("operating", 4): {"2330": _ytd(3809054.27, 59.89, None)},
        ("income", 4): {"2330": {"eps": 66.26}},
        ("operating", 3): {"2330": _ytd(2762963.85, 59.0, None)},
        ("income", 3): {"2330": {"eps": 47.86}},
    }
    monkeypatch.setattr(mqf, "_fetch", _fake_tables(tables))
    result = asyncio.run(mqf.get_quarterly_financials("2330", date(2026, 1, 5)))
    assert (result["fiscal_year"], result["quarter"]) == (2025, 4)
    assert result["data_ytd"]["eps"] == 66.26          # full year, as MOPS reports it
    assert result["data"]["eps"] == round(66.26 - 47.86, 2)
    assert result["data"]["revenue_millions"] == round(3809054.27 - 2762963.85, 2)


def test_q1_data_equals_ytd_without_extra_fetch(monkeypatch):
    _patch_basic(monkeypatch)
    calls = []
    tables = {("operating", 1): {"2330": _ytd(1134103.44, 66.25, None)}, ("income", 1): {"2330": {"eps": 22.08}}}

    async def fake_fetch(kind, market, year_tw, quarter):
        calls.append(quarter)
        return tables.get((kind, quarter), {})

    monkeypatch.setattr(mqf, "_fetch", fake_fetch)
    result = asyncio.run(mqf.get_quarterly_financials("2330", date(2026, 5, 20)))
    assert result["data"] == result["data_ytd"]
    assert set(calls) == {1}


def test_previous_quarter_download_failure_is_upstream_error(monkeypatch):
    _patch_basic(monkeypatch)

    async def fake_fetch(kind, market, year_tw, quarter):
        if quarter == 1:
            return None
        return {"2330": _ytd(2404483.69, 67.03, None)} if kind == "operating" else {"2330": {"eps": 49.33}}

    monkeypatch.setattr(mqf, "_fetch", fake_fetch)
    result = asyncio.run(mqf.get_quarterly_financials("2330", date(2026, 7, 31)))
    assert result["found"] is False and result["reason"] == "upstream_error"


# --- I009: financial companies are only in t163sb04 (EPS), not t163sb06 ---

def test_financial_company_found_with_eps_only(monkeypatch):
    async def fake_basic():
        return {"2882": {"market": "上市"}}

    tables = {
        ("operating", 3): {"2330": _ytd(2762963.85, 59.0, None)},   # 2882 absent
        ("income", 3): {"2882": {"stock_id": "2882", "company_name": "國泰金", "eps": 7.1}},
        ("operating", 2): {},
        ("income", 2): {"2882": {"stock_id": "2882", "company_name": "國泰金", "eps": 4.6}},
    }
    monkeypatch.setattr(mqf, "load_basic_table", fake_basic)
    monkeypatch.setattr(mqf, "_fetch", _fake_tables(tables))
    result = asyncio.run(mqf.get_quarterly_financials("2882", date(2025, 9, 30)))
    assert result["found"] is True
    assert result["company_name"] == "國泰金"
    assert result["data"]["eps"] == 2.5
    assert result["data"]["revenue_millions"] is None and result["data"]["gross_margin_pct"] is None
