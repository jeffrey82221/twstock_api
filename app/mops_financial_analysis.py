"""MOPS 財務分析彙整表資料源（`t51sb02`）。

提供 `get_annual_financial_analysis(stock_id, year)` — 從公開資訊觀測站
「財務分析資料」（t51sb02）年報彙整表抓取單一上市 / 上櫃公司的年度財務比率。

上游 endpoint（每次回全市場所有公司當年度資料；沒有單股 filter 也沒有季報）：

    POST https://mopsov.twse.com.tw/mops/web/t51sb02

**網域說明**：自 2025-02-23 起 MOPS 新網域為 `mops.twse.com.tw`（SPA），
舊 REST endpoint 保留於 `mopsov.twse.com.tw`（原網站網址）。
    (application/x-www-form-urlencoded)

    Form fields:
      encodeURIComponent=1 run=Y step=1 firstin=1 off=1 ifrs=Y
      TYPEK  ∈ {sii, otc, rotc}   (上市 / 上櫃 / 興櫃)
      year   = 民國年 (=西元年 - 1911; IFRS 後 >= 民國 101 / 2012)

回傳 HTML `<table class="hasBorder">`，欄位順序（21 欄）：

    0  stock_id                          11 fixed_asset_turnover
    1  company_name                       12 total_asset_turnover
    2  debt_ratio                         13 roa
    3  lt_fund_to_ppe_ratio               14 roe
    4  current_ratio                      15 pretax_profit_to_capital_ratio
    5  quick_ratio                        16 net_profit_margin
    6  interest_coverage                  17 eps
    7  ar_turnover                        18 cash_flow_ratio
    8  avg_collection_days                19 cash_flow_adequacy_ratio
    9  inventory_turnover                 20 cash_reinvestment_ratio
    10 avg_sales_days

金融 / 保險 / 金控業：流動比率 / 速動比率 / 存貨週轉率 / 平均售貨日數 等
不適用欄位可能空白，統一以 `_parse_float` 解為 None。

**磁碟 cache**：`/tmp/valuation_cache/mops_t51sb02/{market}_{year_tw}.json.gz`
（gzip JSON, atomic 寫 tmp then rename）。key = (market, 民國年)。
以整批市場資料為 cache 粒度（一次抓 ~1700 檔，適合放檔案），同一年重複查
不同 stock_id 只會打上游一次。

**Fallback 策略**：`get_annual_financial_analysis` 先試 `sii`（上市），
找不到 stock_id 時再試 `otc`（上櫃）。客戶端不需知道股票是上市或上櫃。

**資料起始年**：民國 101 (2012) — IFRSs 導入首年。早於此回 400。

**Endpoint 官方頁面**：https://mops.twse.com.tw/mops/web/t05st22
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
from pathlib import Path
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from .sources import (
    SourceError,
    _classify_http_exc,
    _record_source_error,
)

logger = logging.getLogger("twstock_api.mops_financial_analysis")

CACHE_ROOT = Path("/tmp/valuation_cache") / "mops_t51sb02"

# 2025-02-23 後 mops.twse.com.tw 改為 SPA，舊 REST endpoint 保留在 mopsov.twse.com.tw
MOPS_T51SB02_URL = "https://mopsov.twse.com.tw/mops/web/t51sb02"

# IFRSs 導入首年 = 民國 101 = 西元 2012
FA_MIN_YEAR = 2012

# 上市 → 上櫃 fallback（找不到 stock_id 才會試第二個）
MARKETS_FALLBACK_ORDER = ("sii", "otc")

# 欄位順序（21 欄，t51sb02 HTML table 由左至右）
_COLUMNS = (
    "stock_id",
    "company_name",
    "debt_ratio",
    "lt_fund_to_ppe_ratio",
    "current_ratio",
    "quick_ratio",
    "interest_coverage",
    "ar_turnover",
    "avg_collection_days",
    "inventory_turnover",
    "avg_sales_days",
    "fixed_asset_turnover",
    "total_asset_turnover",
    "roa",
    "roe",
    "pretax_profit_to_capital_ratio",
    "net_profit_margin",
    "eps",
    "cash_flow_ratio",
    "cash_flow_adequacy_ratio",
    "cash_reinvestment_ratio",
)

_NUMERIC_COLUMNS = tuple(c for c in _COLUMNS if c not in ("stock_id", "company_name"))


# =============================================================================
# 磁碟 cache
# =============================================================================

def _cache_path(market: str, year_tw: int) -> Path:
    return CACHE_ROOT / f"{market}_{year_tw}.json.gz"


def _cache_read(market: str, year_tw: int) -> Optional[dict]:
    p = _cache_path(market, year_tw)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("mops fa cache read failed %s: %s (discarding)", p, e)
        try:
            p.unlink()
        except Exception:
            pass
        return None


def _cache_write(market: str, year_tw: int, payload: dict) -> None:
    p = _cache_path(market, year_tw)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        tmp.replace(p)
    except Exception as e:
        logger.warning("mops fa cache write failed %s: %s", p, e)


# =============================================================================
# 欄位工具
# =============================================================================

def _parse_float(s: Any) -> Optional[float]:
    """t51sb02 儲存格轉 float；空白 / N/A / 全形破折 / 不適用 一律回 None。"""
    if s is None:
        return None
    txt = str(s).replace("\u3000", "").replace(",", "").strip()
    if txt in ("", "-", "--", "—", "－", "N/A", "n/a", "NA", "不適用"):
        return None
    try:
        return float(txt)
    except Exception:
        return None


def parse_t51sb02_html(html: str) -> dict[str, dict]:
    """解析 t51sb02 HTML → stk_code -> row dict（純函式，便於測試）。

    找 class="hasBorder" 的 table，逐 <tr> 抓 <td>，欄位數 >= 18 才收
    （避開 header / summary / empty rows）。
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table", {"class": "hasBorder"})
    if not tables:
        return {}

    result: dict[str, dict] = {}
    for table in tables:
        for tr in table.find_all("tr"):
            cells = [
                td.get_text(strip=True).replace("\u3000", "")
                for td in tr.find_all("td")
            ]
            if len(cells) < 18:
                continue
            stock_id = cells[0].strip()
            # 略過 header / 合計列 / 空白代號
            if not stock_id or not stock_id[0].isdigit():
                continue
            row: dict[str, Any] = {}
            for idx, col in enumerate(_COLUMNS):
                if idx >= len(cells):
                    row[col] = None
                    continue
                raw = cells[idx]
                if col in ("stock_id", "company_name"):
                    row[col] = raw
                else:
                    row[col] = _parse_float(raw)
            result[stock_id] = row
    return result


# =============================================================================
# 上游抓取（純函式，取代 requests 為 httpx.AsyncClient，配合既有專案風格）
# =============================================================================

async def _fetch_mops_t51sb02(market: str, year_tw: int) -> dict[str, dict]:
    """回傳 stk_code -> row dict（該市場該民國年的全上市 / 上櫃資料）。

    cache miss 時打上游、寫磁碟 cache；上游解析失敗 / 空 payload 回 {} 也 cache
    （避免同一年反覆重打）。
    """
    if market not in ("sii", "otc", "rotc"):
        raise ValueError(f"invalid market: {market!r}")

    cached = _cache_read(market, year_tw)
    if cached is not None:
        return cached

    form_data = {
        "encodeURIComponent": "1",
        "run": "Y",
        "step": "1",
        "TYPEK": market,
        "year": str(year_tw),
        "firstin": "1",
        "off": "1",
        "ifrs": "Y",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://mopsov.twse.com.tw/mops/web/t05st22",
    }

    html: Optional[str] = None
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True, headers=headers,
            ) as client:
                r = await client.post(MOPS_T51SB02_URL, data=form_data)
                r.raise_for_status()
                r.encoding = "utf-8"
                html = r.text
            break
        except Exception as e:
            last_exc = e
            if attempt < 2:
                # 上游有 rate limit / 5xx 建議加大 backoff
                await asyncio.sleep(1.0 * (attempt + 1))
    if html is None:
        assert last_exc is not None
        status, msg, rl = _classify_http_exc(last_exc, MOPS_T51SB02_URL)
        _record_source_error(SourceError(
            source="MOPS", url=MOPS_T51SB02_URL,
            status_code=status, message=msg, is_rate_limited=rl,
        ))
        # 不 cache 網路失敗（下次重試）
        return {}

    parsed = parse_t51sb02_html(html)
    _cache_write(market, year_tw, parsed)
    return parsed


# =============================================================================
# 對外主函式
# =============================================================================

async def get_annual_financial_analysis(
    stock_id: str,
    year: int,
) -> dict[str, Any]:
    """取得單一上市 / 上櫃公司於某西元年的年度財務比率。

    Args:
        stock_id: 股票代號（例 "2330"）
        year: 西元年（例 2023）

    Returns:
        dict，欄位參見 `FinancialAnalysisResponse`。行為與其他 valuation
        endpoint 一致：找不到時 `found=False` + `reason` 標籤，交由 route 層決定
        HTTP status。

    Reason enum:
        - `no_market_data`：整個市場當年度 payload 為空（上游未申報 / 網路失敗）
        - `stock_id_not_listed`：sii + otc 都找不到該代號
    """
    year_tw = year - 1911
    stk = str(stock_id).strip()

    # 依序試 sii → otc
    matched_market: Optional[str] = None
    matched_row: Optional[dict] = None
    all_empty = True
    for market in MARKETS_FALLBACK_ORDER:
        payload = await _fetch_mops_t51sb02(market, year_tw)
        if payload:
            all_empty = False
        if stk in payload:
            matched_market = market
            matched_row = payload[stk]
            break

    if matched_row is None:
        if all_empty:
            return {
                "found": False,
                "year": year,
                "stock_id": stk,
                "reason": "no_market_data",
                "data": None,
                "source": (
                    "MOPS t51sb02 (https://mops.twse.com.tw/mops/web/t51sb02)"
                ),
            }
        return {
            "found": False,
            "year": year,
            "stock_id": stk,
            "reason": "stock_id_not_listed",
            "data": None,
            "source": (
                "MOPS t51sb02 (https://mops.twse.com.tw/mops/web/t51sb02)"
            ),
        }

    # 組回應（欄位順序固定，方便前端渲染）
    data: dict[str, Any] = {
        "stock_id": matched_row.get("stock_id", stk),
        "company_name": matched_row.get("company_name"),
        "year": year,
        "market": matched_market,
    }
    for col in _NUMERIC_COLUMNS:
        data[col] = matched_row.get(col)

    return {
        "found": True,
        "year": year,
        "stock_id": stk,
        "reason": None,
        "data": data,
        "source": (
            "MOPS t51sb02 (https://mops.twse.com.tw/mops/web/t51sb02)"
        ),
    }
