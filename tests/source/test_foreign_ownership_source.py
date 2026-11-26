from __future__ import annotations

import asyncio
from datetime import date, timedelta

import app.foreign_ownership_source as fos


def _csv(*rows: str) -> bytes:
    return ("\n".join(rows) + "\n").encode("big5")


class TestParseMiQfiisCsv:
    def test_parses_rows_and_roc_date(self):
        content = _csv(
            '"115年09月18日 外資及陸資投資持股統計"',
            '"證券代號","證券名稱","國際證券編碼","發行股數","外資及陸資尚可投資股數","全體外資及陸資持有股數","外資及陸資尚可投資比率","全體外資及陸資持股比率","外資及陸資共用法令投資上限比率","陸資法令投資上限比率","與前日異動原因(註)","最近一次上市公司申報外資及陸資持股異動日期"',
            '="2330","台積電","TW0002330008","10,000","7,000","3,000","70.00","30.00","40.00","40.00","","115/09/01"',
            '"說明：本資料僅供參考"',
        )
        result = fos.parse_mi_qfiis_csv(content, date(2026, 9, 18))

        assert set(result) == {"2330"}
        assert result["2330"] == {
            "trade_date": "2026-09-18",
            "stock_id": "2330",
            "stock_name": "台積電",
            "issued_shares": 10000.0,
            "foreign_investment_available_shares": 7000.0,
            "foreign_investment_shares": 3000.0,
            "foreign_investment_available_ratio_pct": 70.0,
            "foreign_investment_ratio_pct": 30.0,
            "foreign_investment_limit_ratio_pct": 40.0,
            "china_investment_limit_ratio_pct": 40.0,
            "change_reason": "",
            "last_change_date": "2026-09-01",
        }

    def test_invalid_payload_returns_empty(self):
        assert fos.parse_mi_qfiis_csv(b"not a csv", date(2026, 9, 18)) == {}


def test_get_foreign_ownership_returns_single_day_row(monkeypatch, tmp_path):
    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)
    payload = {"2330": {"trade_date": "2026-09-18", "stock_id": "2330"}, "1101": {"trade_date": "2026-09-18", "stock_id": "1101"}}

    async def fake_fetch(report_date):
        assert report_date == date(2026, 9, 18)
        return payload

    monkeypatch.setattr(fos, "_fetch_day", fake_fetch)
    row = asyncio.run(fos.get_foreign_ownership("2330", date(2026, 9, 18)))

    assert row == payload["2330"]


def test_get_foreign_ownership_falls_back_to_previous_available_day(monkeypatch, tmp_path):
    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)
    requested_dates = []
    previous_day = date(2026, 9, 18)

    async def fake_fetch(report_date):
        requested_dates.append(report_date)
        if report_date == previous_day:
            return {"2330": {"trade_date": previous_day.isoformat(), "stock_id": "2330"}}
        return {}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(fos, "_fetch_day", fake_fetch)
    monkeypatch.setattr(fos.asyncio, "sleep", no_sleep)
    row = asyncio.run(fos.get_foreign_ownership("2330", date(2026, 9, 20)))

    assert requested_dates == [date(2026, 9, 20), date(2026, 9, 19), previous_day]
    assert row["trade_date"] == "2026-09-18"

def _no_sleep_fetch(monkeypatch, tmp_path, responder):
    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)
    requested = []

    async def fake_fetch(report_date):
        requested.append(report_date)
        return responder(report_date)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(fos, "_fetch_day", fake_fetch)
    monkeypatch.setattr(fos.asyncio, "sleep", no_sleep)
    return requested


def test_lookback_is_bounded_by_max_lookback_days(monkeypatch, tmp_path):
    # every day is a non-trading day → must stop after max_lookback_days, not walk to MIN_DATE
    requested = _no_sleep_fetch(monkeypatch, tmp_path, lambda d: {})
    row = asyncio.run(fos.get_foreign_ownership("2330", date(2008, 1, 5)))
    assert row is None
    assert len(requested) == fos.MAX_LOOKBACK_DAYS + 1
    assert min(requested) == date(2008, 1, 5) - timedelta(days=fos.MAX_LOOKBACK_DAYS)


def test_lookback_never_goes_below_min_date(monkeypatch, tmp_path):
    requested = _no_sleep_fetch(monkeypatch, tmp_path, lambda d: {})
    asyncio.run(fos.get_foreign_ownership("2330", fos.MIN_DATE + timedelta(days=3)))
    assert min(requested) == fos.MIN_DATE


def test_trading_day_without_the_stock_stops_immediately(monkeypatch, tmp_path):
    # MI_QFIIS lists every listed stock on a trading day; absence means "not covered" (e.g. 上櫃)
    requested = _no_sleep_fetch(monkeypatch, tmp_path, lambda d: {"1101": {"trade_date": d.isoformat()}})
    row = asyncio.run(fos.get_foreign_ownership("5483", date(2024, 3, 5)))
    assert row is None
    assert requested == [date(2024, 3, 5)]


def test_custom_lookback_can_be_widened(monkeypatch, tmp_path):
    target = date(2024, 1, 1)
    requested = _no_sleep_fetch(
        monkeypatch, tmp_path,
        lambda d: {"2330": {"trade_date": d.isoformat()}} if d == target else {},
    )
    assert asyncio.run(fos.get_foreign_ownership("2330", date(2024, 3, 5))) is None
    requested.clear()
    row = asyncio.run(fos.get_foreign_ownership("2330", date(2024, 3, 5), max_lookback_days=90))
    assert row["trade_date"] == "2024-01-01"


def _blocked_response(url: str):
    import httpx

    request = httpx.Request("GET", url)
    return httpx.Response(307, request=request, text="<html>TWSE block page</html>")


def test_fetch_day_upstream_block_raises_and_is_rate_limited(monkeypatch, tmp_path):
    # TWSE's WAF answers blocked IPs with a 307 + HTML block page (no followable Location).
    # That must surface as an upstream error, not as an empty "non-trading day" payload.
    import httpx
    import pytest

    from app.sources import UpstreamFetchError, _request_source_errors

    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)

    async def fake_get(self, url, **kwargs):
        return _blocked_response(url)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def run():
        _request_source_errors.set([])
        with pytest.raises(UpstreamFetchError):
            await fos._fetch_day(date(2026, 8, 28))
        return list(_request_source_errors.get())

    errors = asyncio.run(run())
    assert len(errors) == 1 and errors[0].status_code == 307 and errors[0].is_rate_limited
    assert not list(tmp_path.iterdir())  # failure is not cached


def test_upstream_error_stops_lookback(monkeypatch, tmp_path):
    import pytest

    from app.sources import UpstreamFetchError

    def responder(d):
        raise UpstreamFetchError("blocked")

    requested = _no_sleep_fetch(monkeypatch, tmp_path, responder)
    with pytest.raises(UpstreamFetchError):
        asyncio.run(fos.get_foreign_ownership("2330", date(2026, 8, 31)))
    assert requested == [date(2026, 8, 31)]  # no day-by-day hammering of a blocking upstream


# --- I010: a 200 response without the CSV header is a block page, not a holiday ---

def test_fetch_day_without_header_raises_and_is_not_cached(monkeypatch, tmp_path):
    import httpx
    import pytest

    from app.sources import UpstreamFetchError

    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url), text="<html>var contactPhone</html>")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(UpstreamFetchError):
        asyncio.run(fos._fetch_day(date(2017, 6, 5)))
    assert not list(tmp_path.rglob("*.json.gz"))


def test_holiday_csv_with_header_only_is_cached_empty(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)
    holiday = '"113年04月04日 外資及陸資投資持股統計"\r\n"證券代號","證券名稱","國際證券編碼"\r\n'.encode("big5")

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url), content=holiday)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert asyncio.run(fos._fetch_day(date(2024, 4, 4))) == {}
    assert fos._cache_read(date(2024, 4, 4)) == {}


# --- I006: OTC stocks use TPEx qfii ---

TPEX_QFII = {
    "stat": "ok", "date": "20240403",
    "tables": [{"date": "113/04/03", "data": [
        ["1", "8455", "大拓-KY", "25,219,056", "3,081,750", "22,137,306", "12.22%", "87.78%", "100%", ""],
        ["5", "6488", "環球晶", "478,450,000", "357,000,000", "121,450,000", "74.62%", "25.38%", "100%", "僑外資持股"],
    ]}],
}


def test_parse_tpex_qfii_payload_maps_to_mi_qfiis_row():
    rows = fos.parse_tpex_qfii_payload(TPEX_QFII, date(2024, 4, 3))
    assert rows["6488"] == {
        "trade_date": "2024-04-03", "stock_id": "6488", "stock_name": "環球晶",
        "issued_shares": 478450000.0, "foreign_investment_available_shares": 357000000.0,
        "foreign_investment_shares": 121450000.0, "foreign_investment_available_ratio_pct": 74.62,
        "foreign_investment_ratio_pct": 25.38, "foreign_investment_limit_ratio_pct": 100.0,
        "china_investment_limit_ratio_pct": None, "change_reason": "僑外資持股", "last_change_date": None,
    }
    assert rows["8455"]["change_reason"] is None


def test_otc_stock_uses_tpex_and_caches_separately(monkeypatch, tmp_path):
    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)

    async def fake_http_get(url, **kwargs):
        assert "tpex.org.tw" in url and "2024/04/03" in url
        return TPEX_QFII

    async def twse_must_not_be_called(d):
        raise AssertionError("OTC stock must not use MI_QFIIS")

    monkeypatch.setattr(fos, "_http_get", fake_http_get)
    monkeypatch.setattr(fos, "_fetch_day", twse_must_not_be_called)
    row = asyncio.run(fos.get_foreign_ownership("6488", date(2024, 4, 3), market="上櫃"))
    assert row["foreign_investment_ratio_pct"] == 25.38
    assert fos._cache_read(date(2024, 4, 3), "tpex")["6488"] == row
    assert fos._cache_read(date(2024, 4, 3)) is None          # TWSE cache untouched


def test_tpex_payload_for_another_date_raises(monkeypatch, tmp_path):
    import pytest

    from app.sources import UpstreamFetchError

    monkeypatch.setattr(fos, "CACHE_ROOT", tmp_path)

    async def fake_http_get(url, **kwargs):
        return {**TPEX_QFII, "date": "20261125", "tables": [{"date": "115/11/25", "data": []}]}

    monkeypatch.setattr(fos, "_http_get", fake_http_get)
    with pytest.raises(UpstreamFetchError):
        asyncio.run(fos._fetch_tpex_day(date(2024, 4, 3)))
    assert not list(tmp_path.rglob("*.json.gz"))


def test_parses_pre_2009_layout_by_header_name():
    """I010: the 2004-2009 CSV has 11 columns (no mainland limit). Fixed positions read the
    change-reason code as the mainland limit and the change date as the reason."""
    content = _csv(
        '"93年02月11日 外資投資持股統計"',
        '"證券代號","證券名稱","國際證券編碼","發行股數","外資尚可投資股數","全體外資持有股數","外資尚可投資比率","全體外資持股比率","法令投資上限比率","與前日異動原因(註)","最近一次上市公司申報外資持股異動日期",',
        '"2330","台積電","TW0002330008","20,266,618,984","9,226,391,259","11,040,227,725","45.52","54.47","0.00","1","92/08/18",',
    )
    row = fos.parse_mi_qfiis_csv(content, date(2004, 2, 11))["2330"]
    assert row["foreign_investment_ratio_pct"] == 54.47
    assert row["foreign_investment_limit_ratio_pct"] == 0.0
    assert row["china_investment_limit_ratio_pct"] is None
    assert row["change_reason"] == "1"
    assert row["last_change_date"] == "2003-08-18"
