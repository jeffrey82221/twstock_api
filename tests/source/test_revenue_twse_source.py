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


# --- I002: point-in-time -- a month counts only from its filing deadline (10th of next month) ---

def _patch_revenue(monkeypatch, months):
    """months: {(year, month): revenue_in_yuan}; MOPS history honours the base month like the real one."""
    import calendar
    calls = {}

    async def fake_basic(stock_id):
        return {"market": "上市"}

    async def no_t187ap05(market_code):
        return []

    async def fake_history(stock_id, market_code, as_of_year, as_of_month, months=26):
        calls["base"] = (as_of_year, as_of_month)
        rows, y, m = [], as_of_year, as_of_month - 1
        for _ in range(months):
            if m == 0:
                y, m = y - 1, 12
            if (y, m) in months_data:
                rows.append({"date": f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}",
                             "revenue_year": y, "revenue_month": m, "revenue": months_data[(y, m)]})
            m -= 1
        return rows

    months_data = months
    import app.sources as sources
    monkeypatch.setattr(service, "_get_basic", fake_basic)
    monkeypatch.setattr(service, "get_twse_monthly_revenue_all", no_t187ap05)
    monkeypatch.setattr(sources, "get_mops_revenue_history", fake_history)
    return calls


def test_revenue_twse_hides_month_before_its_deadline(monkeypatch):
    calls = _patch_revenue(monkeypatch, {(2026, 9): 300, (2026, 10): 400})
    result = asyncio.run(service.query_revenue_twse("2330", "2026-11-01"))
    assert result["latest_month_label"] == "2026/09"      # October revenue is due 11-10
    assert result["data_date"] == "2026-09-30"
    assert result["available_date"] == "2026-10-10"
    assert calls["base"] == (2026, 10)                   # history starts at September


def test_revenue_twse_shows_month_on_its_deadline(monkeypatch):
    _patch_revenue(monkeypatch, {(2026, 9): 300, (2026, 10): 400})
    result = asyncio.run(service.query_revenue_twse("2330", "2026-11-10"))
    assert result["latest_month_label"] == "2026/10"
    assert result["available_date"] == "2026-11-10"


def test_mops_revenue_parser_accepts_pre_2013_ten_column_rows():
    """Pre-IFRS t21sc03 pages (2010-01..2012-12) have 10 columns (no 備註); requiring 11
    turned every one of them into "no revenue"."""
    from app.sources import _parse_mops_revenue_html

    def page(cells_per_row):
        rows = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in cells_per_row)
        return f"<html><table>{rows}</table></html>"

    old = ["1103", "嘉泥", "112,508", "94,668", "84,001", "18.84", "33.93", "1,284,176", "877,339", "46.37"]
    new = ["1103", "嘉泥", "288,455", "不適用", "355,452", "不適用", "-18.84", "288,455", "355,452", "-18.84", "-"]
    assert _parse_mops_revenue_html(page([old]))["1103"]["當月營收"] == "112,508"
    assert _parse_mops_revenue_html(page([old]))["1103"]["備註"] == ""
    assert _parse_mops_revenue_html(page([new]))["1103"]["備註"] == "-"
