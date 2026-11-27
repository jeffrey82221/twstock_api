"""yfinance 財報 TTM：從 as_of 當天應已公告的最新一季起算、4 季連續（I016）。"""
import asyncio
from datetime import date

import app.service as svc


def _rows(quarters, eps=1.0):
    out = []
    for q in quarters:
        for t in ("EPS", "Revenue", "IncomeAfterTaxes", "OperatingIncome"):
            out.append({"date": q, "type": t, "value": eps if t == "EPS" else 100.0})
    return out


# Yahoo 窗口（2026-11）：2025Q3 ～ 2026Q3 共 5 季
WINDOW = ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]


def test_expected_quarter_follows_filing_deadline():
    assert svc._yf_expected_quarter(date(2026, 1, 1)) == "2025-09-30"
    assert svc._yf_expected_quarter(date(2026, 3, 30)) == "2025-09-30"   # Q4 due 03-31
    assert svc._yf_expected_quarter(date(2026, 3, 31)) == "2025-12-31"
    assert svc._yf_expected_quarter(date(2026, 8, 20)) == "2026-03-31"   # Q2 financial deadline 08-31
    assert svc._yf_expected_quarter(date(2026, 11, 27)) == "2026-09-30"


def test_anchored_ttm_full_window():
    qmap = svc._build_quarter_map(_rows(WINDOW))
    as_of = date(2026, 11, 27)
    exp = svc._yf_expected_quarter(as_of)
    ttm, qs = svc._yf_anchored_ttm(qmap, as_of, "EPS", exp)
    assert ttm == 4.0
    assert qs == ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31"]
    assert svc._yf_financials_miss_reason(qmap, exp, ttm) is None


def test_historical_as_of_outside_window_is_not_found():
    qmap = svc._build_quarter_map(_rows(WINDOW))
    as_of = date(2026, 1, 1)
    exp = svc._yf_expected_quarter(as_of)
    ttm, qs = svc._yf_anchored_ttm(qmap, as_of, "EPS", exp)
    assert ttm is None
    assert qs == ["2025-09-30"]   # 連續部分仍列出，供 financial_quarter_yfinance_list 攤 seed
    assert svc._yf_financials_miss_reason(qmap, exp, ttm) == svc.YF_MISS_OUTSIDE_WINDOW


def test_latest_quarter_not_on_yahoo_does_not_fall_back_to_older_quarters():
    # Yahoo 還沒放 2026Q3：舊邏輯會拿 2025Q3～2026Q2 冒充 TTM
    qmap = svc._build_quarter_map(_rows(WINDOW[:-1]))
    as_of = date(2026, 11, 27)
    exp = svc._yf_expected_quarter(as_of)
    ttm, qs = svc._yf_anchored_ttm(qmap, as_of, "EPS", exp)
    assert ttm is None and qs == []
    assert svc._yf_financials_miss_reason(qmap, exp, ttm) == svc.YF_MISS_NOT_YET_ON_YAHOO


def test_gap_in_quarters_breaks_ttm():
    qmap = svc._build_quarter_map(_rows([q for q in WINDOW if q != "2026-03-31"]))
    as_of = date(2026, 11, 27)
    ttm, qs = svc._yf_anchored_ttm(qmap, as_of, "EPS", svc._yf_expected_quarter(as_of))
    assert ttm is None and qs == ["2026-09-30", "2026-06-30"]


def test_query_financials_yfinance_end_to_end(monkeypatch):
    async def fake_basic(stock_id):
        return {"market": "sii"}

    async def fake_fs(stock_id, market):
        return _rows(WINDOW, eps=2.5)

    monkeypatch.setattr(svc, "_get_basic", fake_basic)
    monkeypatch.setattr(svc, "get_financial_statements_yf", fake_fs)

    r = asyncio.run(svc.query_financials_yfinance("2330", "2026-11-27"))
    assert r["found"] is True and r["reason"] is None
    assert r["expected_quarter"] == "2026-09-30"
    assert r["eps"]["ttm"] == 10.0
    assert r["operating_margin_pct"] == 100.0

    r = asyncio.run(svc.query_financials_yfinance("2330", "2026-01-01"))
    assert r["found"] is False and r["reason"] == svc.YF_MISS_OUTSIDE_WINDOW
    assert r["eps"]["ttm"] is None and r["eps"]["latest_quarter_value"] == 2.5


def test_query_financials_yfinance_not_covered(monkeypatch):
    async def fake_basic(stock_id):
        return None

    async def fake_fs(stock_id, market):
        return []

    monkeypatch.setattr(svc, "_get_basic", fake_basic)
    monkeypatch.setattr(svc, "get_financial_statements_yf", fake_fs)
    r = asyncio.run(svc.query_financials_yfinance("9999", "2026-11-27"))
    assert r["found"] is False and r["reason"] == svc.YF_MISS_NOT_COVERED
