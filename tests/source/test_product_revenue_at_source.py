"""get_product_revenue_at: an upstream failure must not be reported (or cached) as "never filed"."""
import asyncio

import pytest

import app.sources as src


@pytest.fixture(autouse=True)
def clear_caches():
    src._last_filed_cache.clear()
    src._last_filed_notes.clear()
    src._product_revenue_at_cache.clear()
    yield
    src._last_filed_cache.clear()
    src._last_filed_notes.clear()
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


# ---- MOPS t05st08 page layouts (2026-11-26) ----

_EXEMPT_HTML = "<html><body><h3>公開資訊觀測站</h3><center>外國發行人免申報本項資訊</center></body></html>"

_ZERO_REVENUE_HTML = """
<span style='color:blue;'>(上櫃公司) 坤悅</span>
<TABLE><TR><TD>民國</TD> <TD>111</TD> <TD>年</TD> <TD>03</TD> <TD>月</TD></TR></TABLE>
<TABLE class='hasBorder'>
<TR class='odd'><TD>(1)</TD><TD>&nbsp;營建</TD><TD>&nbsp;</TD></TR>
<TR class='even'><TD>(2)</TD><TD>&nbsp;</TD><TD>&nbsp;</TD></TR>
<TR class='odd'><TD>減</TD><TD>銷貨退回及折讓</TD><TD>&nbsp;0</TD></TR>
<TR class='even'><TD>合計</TD><TD>業務營收淨額</TD><TD>&nbsp;0</TD></TR>
</TABLE>"""


def test_foreign_issuer_exempt_page_is_explicit_no_data():
    parsed = src._parse_mops_table(_EXEMPT_HTML)
    assert parsed["found"] is False and parsed["no_data"] is True
    assert "外國發行人免申報" in parsed["notes"]


def test_zero_revenue_filing_is_found():
    parsed = src._parse_mops_table(_ZERO_REVENUE_HTML)
    assert parsed["found"] is True
    assert parsed["total_revenue"] == 0 and parsed["sales_return"] == 0
    assert parsed["year"] == "111" and parsed["month"] == "03"
    assert parsed["items"] == []      # 金額空白的項目不列


def test_error_page_is_not_no_data():
    parsed = src._parse_mops_table("<html><body>系統忙碌中，請稍後再試</body></html>")
    assert parsed["found"] is False and not parsed.get("no_data")


def test_exempt_detail_page_is_cached_without_source_error(monkeypatch):
    # 2637（KY）在全市場申報名單裡，但明細頁回「免申報」：合法無資料，不是上游失敗
    async def last_filed(stock_id):
        return (2026, 10)

    async def filers(client, ym, typek):
        return {"2637"}, {"year": "115", "month": "10", "TYPEK": typek}, None

    async def detail(client, stock_id, form_fields, referer):
        return src._parse_mops_table(_EXEMPT_HTML)

    bad = []
    monkeypatch.setattr(src, "_get_last_filed_ym", last_filed)
    monkeypatch.setattr(src, "_fetch_filer_list", filers)
    monkeypatch.setattr(src, "_fetch_detail_at", detail)
    monkeypatch.setattr(src, "record_bad_payload", lambda *a, **k: bad.append(a))
    out = asyncio.run(src.get_product_revenue_at("2637", 2026, 10))
    assert out["found"] is False and out["error"] is None
    assert "免申報" in out["notes"] and "no_data" not in out
    assert bad == []
    assert "2637@11510" in src._product_revenue_at_cache


def test_foreign_issuer_note_reaches_as_of_lookup(monkeypatch):
    async def exempt(stock_id):
        return {"found": False, "error": None, "notes": src._MOPS_T05ST08_NO_DATA["外國發行人免申報"]}

    monkeypatch.setattr(src, "get_product_revenue", exempt)
    out = asyncio.run(src.get_product_revenue_at("2637", 2026, 10))
    assert out["found"] is False and out["error"] is None
    assert out["notes"] == "外國發行人免申報各項產品業務營收。"
