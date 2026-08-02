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

from app.valuation_source import (
    PAR_VALUE,
    _parse_bwibbu_payload,
    _parse_float,
    aggregate_market_summary,
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
