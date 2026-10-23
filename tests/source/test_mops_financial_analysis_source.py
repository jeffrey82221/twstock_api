"""Tests for `app.mops_financial_analysis`.

覆蓋：
  * `_parse_float` — 千分位 / 空白 / N/A / 全形破折 / 不適用
  * `parse_t51sb02_html` — 21 欄完整列 / 標頭略過 / 金融業空欄 / 欄位不足略過
  * `get_annual_financial_analysis` — sii found / otc fallback / not_listed /
    no_market_data / stock_id trim
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

import app.mops_financial_analysis as mfa
from app.mops_financial_analysis import (
    _parse_float,
    parse_t51sb02_html,
)


# =============================================================================
# _parse_float
# =============================================================================

class TestParseFloat:
    def test_normal_number(self):
        assert _parse_float("12.34") == 12.34

    def test_negative_number(self):
        assert _parse_float("-5.67") == -5.67

    def test_comma_number(self):
        assert _parse_float("1,234.56") == 1234.56

    def test_empty_returns_none(self):
        assert _parse_float("") is None

    def test_whitespace_returns_none(self):
        assert _parse_float("   ") is None

    def test_full_width_space(self):
        assert _parse_float("\u30001.5") == 1.5

    def test_dash_returns_none(self):
        assert _parse_float("-") is None

    def test_double_dash_returns_none(self):
        assert _parse_float("--") is None

    def test_full_width_dash_returns_none(self):
        assert _parse_float("－") is None

    def test_em_dash_returns_none(self):
        assert _parse_float("—") is None

    def test_na_returns_none(self):
        assert _parse_float("N/A") is None

    def test_not_applicable_returns_none(self):
        assert _parse_float("不適用") is None

    def test_none_input(self):
        assert _parse_float(None) is None

    def test_garbage_returns_none(self):
        assert _parse_float("abc") is None

    def test_zero(self):
        assert _parse_float("0") == 0.0


# =============================================================================
# parse_t51sb02_html
# =============================================================================

def _row_html(*cells: str) -> str:
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return f"<tr>{tds}</tr>"


def _table_html(rows: list[str]) -> str:
    body = "".join(rows)
    return f'<html><body><table class="hasBorder">{body}</table></body></html>'


class TestParseT51sb02Html:
    def test_empty_html_returns_empty_dict(self):
        assert parse_t51sb02_html("") == {}

    def test_no_hasborder_table_returns_empty_dict(self):
        html = "<html><body><table><tr><td>2330</td></tr></table></body></html>"
        assert parse_t51sb02_html(html) == {}

    def test_full_row_parses(self):
        cells = [
            "2330", "台積電",
            "23.51", "35.20", "230.99", "180.10", "1234.5",
            "12.30", "29.67", "8.20", "44.51",
            "1.50", "0.42",
            "22.45", "26.66", "125.30", "38.90",
            "39.20",
            "150.20", "1234.5", "56.78",
        ]
        html = _table_html([_row_html(*cells)])
        result = parse_t51sb02_html(html)
        assert "2330" in result
        row = result["2330"]
        assert row["stock_id"] == "2330"
        assert row["company_name"] == "台積電"
        assert row["debt_ratio"] == 23.51
        assert row["current_ratio"] == 230.99
        assert row["quick_ratio"] == 180.10
        assert row["interest_coverage"] == 1234.5
        assert row["ar_turnover"] == 12.30
        assert row["avg_collection_days"] == 29.67
        assert row["inventory_turnover"] == 8.20
        assert row["fixed_asset_turnover"] == 1.50
        assert row["total_asset_turnover"] == 0.42
        assert row["roa"] == 22.45
        assert row["roe"] == 26.66
        assert row["net_profit_margin"] == 38.90
        assert row["eps"] == 39.20
        assert row["cash_reinvestment_ratio"] == 56.78

    def test_skips_header_row_with_nondigit_stock_id(self):
        header = _row_html(
            "公司代號", "公司名稱",
            *["負債比率"] * 19,
        )
        cells = ["2330", "台積電"] + ["1.0"] * 19
        html = _table_html([header, _row_html(*cells)])
        result = parse_t51sb02_html(html)
        assert list(result.keys()) == ["2330"]

    def test_skips_rows_with_too_few_columns(self):
        short = _row_html("2330", "台積電", "1.0")  # 只有 3 欄
        cells = ["1101", "台泥"] + ["2.0"] * 19
        html = _table_html([short, _row_html(*cells)])
        result = parse_t51sb02_html(html)
        assert "2330" not in result
        assert "1101" in result

    def test_financial_industry_na_columns_become_none(self):
        """金融業 current_ratio / quick_ratio / inventory_turnover 常為空白"""
        cells = [
            "2882", "國泰金",
            "89.50",  # debt_ratio
            "45.20",  # lt_fund_to_ppe_ratio
            "",       # current_ratio (金融業空白)
            "",       # quick_ratio (金融業空白)
            "N/A",    # interest_coverage
            "1.5",    # ar_turnover
            "240.0",  # avg_collection_days
            "－",     # inventory_turnover (全形破折)
            "不適用", # avg_sales_days
            "5.6",    # fixed_asset_turnover
            "0.05",   # total_asset_turnover
            "0.85",   # roa
            "8.5",    # roe
            "125.0",  # pretax_profit_to_capital_ratio
            "12.5",   # net_profit_margin
            "3.2",    # eps
            "10.5",   # cash_flow_ratio
            "80.0",   # cash_flow_adequacy_ratio
            "5.2",    # cash_reinvestment_ratio
        ]
        html = _table_html([_row_html(*cells)])
        result = parse_t51sb02_html(html)
        assert "2882" in result
        row = result["2882"]
        assert row["debt_ratio"] == 89.50
        assert row["current_ratio"] is None
        assert row["quick_ratio"] is None
        assert row["interest_coverage"] is None
        assert row["inventory_turnover"] is None
        assert row["avg_sales_days"] is None
        assert row["roe"] == 8.5
        assert row["eps"] == 3.2

    def test_multiple_rows(self):
        r1 = _row_html("2330", "台積電", *["1.0"] * 19)
        r2 = _row_html("2454", "聯發科", *["2.0"] * 19)
        r3 = _row_html("1101", "台泥", *["3.0"] * 19)
        html = _table_html([r1, r2, r3])
        result = parse_t51sb02_html(html)
        assert set(result.keys()) == {"2330", "2454", "1101"}
        assert result["2454"]["roa"] == 2.0
        assert result["1101"]["roe"] == 3.0

    def test_comma_number_in_interest_coverage(self):
        cells = ["2330", "台積電", "1.0", "1.0", "1.0", "1.0",
                 "1,234.56"] + ["1.0"] * 14
        html = _table_html([_row_html(*cells)])
        result = parse_t51sb02_html(html)
        assert result["2330"]["interest_coverage"] == 1234.56


# =============================================================================
# get_annual_financial_analysis (async, monkeypatched fetcher)
# =============================================================================

def _make_row(stock_id: str, name: str, **overrides: Any) -> dict:
    row: dict[str, Any] = {"stock_id": stock_id, "company_name": name}
    for c in mfa._COLUMNS[2:]:
        row[c] = 1.0
    row.update(overrides)
    return row


class TestGetAnnualFinancialAnalysis:
    """monkeypatch `_fetch_mops_t51sb02` 測所有 branch。"""

    @pytest.fixture(autouse=True)
    def _patch_fetcher(self, monkeypatch, tmp_path):
        """設 cache dir 為 tmp，避免污染真實 /tmp/valuation_cache"""
        monkeypatch.setattr(mfa, "CACHE_ROOT", tmp_path / "mops_t51sb02")

        self._sii_payloads: dict[int, dict] = {}
        self._otc_payloads: dict[int, dict] = {}

        async def _fake_fetch(market: str, year_tw: int) -> dict:
            if market == "sii":
                return self._sii_payloads.get(year_tw, {})
            if market == "otc":
                return self._otc_payloads.get(year_tw, {})
            return {}

        monkeypatch.setattr(mfa, "_fetch_mops_t51sb02", _fake_fetch)

    @pytest.mark.asyncio
    async def test_found_on_sii(self):
        self._sii_payloads[112] = {
            "2330": _make_row("2330", "台積電", roe=26.66, eps=39.20),
        }
        result = await mfa.get_annual_financial_analysis("2330", 2023)
        assert result["found"] is True
        assert result["year"] == 2023
        assert result["stock_id"] == "2330"
        assert result["reason"] is None
        assert result["data"]["market"] == "sii"
        assert result["data"]["stock_id"] == "2330"
        assert result["data"]["company_name"] == "台積電"
        assert result["data"]["roe"] == 26.66
        assert result["data"]["eps"] == 39.20

    @pytest.mark.asyncio
    async def test_fallback_to_otc(self):
        """sii 找不到 → 試 otc"""
        self._sii_payloads[112] = {"2330": _make_row("2330", "台積電")}
        self._otc_payloads[112] = {
            "3008": _make_row("3008", "大立光", roe=15.5),
        }
        result = await mfa.get_annual_financial_analysis("3008", 2023)
        assert result["found"] is True
        assert result["data"]["market"] == "otc"
        assert result["data"]["roe"] == 15.5

    @pytest.mark.asyncio
    async def test_stock_id_not_listed_in_either_market(self):
        self._sii_payloads[112] = {"2330": _make_row("2330", "台積電")}
        self._otc_payloads[112] = {"3008": _make_row("3008", "大立光")}
        result = await mfa.get_annual_financial_analysis("9999", 2023)
        assert result["found"] is False
        assert result["reason"] == "stock_id_not_listed"
        assert result["data"] is None
        assert result["stock_id"] == "9999"

    @pytest.mark.asyncio
    async def test_no_market_data_when_both_empty(self):
        """sii 與 otc 都空 payload → no_market_data"""
        # 不塞任何 year_tw=112 payload
        result = await mfa.get_annual_financial_analysis("2330", 2023)
        assert result["found"] is False
        assert result["reason"] == "no_market_data"
        assert result["data"] is None

    @pytest.mark.asyncio
    async def test_stock_id_trimmed(self):
        self._sii_payloads[112] = {"2330": _make_row("2330", "台積電")}
        result = await mfa.get_annual_financial_analysis("  2330  ", 2023)
        assert result["found"] is True
        assert result["stock_id"] == "2330"

    @pytest.mark.asyncio
    async def test_year_conversion_western_to_minguo(self):
        """西元 2023 → 民國 112"""
        self._sii_payloads[112] = {"2330": _make_row("2330", "台積電")}
        result = await mfa.get_annual_financial_analysis("2330", 2023)
        assert result["found"] is True
        # 民國 111 = 西元 2022，不該找得到
        self._sii_payloads[111] = {"2454": _make_row("2454", "聯發科")}
        result_2022 = await mfa.get_annual_financial_analysis("2454", 2022)
        assert result_2022["found"] is True
        assert result_2022["year"] == 2022

    @pytest.mark.asyncio
    async def test_source_field_present(self):
        self._sii_payloads[112] = {"2330": _make_row("2330", "台積電")}
        result = await mfa.get_annual_financial_analysis("2330", 2023)
        assert "MOPS t51sb02" in result["source"]

    @pytest.mark.asyncio
    async def test_all_data_fields_present_in_response(self):
        """完整資料列時 data dict 有所有 19 個數值欄位"""
        self._sii_payloads[112] = {
            "2330": _make_row(
                "2330", "台積電",
                debt_ratio=23.51, current_ratio=230.99,
                roa=22.45, roe=26.66, eps=39.20,
            ),
        }
        result = await mfa.get_annual_financial_analysis("2330", 2023)
        assert result["found"] is True
        data = result["data"]
        for col in mfa._NUMERIC_COLUMNS:
            assert col in data, f"missing column {col}"

    @pytest.mark.asyncio
    async def test_sii_hit_does_not_query_otc(self):
        """sii 找到就不要再打 otc（減少上游 request）"""
        self._sii_payloads[112] = {"2330": _make_row("2330", "台積電")}
        # otc 塞不同 payload 用來偵測有沒有被呼叫
        otc_called = {"count": 0}
        original_fetch = mfa._fetch_mops_t51sb02

        async def _counting_fetch(market: str, year_tw: int) -> dict:
            if market == "otc":
                otc_called["count"] += 1
            return await original_fetch(market, year_tw)

        mfa._fetch_mops_t51sb02 = _counting_fetch  # type: ignore[assignment]
        try:
            await mfa.get_annual_financial_analysis("2330", 2023)
        finally:
            mfa._fetch_mops_t51sb02 = original_fetch  # type: ignore[assignment]
        assert otc_called["count"] == 0


# =============================================================================
# 磁碟 cache 行為
# =============================================================================

class TestCacheRoundTrip:
    def test_cache_write_then_read(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mfa, "CACHE_ROOT", tmp_path / "mops_t51sb02")
        payload = {
            "2330": {"stock_id": "2330", "company_name": "台積電", "roe": 26.66},
        }
        mfa._cache_write("sii", 112, payload)
        cached = mfa._cache_read("sii", 112)
        assert cached == payload

    def test_cache_read_missing_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mfa, "CACHE_ROOT", tmp_path / "mops_t51sb02")
        assert mfa._cache_read("sii", 999) is None

    def test_cache_read_corrupt_returns_none_and_removes(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(mfa, "CACHE_ROOT", tmp_path / "mops_t51sb02")
        # 直接寫壞掉的 gzip
        p = mfa._cache_path("sii", 111)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"not a gzip file")
        assert mfa._cache_read("sii", 111) is None
        assert not p.exists()
