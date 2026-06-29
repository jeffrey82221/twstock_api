"""yfinance 來源：取得台股季財報（EPS / 淨利 / 營業利潤率 衍生用）。

設計理念
========
- yfinance 對台股代號需加 `.TW`（上市）或 `.TWO`（上櫃）後綴。
- `Ticker.quarterly_financials` 在台股回傳的數值是「單季值」（與 FinMind 同結構），
  以 sandbox 驗證 2330.TW 進行比對 FinMind 單季數值誤差 < 1%，不需差分還原。
- 輸出 rows 結構與 FinMind 一致：`{"date": "YYYY-MM-DD", "type": <FinMind type>, "value": float}`，
  讓 service 層可重用 quarter_map + TTM 計算函式（零分支）。

來源限制
========
- yfinance 為非官方 wrapper，台股財務細目對應到 Yahoo Finance 標籤；偶有缺失（NaN）。
  本檔將 NaN 視為「該季此欄位缺失」直接略過，由 service 端用「不足 4 季回 None」保護。
- 缺乏 token / 速率限制：經驗值每小時 thousands of requests 沒問題；遠寬於 FinMind。
- yfinance 為同步 IO（HTTP + DataFrame）；用 `asyncio.to_thread` 包成 async 接口避免阻塞。

對應表（FinMind type → yfinance row）：
- EPS                  → "Basic EPS"
- IncomeAfterTaxes     → "Net Income"
- OperatingIncome      → "Operating Income"
- Revenue              → "Total Revenue"

股利股息（v0.0.8 新增）
========================
- yfinance `Ticker.dividends` 回傳 pandas Series，index 為「除息日」（tz-aware Timestamp），
  value 為「每股現金股利（元）」。`Ticker.actions` 額外多一欄 stock split，這裡只取 dividends。
- 台股一般一年配息一次（少數半年配/季配），故同一 year 可能有 1~4 個 row；轉成 FinMind 同形
  rows 後可直接餵 service._pick_dividend（共用既有挑選邏輯，零分支）。
- yfinance 不提供「股票股利」「股利公告日」「現金股利發放日」 → 對應欄位填 None；其餘日期一律
  以「除息日」回填 `CashExDividendTradingDate` / `date`。
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Optional

# yfinance quarterly_financials 在台股 row label 台股對應表：FinMind type → yfinance label

from cachetools import TTLCache

from .sources import SourceError, _record_source_error

logger = logging.getLogger("twstock_api.yfinance")

# 1 小時 TTL；單 process 內最多 256 個股票快取
_yf_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)

# FinMind type → yfinance row label 對應表
_TYPE_MAP = {
    "EPS": "Basic EPS",
    "IncomeAfterTaxes": "Net Income",
    "OperatingIncome": "Operating Income",
    "Revenue": "Total Revenue",
}


def _ticker_symbol(stock_id: str, market: Optional[str]) -> str:
    """根據市場別補對應 yfinance 後綴。"""
    sid = stock_id.strip()
    if market == "上櫃":
        return f"{sid}.TWO"
    # 上市或不明時預設 .TW
    return f"{sid}.TW"


def _filter_valid_values(values_by_date: dict[str, Any]) -> dict[str, float]:
    """送 NaN / None 跳過、全部轉為 float。"""
    out: dict[str, float] = {}
    for d, v in values_by_date.items():
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        try:
            out[d] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _fetch_yfinance_sync(ticker_sym: str) -> list[dict[str, Any]]:
    """同步取 yfinance quarterly_financials 並轉為 FinMind-style rows。

    回傳 list of {"date", "type", "value"}。
    """
    import yfinance as yf

    t = yf.Ticker(ticker_sym)
    qf = t.quarterly_financials
    if qf is None or qf.empty:
        return []

    rows: list[dict[str, Any]] = []
    for fm_type, yf_label in _TYPE_MAP.items():
        if yf_label not in qf.index:
            continue
        series = qf.loc[yf_label]  # columns are timestamps
        values_by_date: dict[str, Any] = {}
        for ts, v in series.items():
            d = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            values_by_date[d] = v
        # yfinance 台股 quarterly_financials 已是單季值，不需差分；只送 NaN / None
        clean = _filter_valid_values(values_by_date)
        for d, v in clean.items():
            rows.append({"date": d, "type": fm_type, "value": v})
    return rows


async def get_financial_statements_yf(stock_id: str, market: Optional[str]) -> list[dict[str, Any]]:
    """取 yfinance 季財報（已差分為單季），用法相容 sources.get_financial_statements。

    回傳 row 結構：`[{"date": "YYYY-MM-DD", "type": "EPS|IncomeAfterTaxes|OperatingIncome|Revenue", "value": float}]`
    錯誤情境：寫入 SourceError buffer 並回 []，由 service 端 `_ttm_value` 用「不足 4 季回 None」保護。
    """
    ticker_sym = _ticker_symbol(stock_id, market)
    cache_key = ticker_sym
    cached = _yf_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        rows = await asyncio.to_thread(_fetch_yfinance_sync, ticker_sym)
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning(
            "[yfinance-error] symbol=%s :: %s", ticker_sym, msg,
        )
        _record_source_error(
            SourceError(
                source="yfinance",
                url=f"yfinance://Ticker/{ticker_sym}/quarterly_financials",
                status_code=None,
                message=msg,
            )
        )
        return []

    if not rows:
        # 拿不到任何 row：通常代表 ticker 在 Yahoo 上查無，記錄一筆警告但不視為 rate-limit
        _record_source_error(
            SourceError(
                source="yfinance",
                url=f"yfinance://Ticker/{ticker_sym}/quarterly_financials",
                status_code=None,
                message=f"empty quarterly_financials for {ticker_sym}",
            )
        )
        # 仍把空 list 寫入快取，避免短時間重複打
        _yf_cache[cache_key] = rows
        return rows

    _yf_cache[cache_key] = rows
    return rows


# =====================================================================
# v0.0.8: yfinance 股利（Ticker.dividends）
# =====================================================================
# 與 FinMind TaiwanStockDividend rows 同欄位結構，使 service._pick_dividend 可直接共用。
# 1 小時 TTL；單 process 內最多 256 個 ticker 快取。
_yf_dividend_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)


def _fetch_yfinance_dividends_sync(ticker_sym: str) -> list[dict[str, Any]]:
    """同步取 yfinance `Ticker.dividends` 並轉為 FinMind TaiwanStockDividend-style rows。

    回傳 rows 結構（與 FinMind TaiwanStockDividend 同欄位，便於共用 _pick_dividend）：
    - date: 除息日（YYYY-MM-DD，以 yfinance index 為準）
    - stock_id: 純股票代號（去掉 .TW/.TWO 後綴）
    - year: 除息日的西元年
    - CashEarningsDistribution: 每股現金股利（元）
    - CashStatutorySurplus: 0（yfinance 不分項，全部歸入 CashEarningsDistribution）
    - StockEarningsDistribution: 0（yfinance 不提供股票股利）
    - StockStatutorySurplus: 0
    - CashExDividendTradingDate: 除息日（YYYY-MM-DD）
    - StockExDividendTradingDate: None
    - CashDividendPaymentDate: None（yfinance 不提供發放日）
    - AnnouncementDate: None（yfinance 不提供公告日）
    """
    import yfinance as yf

    t = yf.Ticker(ticker_sym)
    div = t.dividends
    if div is None or div.empty:
        return []

    # 純股票代號（去 .TW / .TWO）— 保持與 FinMind data_id 一致
    stock_id = ticker_sym.rsplit(".", 1)[0]

    rows: list[dict[str, Any]] = []
    for ts, v in div.items():
        # ts 是 pandas Timestamp（通常 tz-aware）；只取日期部分
        if hasattr(ts, "strftime"):
            d = ts.strftime("%Y-%m-%d")
            year = ts.year
        else:
            d = str(ts)[:10]
            try:
                year = int(d[:4])
            except ValueError:
                continue
        # 過濾 NaN / None
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        try:
            cash = float(v)
        except (TypeError, ValueError):
            continue
        rows.append({
            "date": d,
            "stock_id": stock_id,
            "year": year,
            "CashEarningsDistribution": cash,
            "CashStatutorySurplus": 0,
            "StockEarningsDistribution": 0,
            "StockStatutorySurplus": 0,
            "CashExDividendTradingDate": d,
            "StockExDividendTradingDate": None,
            "CashDividendPaymentDate": None,
            "AnnouncementDate": None,
        })
    return rows


async def get_dividend_yf(
    stock_id: str,
    market: Optional[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict[str, Any]]:
    """取 yfinance 股利歷史，rows 結構與 FinMind TaiwanStockDividend 一致。

    參數:
        stock_id: 股票代號（不含後綴）。
        market: "上市" 或 "上櫃"；決定 .TW / .TWO 後綴。
        start_date / end_date: "YYYY-MM-DD"。若提供，於回傳前依 `CashExDividendTradingDate`
            過濾，使與 FinMind endpoint 同樣支援「5 年區間 + as_of」呼叫慣例。

    錯誤情境：寫入 SourceError buffer 並回 []，由 service._pick_dividend 端用「無候選回 None」保護。
    """
    ticker_sym = _ticker_symbol(stock_id, market)
    cache_key = ticker_sym
    cached = _yf_dividend_cache.get(cache_key)
    if cached is None:
        try:
            rows = await asyncio.to_thread(_fetch_yfinance_dividends_sync, ticker_sym)
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            logger.warning(
                "[yfinance-dividend-error] symbol=%s :: %s", ticker_sym, msg,
            )
            _record_source_error(
                SourceError(
                    source="yfinance",
                    url=f"yfinance://Ticker/{ticker_sym}/dividends",
                    status_code=None,
                    message=msg,
                )
            )
            return []

        if not rows:
            _record_source_error(
                SourceError(
                    source="yfinance",
                    url=f"yfinance://Ticker/{ticker_sym}/dividends",
                    status_code=None,
                    message=f"empty dividends for {ticker_sym}",
                )
            )
        # 全量寫入快取（空 list 也寫入，避免短時間重打）
        _yf_dividend_cache[cache_key] = rows
        cached = rows

    # 依日期區間過濾（與 FinMind 呼叫慣例一致；快取仍持有全量）
    if not start_date and not end_date:
        return cached
    out: list[dict[str, Any]] = []
    for r in cached:
        d = r.get("CashExDividendTradingDate") or r.get("date") or ""
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            continue
        out.append(r)
    return out
