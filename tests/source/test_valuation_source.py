"""app.valuation_source 純函式單元測試。

不依賴實網、不依賴 company_basic_info；用假 payload 直接驗證：
    1. TWSE payload parser 欄位對映
    2. 反推每股淨值 / 每股純益 / 每股股利公式
    3. 全市場加總 + 市值加權 PER / PBR / 殖利率
    4. 缺值 / 異常值排除規則
    5. 日期序列 helper

跑法（sandbox）：
    cd /path/to/twstock_api
    python -m pytest tests/test_valuation.py -v
"""
from __future__ import annotations

from datetime import date

import pytest

import app.valuation_source as vs
from app.valuation_source import (
    PAR_VALUE,
    _parse_bwibbu_payload,
    _parse_float,
    aggregate_market_summary,
    build_constituent_row,
    derive_per_share,
    iter_month_dates,
)


# =============================================================================
# _parse_float
# =============================================================================

class TestParseFloat:
    @pytest.mark.parametrize("raw,expected", [
        ("15.78", 15.78),
        ("1,234.56", 1234.56),
        (" 20.5 ", 20.5),
        (20.5, 20.5),
        (None, None),
        ("", None),
        ("-", None),
        ("--", None),
        ("N/A", None),
        ("abc", None),
    ])
    def test_parse_float(self, raw, expected):
        assert _parse_float(raw) == expected


# =============================================================================
# _parse_bwibbu_payload
# =============================================================================

class TestParseBwibbuPayload:
    """BWIBBU_d fields 順序：
    [代號, 名稱, 收盤價, 殖利率(%), 股利年度, 本益比, 股價淨值比, 財報年/季]
    """

    def test_parses_valid_payload(self):
        payload = {
            "stat": "OK",
            "data": [
                ["2330", "台積電", "1180.00", "1.36", 113, "24.03", "6.14", "114/1"],
                ["1101", "台泥", "24.15", "4.14", 113, "19.02", "0.73", "114/1"],
            ],
        }
        out = _parse_bwibbu_payload(payload)
        assert set(out.keys()) == {"2330", "1101"}
        r = out["2330"]
        assert r["stock_name"] == "台積電"
        assert r["close_price"] == 1180.0
        assert r["dividend_yield"] == 1.36
        assert r["dividend_year"] == "113"
        assert r["per"] == 24.03
        assert r["pbr"] == 6.14
        assert r["financial_report_period"] == "114/1"

    def test_empty_payload_returns_empty_dict(self):
        # 非交易日 TWSE 回 stat != OK
        assert _parse_bwibbu_payload({"stat": "very error", "data": []}) == {}
        assert _parse_bwibbu_payload({}) == {}
        assert _parse_bwibbu_payload({"stat": "OK"}) == {}

    def test_skips_malformed_rows(self):
        payload = {
            "stat": "OK",
            "data": [
                ["2330", "台積電", "1180", "1.36", 113, "24.03", "6.14", "114/1"],
                ["", "", "", "", "", "", "", ""],  # 空代號被略
                ["1101", "台泥"],  # 欄位不足被略
            ],
        }
        assert set(_parse_bwibbu_payload(payload).keys()) == {"2330"}

    def test_handles_missing_ratios(self):
        payload = {
            "stat": "OK",
            "data": [
                ["9999", "假想虧損股", "10.00", "0.00", 113, "-", "0.85", "114/1"],
            ],
        }
        r = _parse_bwibbu_payload(payload)["9999"]
        assert r["close_price"] == 10.0
        assert r["per"] is None
        assert r["dividend_yield"] == 0.0
        assert r["pbr"] == 0.85


# =============================================================================
# derive_per_share
# =============================================================================

class TestDerivePerShare:
    def test_full_valid(self):
        # close=100, PER=20, PBR=2, yield=3%
        # EPS = 100/20 = 5, BVPS = 100/2 = 50, DPS = 100*3/100 = 3
        r = derive_per_share(100.0, 20.0, 2.0, 3.0)
        assert r["eps"] == pytest.approx(5.0)
        assert r["bvps"] == pytest.approx(50.0)
        assert r["dps"] == pytest.approx(3.0)

    def test_zero_yield_is_valid(self):
        # yield=0 表示不配息但仍算入市值加權殖利率分母 → DPS=0 有效
        r = derive_per_share(100.0, 20.0, 2.0, 0.0)
        assert r["dps"] == 0.0

    def test_negative_per_excluded(self):
        r = derive_per_share(100.0, -5.0, 2.0, 1.0)
        assert r["eps"] is None  # PER<=0 不成立
        assert r["bvps"] == pytest.approx(50.0)

    def test_zero_pbr_excluded(self):
        r = derive_per_share(100.0, 20.0, 0.0, 1.0)
        assert r["bvps"] is None
        assert r["eps"] == pytest.approx(5.0)

    def test_missing_close_excludes_all(self):
        assert derive_per_share(None, 20.0, 2.0, 3.0) == {"eps": None, "bvps": None, "dps": None}
        assert derive_per_share(0.0, 20.0, 2.0, 3.0) == {"eps": None, "bvps": None, "dps": None}

    def test_missing_per_pbr_yield(self):
        r = derive_per_share(100.0, None, None, None)
        assert r == {"eps": None, "bvps": None, "dps": None}


# =============================================================================
# aggregate_market_summary
# =============================================================================

class TestAggregateMarketSummary:
    """假資料驗證公式：
       * A 全欄位有效，PER=10, PBR=2, yield=5%, close=100, 資本額=1000元 → 100股
       * B 缺 PER (虧損)，PBR=1.5, yield=0, close=50, 資本額=500元 → 50股
       * C 缺 paid_in_capital → 完全排除
       * D 缺 close → 完全排除

       手算：
         A: shares=100, mv=100*100=10000, eps=100/10=10, bvps=100/2=50, dps=100*5/100=5
            → contribute per=+10000/1000=10, pbr=+10000/5000=2, yield=+500/10000=5%
         B: shares=50,  mv=50*50=2500,    eps=None,     bvps=50/1.5=33.33, dps=0
            → PER 分子分母皆 skip；PBR 分子 +2500, 分母 +50*33.33=1666.67；yield 分子 +0, 分母 +2500
    """

    @pytest.fixture
    def fixture_payload(self):
        return {
            "A": {"close_price": 100.0, "per": 10.0, "pbr": 2.0, "dividend_yield": 5.0, "stock_name": "A股"},
            "B": {"close_price": 50.0, "per": None, "pbr": 1.5, "dividend_yield": 0.0, "stock_name": "B股"},
            "C": {"close_price": 30.0, "per": 15.0, "pbr": 1.0, "dividend_yield": 2.0, "stock_name": "C股"},
            "D": {"close_price": None, "per": 20.0, "pbr": 2.0, "dividend_yield": 1.0, "stock_name": "D股"},
        }

    @pytest.fixture
    def fixture_basic(self):
        return {
            "A": {"paid_in_capital": 1000, "short_name": "A公司"},
            "B": {"paid_in_capital": 500, "short_name": "B公司"},
            # C 沒有 paid_in_capital → excluded_no_shares
            "D": {"paid_in_capital": 400, "short_name": "D公司"},
        }

    def test_counts(self, fixture_payload, fixture_basic):
        agg = aggregate_market_summary(fixture_payload, fixture_basic, sample_size=10)
        assert agg["total_rows"] == 4
        assert agg["constituent_count"] == 2  # 只 A + B
        assert agg["excluded_count"] == 2
        assert agg["excluded_no_price"] == 1  # D
        assert agg["excluded_no_shares"] == 1  # C
        assert agg["per_included"] == 1  # 只 A
        assert agg["pbr_included"] == 2  # A + B
        assert agg["yield_included"] == 2  # A + B

    def test_totals(self, fixture_payload, fixture_basic):
        # shares_A = 1000 / PAR_VALUE
        # shares_B = 500 / PAR_VALUE
        shares_A = 1000 / PAR_VALUE
        shares_B = 500 / PAR_VALUE
        mv_A = 100.0 * shares_A
        mv_B = 50.0 * shares_B

        agg = aggregate_market_summary(fixture_payload, fixture_basic)

        assert agg["total_market_cap"] == pytest.approx(mv_A + mv_B)
        assert agg["total_market_cap_per_basis"] == pytest.approx(mv_A)  # 只 A
        assert agg["total_market_cap_pbr_basis"] == pytest.approx(mv_A + mv_B)
        assert agg["total_market_cap_yield_basis"] == pytest.approx(mv_A + mv_B)

        # 純益：只 A: EPS=10, shares=100 → 1000
        assert agg["total_net_income"] == pytest.approx(10.0 * shares_A)
        # 淨值：A: BVPS=50 * 100 = 5000; B: BVPS=50/1.5 * 50 = 1666.67
        expected_bv = 50.0 * shares_A + (50.0 / 1.5) * shares_B
        assert agg["total_book_value"] == pytest.approx(expected_bv)
        # 股利：A: DPS=5 * 100 = 500; B: DPS=0 * 50 = 0
        assert agg["total_cash_dividend"] == pytest.approx(5.0 * shares_A)

    def test_ratios(self, fixture_payload, fixture_basic):
        agg = aggregate_market_summary(fixture_payload, fixture_basic)
        # PER = 10000 / 1000 = 10
        assert agg["market_per"] == pytest.approx(10.0)
        # PBR = 12500 / (5000 + 1666.67) = 12500 / 6666.67 ≈ 1.875
        assert agg["market_pbr"] == pytest.approx(12500 / (50.0 * 100 + (50.0 / 1.5) * 50))
        # yield = 500 / 12500 * 100 = 4.0%
        assert agg["market_dividend_yield_pct"] == pytest.approx(500.0 / 12500.0 * 100.0)

    def test_sample_size_limit(self, fixture_payload, fixture_basic):
        agg = aggregate_market_summary(fixture_payload, fixture_basic, sample_size=1)
        assert len(agg["sample_constituents"]) == 1

    def test_sample_zero(self, fixture_payload, fixture_basic):
        agg = aggregate_market_summary(fixture_payload, fixture_basic, sample_size=0)
        assert agg["sample_constituents"] == []

    def test_all_excluded_returns_none_ratios(self):
        # 全部無 close/shares → 分母全 0 → ratios 為 None
        payload = {"X": {"close_price": None, "per": 10.0, "pbr": 2.0, "dividend_yield": 3.0}}
        agg = aggregate_market_summary(payload, {}, sample_size=5)
        assert agg["market_per"] is None
        assert agg["market_pbr"] is None
        assert agg["market_dividend_yield_pct"] is None
        assert agg["constituent_count"] == 0

    def test_stock_name_prefers_basic_short_name(self, fixture_payload, fixture_basic):
        agg = aggregate_market_summary(fixture_payload, fixture_basic, sample_size=10)
        names = {c["stk_code"]: c["stock_name"] for c in agg["sample_constituents"]}
        assert names["A"] == "A公司"  # 用 basic 而非 payload 的 "A股"


# =============================================================================
# iter_month_dates
# =============================================================================

class TestIterMonthDates:
    def test_february_leap_year(self):
        dates = list(iter_month_dates(2024, 2))
        assert dates[0] == date(2024, 2, 1)
        assert dates[-1] == date(2024, 2, 29)  # 2024 閏年
        assert len(dates) == 29

    def test_january(self):
        dates = list(iter_month_dates(2025, 1))
        assert len(dates) == 31
        assert dates[0] == date(2025, 1, 1)
        assert dates[-1] == date(2025, 1, 31)

    def test_invalid_month(self):
        with pytest.raises(ValueError):
            list(iter_month_dates(2025, 13))

    def test_invalid_year(self):
        with pytest.raises(ValueError):
            list(iter_month_dates(1999, 1))


# =============================================================================
# build_constituent_row（單股共用建構）
# =============================================================================

class TestBuildConstituentRow:
    def _basic(self, pic=10_000_000_000, name="台積電"):
        return {"paid_in_capital": pic, "short_name": name}

    def _row(self, close=1180.0, per=27.5, pbr=6.8, yld=1.4, name=None):
        return {
            "close_price": close, "per": per, "pbr": pbr,
            "dividend_yield": yld,
            **({"stock_name": name} if name else {}),
        }

    def test_full_valid(self):
        c, r = build_constituent_row("2330", self._row(), self._basic())
        assert r is None
        assert c is not None
        assert c["stk_code"] == "2330"
        assert c["stock_name"] == "台積電"
        assert c["close_price"] == 1180.0
        assert c["estimated_shares"] == 10_000_000_000 / PAR_VALUE
        assert c["market_cap"] == pytest.approx(1180.0 * 10_000_000_000 / PAR_VALUE)
        assert c["eps_ttm"] == pytest.approx(1180.0 / 27.5)
        assert c["bvps"] == pytest.approx(1180.0 / 6.8)
        assert c["dps"] == pytest.approx(1180.0 * 1.4 / 100.0)
        assert c["included_in_per"] is True
        assert c["included_in_pbr"] is True
        assert c["included_in_yield"] is True

    def test_missing_price(self):
        c, r = build_constituent_row("2330", self._row(close=None), self._basic())
        assert c is None
        assert r == "no_price"

    def test_zero_price(self):
        c, r = build_constituent_row("2330", self._row(close=0), self._basic())
        assert c is None
        assert r == "no_price"

    def test_missing_shares(self):
        c, r = build_constituent_row("2330", self._row(), {"paid_in_capital": None})
        assert c is None
        assert r == "no_shares"

    def test_missing_basic_entry_entirely(self):
        c, r = build_constituent_row("2330", self._row(), None)
        assert c is None
        assert r == "no_shares"

    def test_partial_ratios_flags(self):
        # PER 缺（虧損公司）→ EPS None、included_in_per False，PBR/yield 仍 True
        c, r = build_constituent_row("9999", self._row(per=None), self._basic(name="虧損股"))
        assert r is None
        assert c["eps_ttm"] is None
        assert c["included_in_per"] is False
        assert c["included_in_pbr"] is True
        assert c["included_in_yield"] is True

    def test_name_fallback_to_payload(self):
        c, r = build_constituent_row(
            "2330",
            self._row(name="TSMC-payload"),
            {"paid_in_capital": 10_000_000_000, "short_name": ""},
        )
        assert r is None
        assert c["stock_name"] == "TSMC-payload"

    def test_zero_yield_still_included(self):
        c, r = build_constituent_row("1234", self._row(yld=0.0), self._basic(name="無配息"))
        assert r is None
        assert c["dps"] == 0.0
        assert c["included_in_yield"] is True


# =============================================================================
# get_company_valuation（by-company 對外主函式）
# =============================================================================

class TestGetCompanyValuation:
    """用 monkeypatch stub 上游 fetcher 與 basic loader，測所有 branch。"""

    D = date(2025, 8, 1)

    _fake_payload = {
        "2330": {
            "stk_code": "2330",
            "stock_name": "台積電",
            "close_price": 1180.0,
            "per": 27.5, "pbr": 6.8,
            "dividend_yield": 1.4,
        },
        "1101": {
            "stk_code": "1101",
            "stock_name": "台泥",
            "close_price": 24.15,
            "per": 19.02, "pbr": 0.73,
            "dividend_yield": 4.14,
        },
        "9999": {
            "stk_code": "9999",
            "stock_name": "虧損股",
            "close_price": 12.0,
            "per": None,        # 虧損
            "pbr": 1.2,
            "dividend_yield": 0.0,
        },
        "1234": {
            "stk_code": "1234",
            "stock_name": "缺價股",
            "close_price": None,
            "per": 10.0, "pbr": 1.0, "dividend_yield": 3.0,
        },
    }

    _fake_basic = {
        "2330": {"paid_in_capital": 259_303_804_580, "short_name": "台積電"},
        "1101": {"paid_in_capital": 77_231_817_420, "short_name": "台泥"},
        "9999": {"paid_in_capital": 5_000_000_000, "short_name": "虧損股"},
        "1234": {"paid_in_capital": 1_000_000_000, "short_name": "缺價股"},
        "5555": {"paid_in_capital": None, "short_name": "缺股數股"},
    }

    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        async def _fake_fetch(d):
            return dict(self._fake_payload)

        async def _fake_basic_loader():
            return dict(self._fake_basic)

        monkeypatch.setattr(vs, "_fetch_twse_bwibbu_day", _fake_fetch)
        monkeypatch.setattr(vs, "load_basic_table", _fake_basic_loader)

    @pytest.mark.asyncio
    async def test_found_full(self):
        r = await vs.get_company_valuation("2330", self.D)
        assert r["found"] is True
        assert r["stock_id"] == "2330"
        assert r["reason"] is None
        assert r["date"] == "2025-08-01"
        assert r["calculation_method"].startswith("estimated_market_cap_weighted")
        c = r["constituent"]
        assert c["stk_code"] == "2330"
        assert c["stock_name"] == "台積電"
        assert c["close_price"] == 1180.0
        assert c["eps_ttm"] == pytest.approx(1180.0 / 27.5)
        assert c["included_in_per"] is True

    @pytest.mark.asyncio
    async def test_stock_id_trimmed(self):
        r = await vs.get_company_valuation("  2330  ", self.D)
        assert r["found"] is True
        assert r["stock_id"] == "2330"

    @pytest.mark.asyncio
    async def test_empty_stock_id(self):
        r = await vs.get_company_valuation("   ", self.D)
        assert r["found"] is False
        assert r["reason"] == "stock_id_not_listed"
        assert r["constituent"] is None

    @pytest.mark.asyncio
    async def test_stock_id_not_listed(self):
        r = await vs.get_company_valuation("0000", self.D)
        assert r["found"] is False
        assert r["reason"] == "stock_id_not_listed"
        assert r["constituent"] is None

    @pytest.mark.asyncio
    async def test_no_market_data(self, monkeypatch):
        async def _empty(d):
            return {}
        monkeypatch.setattr(vs, "_fetch_twse_bwibbu_day", _empty)
        r = await vs.get_company_valuation("2330", self.D)
        assert r["found"] is False
        assert r["reason"] == "no_market_data"
        assert r["constituent"] is None

    @pytest.mark.asyncio
    async def test_no_price_reason(self):
        r = await vs.get_company_valuation("1234", self.D)
        assert r["found"] is False
        assert r["reason"] == "no_price"
        assert r["constituent"] is None

    @pytest.mark.asyncio
    async def test_no_shares_reason(self):
        # 5555 在 basic 有但 paid_in_capital=None，且不在 payload → 先 not_listed
        # 這裡改用 9999 但把它的 basic 拿掉來測 no_shares
        r = await vs.get_company_valuation("5555", self.D)
        # 5555 不在 payload
        assert r["found"] is False
        assert r["reason"] == "stock_id_not_listed"

    @pytest.mark.asyncio
    async def test_no_shares_when_basic_missing_pic(self, monkeypatch):
        # 讓 payload 有這檔但 basic pic=None
        payload = dict(self._fake_payload)
        payload["8888"] = {
            "stk_code": "8888", "stock_name": "PIC 缺股",
            "close_price": 50.0, "per": 10.0, "pbr": 1.5, "dividend_yield": 2.0,
        }
        basic = dict(self._fake_basic)
        basic["8888"] = {"paid_in_capital": None, "short_name": "PIC 缺股"}

        async def _fake_fetch(d):
            return payload

        async def _fake_basic_loader():
            return basic

        monkeypatch.setattr(vs, "_fetch_twse_bwibbu_day", _fake_fetch)
        monkeypatch.setattr(vs, "load_basic_table", _fake_basic_loader)

        r = await vs.get_company_valuation("8888", self.D)
        assert r["found"] is False
        assert r["reason"] == "no_shares"

    @pytest.mark.asyncio
    async def test_partial_ratios_still_found(self):
        # 9999 虧損但 close/shares 齊 → found=True，included_in_per=False
        r = await vs.get_company_valuation("9999", self.D)
        assert r["found"] is True
        c = r["constituent"]
        assert c["eps_ttm"] is None
        assert c["included_in_per"] is False
        assert c["included_in_pbr"] is True
        assert c["included_in_yield"] is True  # yield=0 也計入

    @pytest.mark.asyncio
    async def test_constituent_matches_market_sample(self):
        """同一天同一檔：by-company 的 constituent 與 by-market sample 對應 row 完全一致。"""
        by_company = await vs.get_company_valuation("2330", self.D)
        # aggregate 用同一份 payload / basic
        agg = aggregate_market_summary(self._fake_payload, self._fake_basic, sample_size=50)
        matched = [c for c in agg["sample_constituents"] if c["stk_code"] == "2330"]
        assert len(matched) == 1
        m = matched[0]
        c = by_company["constituent"]
        # 檢查關鍵欄位一致
        for k in ("close_price", "per", "pbr", "dividend_yield_pct",
                  "estimated_shares", "market_cap", "eps_ttm", "bvps", "dps",
                  "included_in_per", "included_in_pbr", "included_in_yield"):
            assert c[k] == m[k], f"mismatch on {k}"
